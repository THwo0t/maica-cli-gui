# -*- coding: utf-8 -*-
"""Persistent GUI worker that owns MaicaEngine on a background thread."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot


ROOT_DIR = Path(__file__).resolve().parents[1]
CLI_DIR = ROOT_DIR / 'maica cli'

if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from embedding_index import build_memory_vector_index, prewarm_embedding_model  # noqa: E402
from engine import MaicaEngine  # noqa: E402
from mfocus import status_summary  # noqa: E402
from embedding_service_client import build_service_memory_index  # noqa: E402
from config_io import save_json  # noqa: E402
from data_portability import export_user_data, import_user_data, preview_user_data  # noqa: E402
from text_utils import redact_secret  # noqa: E402


class GuiEngineWorker(QObject):
    ready = Signal(dict)
    config_ready = Signal(dict)
    status = Signal(str)
    finished = Signal(dict)
    stream_started = Signal(dict)
    stream_chunk = Signal(str)
    data_ready = Signal(dict)
    pet_action = Signal(str, str)  # (action, arg) for the desktop pet body

    def __init__(
        self,
        config_path: str | Path | None = None,
        db_path: str | Path | None = None,
        app_dir: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.engine: MaicaEngine | None = None
        self.embedding_service_process: subprocess.Popen[Any] | None = None
        self.config_path = Path(config_path).resolve() if config_path else None
        self.db_path = Path(db_path).resolve() if db_path else None
        self.app_dir = Path(app_dir).resolve() if app_dir else CLI_DIR

    def _safe_error_text(self, exc: Exception | str, with_traceback: bool = False) -> str:
        config = self.engine.config if self.engine is not None else {}
        text = str(exc)
        if with_traceback:
            text = f'{text}\n{traceback.format_exc()}'
        return redact_secret(
            text,
            config.get('api_key', ''),
            config.get('tts_bailian_api_key', ''),
            config.get('stt_bailian_api_key', ''),
        )

    @Slot()
    def initialize(self) -> None:
        try:
            self.engine = MaicaEngine(
                config_path=self.config_path,
                db_path=self.db_path,
                app_dir=self.app_dir,
            )
            self._apply_gui_safety_overrides()
            self._register_pet_body_tools()
            self._sync_embedding_service()
            self.config_ready.emit(dict(self.engine.config))
            self.ready.emit({'ok': True, 'error': ''})
            self._prewarm_embeddings_if_needed()
        except Exception as exc:
            self.ready.emit({'ok': False, 'error': self._safe_error_text(exc, with_traceback=True)})

    def _register_pet_body_tools(self) -> None:
        # Body tools live on the GUI's single engine; their run() (called in
        # this worker thread during the agent loop) emits pet_action, which the
        # GUI applies to the hosted pet on the UI thread. Harmless no-op when no
        # pet is open.
        if self.engine is None:
            return
        empty = {'type': 'object', 'properties': {}}

        def reg(name: str, desc: str, params: dict[str, Any], action: str, arg_key: str | None) -> None:
            def run(args: dict[str, Any], _action: str = action, _key: str = arg_key) -> dict[str, Any]:
                self.pet_action.emit(_action, str(args.get(_key) or '') if _key else '')
                return {'ok': True}
            self.engine.register_tool(name, {'type': 'function', 'function': {'name': name, 'description': desc, 'parameters': params}}, run)

        reg('set_expression', 'Change your facial expression on the desktop pet to show how you feel.',
            {'type': 'object', 'properties': {'expression': {'type': 'string'}}, 'required': ['expression']},
            'expression', 'expression')
        reg('do_gesture', 'Play a small body gesture on the desktop pet (wave, jump, pout, nod).',
            {'type': 'object', 'properties': {'gesture': {'type': 'string'}}, 'required': ['gesture']},
            'gesture', 'gesture')
        reg('pop_to_front', "Bring your desktop pet to the front to get the user's attention.", empty, 'pop', None)
        reg('nudge', 'Give a small playful bounce on the desktop pet.', empty, 'nudge', None)

    def _apply_gui_safety_overrides(self) -> None:
        if self.engine is None:
            return
        config = self.engine.config
        if not config.get('gui_disable_thread_embeddings', True):
            return
        config['gui_prewarm_embeddings'] = False
        if config.get('embedding_service_enabled', False):
            self.status.emit('Embedding service mode is enabled; GUI will not load vectors in-process.')
            return
        disabled = []
        for key in ('embedding_enabled', 'memory_embedding_enabled'):
            if config.get(key):
                config[key] = False
                disabled.append(key)
        if disabled:
            self.status.emit(
                'GUI thread vector retrieval is disabled for stability. CLI can still use local vectors.'
            )

    def _embedding_service_url(self) -> str:
        config = self.engine.config if self.engine is not None else {}
        host = str(config.get('embedding_service_host') or '127.0.0.1').strip() or '127.0.0.1'
        port = int(config.get('embedding_service_port') or 8766)
        return f'http://{host}:{port}'

    def _embedding_service_health(self, timeout: float = 0.6) -> bool:
        try:
            request = urllib.request.Request(self._embedding_service_url() + '/health', method='GET')
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode('utf-8', errors='replace'))
            return bool(data.get('ok'))
        except Exception:
            return False

    def _sync_embedding_service(self) -> None:
        if self.engine is None:
            return
        config = self.engine.config
        wants_service = bool(config.get('embedding_service_enabled', False))
        wants_vectors = bool(config.get('embedding_enabled') or config.get('memory_embedding_enabled'))
        autostart = bool(config.get('embedding_service_autostart', True))
        if not wants_service or not wants_vectors:
            self._stop_embedding_service()
            return
        if self._embedding_service_health():
            self.status.emit(f'Embedding service ready at {self._embedding_service_url()}')
            return
        if not autostart:
            self.status.emit('Embedding service is enabled but autostart is off.')
            return
        args = [
            *self._embedding_service_command(),
            '--host',
            str(config.get('embedding_service_host') or '127.0.0.1'),
            '--port',
            str(int(config.get('embedding_service_port') or 8766)),
            '--config',
            str(self.engine.config_path),
            '--db',
            str(self.engine.db_path),
        ]
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        try:
            self.embedding_service_process = subprocess.Popen(
                args,
                cwd=str(CLI_DIR),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
        except Exception as exc:
            self.status.emit(f'Embedding service failed to start: {self._safe_error_text(exc)}')
            return
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if self._embedding_service_health(timeout=0.4):
                self.status.emit(f'Embedding service started at {self._embedding_service_url()}')
                return
            time.sleep(0.2)
        self.status.emit('Embedding service was started, but health check is not ready yet.')

    def _embedding_service_command(self) -> list[str]:
        if getattr(sys, 'frozen', False):
            service_exe = Path(sys.executable).with_name('maica-embedding-service.exe')
            if service_exe.exists():
                return [str(service_exe)]
        return [sys.executable, str(CLI_DIR / 'embedding_service.py')]

    def _stop_embedding_service(self) -> None:
        process = self.embedding_service_process
        self.embedding_service_process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _prewarm_embeddings_if_needed(self) -> None:
        if self.engine is None:
            return
        config = self.engine.config
        if not config.get('gui_prewarm_embeddings', False):
            return
        if not (config.get('embedding_enabled') or config.get('memory_embedding_enabled')):
            return
        self.status.emit('Loading embedding model in the background...')
        report = prewarm_embedding_model(
            config,
            quiet=bool(config.get('gui_quiet_embedding_load', True)),
        )
        if report.get('ok'):
            dim = report.get('dimension') or '?'
            self.status.emit(f'Embedding model is ready: {dim}d')
        else:
            self.status.emit(f'Embedding model prewarm failed: {report.get("error")}')

    @Slot(str)
    def chat(self, text: str) -> None:
        self._run_request('chat', text)

    @Slot(str)
    def spire(self, hint: str) -> None:
        self._run_request('spire', hint)

    @Slot()
    def shutdown(self) -> None:
        self._stop_embedding_service()
        if self.engine is not None:
            self.engine.close()
            self.engine = None

    @Slot()
    def data_snapshot(self) -> None:
        self._run_data_action('snapshot')

    @Slot(str, str)
    def set_profile_value(self, key: str, value: str) -> None:
        self._run_data_action('set_profile_value', key=key, value=value)

    @Slot(str, str, int)
    def add_memory(self, text: str, tags: str, importance: int) -> None:
        self._run_data_action('add_memory', text=text, tags=tags, importance=importance)

    @Slot(int)
    def delete_memory(self, memory_id: int) -> None:
        self._run_data_action('delete_memory', memory_id=memory_id)

    @Slot(str, str, int)
    def add_fact(self, text: str, category: str, importance: int) -> None:
        self._run_data_action('add_fact', text=text, category=category, importance=importance)

    @Slot(int)
    def delete_fact(self, fact_id: int) -> None:
        self._run_data_action('delete_fact', fact_id=fact_id)

    @Slot(str)
    def export_debug(self, path: str) -> None:
        self._run_data_action('export_debug', path=path)

    @Slot(str)
    def export_user_data(self, path: str) -> None:
        self._run_data_action('export_user_data', path=path)

    @Slot(str, bool)
    def import_user_data(self, path: str, replace: bool) -> None:
        self._run_data_action('import_user_data', path=path, replace=replace)

    @Slot(str)
    def preview_import(self, path: str) -> None:
        self._run_data_action('preview_import', path=path)

    @Slot()
    def summarize_memory(self) -> None:
        self._run_data_action('summarize_memory')

    @Slot(dict)
    def save_config(self, updates: dict) -> None:
        self._run_data_action('save_config', updates=updates)

    def _run_request(self, mode: str, text: str) -> None:
        try:
            if self.engine is None:
                self._ensure_engine()
            stream_started = False

            def on_stream_chunk(chunk: str) -> None:
                nonlocal stream_started
                if not stream_started:
                    stream_started = True
                    self.stream_started.emit({'source': mode})
                self.stream_chunk.emit(chunk)

            if mode == 'spire':
                result = self.engine.spire(text, stream_callback=on_stream_chunk)
            else:
                result = self.engine.chat(text, stream_callback=on_stream_chunk)
            if stream_started:
                result['streamed'] = True
            self.finished.emit(result)
        except Exception as exc:
            self.finished.emit(
                {
                    'ok': False,
                    'source': mode,
                    'text': '',
                    'emotion': 'concerned',
                    'action': {},
                    'mtrigger_notices': [],
                    'debug': {},
                    'error': self._safe_error_text(exc, with_traceback=True),
                }
            )

    def _ensure_engine(self) -> MaicaEngine:
        if self.engine is None:
            self.engine = MaicaEngine(
                config_path=self.config_path,
                db_path=self.db_path,
                app_dir=self.app_dir,
            )
            self._apply_gui_safety_overrides()
            self._sync_embedding_service()
        return self.engine

    def _run_data_action(self, action: str, **kwargs: Any) -> None:
        try:
            engine = self._ensure_engine()
            notice = ''
            extra: dict[str, Any] = {}
            if action == 'set_profile_value':
                key = str(kwargs.get('key') or '').strip()
                value = str(kwargs.get('value') or '').strip()
                if key:
                    if key == 'affection':
                        engine.store.set_affection(float(value or 0))
                    elif key == 'nicknames':
                        nicknames = [item.strip() for item in value.split(',') if item.strip()]
                        engine.store.set_nicknames(nicknames)
                    else:
                        engine.store.set_profile_value(key, value)
                    notice = f'Updated profile: {key}'
            elif action == 'add_memory':
                text = str(kwargs.get('text') or '').strip()
                if text:
                    memory_id = engine.store.add_memory(
                        text,
                        str(kwargs.get('tags') or '').strip(),
                        int(kwargs.get('importance') or 1),
                    )
                    notice = f'Added memory #{memory_id}'
            elif action == 'delete_memory':
                memory_id = int(kwargs.get('memory_id') or 0)
                if memory_id and engine.store.delete_memory(memory_id):
                    notice = f'Deleted memory #{memory_id}'
            elif action == 'add_fact':
                text = str(kwargs.get('text') or '').strip()
                if text:
                    fact_id = engine.store.add_fact(
                        text,
                        str(kwargs.get('category') or 'custom').strip() or 'custom',
                        'gui',
                        int(kwargs.get('importance') or 2),
                    )
                    notice = f'Added fact #{fact_id}'
            elif action == 'delete_fact':
                fact_id = int(kwargs.get('fact_id') or 0)
                if fact_id and engine.store.delete_fact(fact_id):
                    notice = f'Deleted fact #{fact_id}'
            elif action == 'export_debug':
                target = Path(str(kwargs.get('path') or '')).expanduser()
                if target:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(self._debug_payload(engine), encoding='utf-8')
                    notice = f'Exported debug info: {target}'
            elif action == 'export_user_data':
                target = Path(str(kwargs.get('path') or '')).expanduser()
                if target:
                    result = export_user_data(engine.store, target)
                    notice = f'Exported user data: {result["path"]}'
            elif action == 'preview_import':
                target = Path(str(kwargs.get('path') or '')).expanduser()
                if target:
                    result = preview_user_data(target)
                    extra['import_preview'] = result
                    extra['import_path'] = str(target)
                    notice = 'Import preview ready.'
            elif action == 'import_user_data':
                target = Path(str(kwargs.get('path') or '')).expanduser()
                if target:
                    result = import_user_data(engine.store, target, bool(kwargs.get('replace', False)))
                    extra['import_result'] = result
                    notice = 'Imported user data: ' + json.dumps(result, ensure_ascii=False)
            elif action == 'summarize_memory':
                result = engine.summarize_recent_memory()
                notice = 'Memory summary: ' + json.dumps(result, ensure_ascii=False)
            elif action == 'save_config':
                updates = kwargs.get('updates') if isinstance(kwargs.get('updates'), dict) else {}
                applied = self._apply_config_updates(engine, updates)
                if applied:
                    save_json(engine.config_path, engine.config)
                    self._apply_gui_safety_overrides()
                    self._sync_embedding_service()
                    notice = f'Saved config: {", ".join(applied)}'

            vector_notice = self._auto_refresh_memory_vectors(engine)
            if vector_notice:
                notice = f'{notice} | {vector_notice}' if notice else vector_notice

            payload = self._data_snapshot(engine)
            payload.update(extra)
            payload.update({'ok': True, 'action': action, 'notice': notice, 'error': ''})
            self.data_ready.emit(payload)
        except Exception as exc:
            self.data_ready.emit(
                {
                    'ok': False,
                    'action': action,
                    'notice': '',
                    'error': self._safe_error_text(exc, with_traceback=True),
                }
            )

    def _auto_refresh_memory_vectors(self, engine: MaicaEngine) -> str:
        config = engine.config
        if not config.get('memory_vector_auto_rebuild', True):
            return ''
        if not config.get('memory_embedding_enabled', False):
            return ''
        if not engine.store.memory_vector_dirty():
            return ''
        if config.get('embedding_service_enabled', False):
            try:
                result = build_service_memory_index(config)
                return f'Memory vector index rebuilt by service ({result.get("count", 0)} memories).'
            except Exception as exc:
                return f'Memory vector rebuild pending; service failed: {self._safe_error_text(exc)}'
        if config.get('gui_disable_thread_embeddings', True):
            return 'Memory vector index marked dirty. Enable embedding service or rebuild from CLI.'
        try:
            result = build_memory_vector_index(engine.store, config)
            engine.store.clear_memory_vector_dirty()
            engine.store.add_event('memory_vector_rebuilt', {'count': result.get('count'), 'mode': 'gui_worker'})
            return f'Memory vector index rebuilt ({result.get("count", 0)} memories).'
        except Exception as exc:
            return f'Memory vector rebuild pending: {self._safe_error_text(exc)}'

    def _data_snapshot(self, engine: MaicaEngine) -> dict[str, Any]:
        profile = engine.store.get_profile()
        return {
            'profile': profile,
            'nicknames': engine.store.get_nicknames(),
            'memories': [dict(row) for row in engine.store.all_memories()],
            'facts': [dict(row) for row in engine.store.search_facts('', 200)],
            'events': [dict(row) for row in engine.store.recent_events(30)],
            'summaries': [dict(row) for row in engine.store.recent_summaries(20)],
            'translation_cache_count': engine.store.translation_cache_count(),
            'recent_messages': engine.store.recent_messages(int(engine.config.get('gui_load_recent_messages', 20))),
            'status': status_summary(engine.store, engine.config),
            'token_usage': engine.store.token_usage_summary(),
            'config': self._safe_config(engine.config),
        }

    def _safe_config(self, config: dict[str, Any]) -> dict[str, Any]:
        safe = {}
        secret_words = ('key', 'token', 'secret', 'password')
        public_keys = {
            'api_base',
            'model',
            'llm_call_mode',
            'agent_api_base',
            'agent_model',
            'agent_tools_enabled',
            'file_tools_enabled',
            'vision_enabled',
            'vision_model',
            'sandbox_root',
            'sandbox_readonly_allowlist',
            'language',
            'temperature',
            'top_p',
            'max_tokens',
            'frequency_penalty',
            'presence_penalty',
            'streaming_enabled',
            'response_output_mode',
            'metadata_extract_enabled',
            'context_translation_enabled',
            'response_planner_mode',
            'example_bank_limit',
            'example_bank_weight',
            'example_bank_min_score',
            'example_bank_paths_by_language',
            'example_bank_core_paths_by_language',
            'mfocus_mode',
            'mtrigger_mode',
            'tts_enabled',
            'tts_provider',
            'tts_bailian_model',
            'tts_bailian_voice',
            'tts_bailian_format',
            'tts_playback_backend',
            'tts_bailian_instruction',
            'stt_provider',
            'stt_language',
            'stt_timeout',
            'embedding_enabled',
            'memory_embedding_enabled',
            'embedding_service_enabled',
            'embedding_service_autostart',
            'embedding_service_host',
            'embedding_service_port',
            'embedding_service_timeout',
            'gui_disable_thread_embeddings',
            'gui_background_mode',
            'gui_load_recent_messages',
            'gui_idle_spire_enabled',
            'gui_idle_spire_minutes',
            'idle_self_actions_enabled',
            'gui_startup_greeting_enabled',
            'auto_memory_summary_enabled',
            'auto_memory_summary_turns',
            'token_stats_enabled',
            'show_debug',
        }
        for key, value in config.items():
            if key in public_keys:
                safe[key] = value
            elif any(word in key.lower() for word in secret_words):
                safe[key] = '<hidden>' if value else ''
        return safe

    def _debug_payload(self, engine: MaicaEngine) -> str:
        import json

        payload = self._data_snapshot(engine)
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)

    def _apply_config_updates(self, engine: MaicaEngine, updates: dict[str, Any]) -> list[str]:
        allowed = {
            'api_base': str,
            'api_key': str,
            'model': str,
            'llm_call_mode': str,
            'agent_api_base': str,
            'agent_api_key': str,
            'agent_model': str,
            'agent_tools_enabled': bool,
            'file_tools_enabled': bool,
            'vision_enabled': bool,
            'vision_model': str,
            'sandbox_root': str,
            'sandbox_readonly_allowlist': list,
            'language': str,
            'temperature': float,
            'top_p': float,
            'max_tokens': int,
            'frequency_penalty': float,
            'presence_penalty': float,
            'streaming_enabled': bool,
            'response_output_mode': str,
            'metadata_extract_enabled': bool,
            'context_translation_enabled': bool,
            'response_planner_mode': str,
            'example_bank_limit': int,
            'example_bank_weight': float,
            'example_bank_min_score': float,
            'mfocus_mode': str,
            'mtrigger_mode': str,
            'show_debug': bool,
            'tts_enabled': bool,
            'tts_provider': str,
            'tts_bailian_model': str,
            'tts_bailian_voice': str,
            'tts_bailian_format': str,
            'tts_playback_backend': str,
            'tts_bailian_instruction': str,
            'stt_provider': str,
            'stt_language': str,
            'stt_timeout': int,
            'embedding_enabled': bool,
            'memory_embedding_enabled': bool,
            'embedding_service_enabled': bool,
            'embedding_service_autostart': bool,
            'embedding_service_host': str,
            'embedding_service_port': int,
            'embedding_service_timeout': int,
            'gui_disable_thread_embeddings': bool,
            'gui_background_mode': str,
            'gui_load_recent_messages': int,
            'gui_idle_spire_enabled': bool,
            'gui_idle_spire_minutes': int,
            'idle_self_actions_enabled': bool,
            'gui_startup_greeting_enabled': bool,
            'auto_memory_summary_enabled': bool,
            'auto_memory_summary_turns': int,
            'token_stats_enabled': bool,
        }
        applied: list[str] = []
        for key, caster in allowed.items():
            if key not in updates:
                continue
            value = updates[key]
            try:
                if caster is bool:
                    if isinstance(value, str):
                        value = value.strip().lower() in {'1', 'true', 'yes', 'on'}
                    else:
                        value = bool(value)
                elif caster is int:
                    value = int(value)
                elif caster is float:
                    value = float(value)
                elif caster is list:
                    if isinstance(value, list):
                        value = [str(item).strip() for item in value if str(item).strip()]
                    else:
                        value = [part.strip() for part in str(value).split(',') if part.strip()]
                else:
                    value = str(value).strip()
            except (TypeError, ValueError):
                continue
            engine.config[key] = value
            applied.append(key)
        return applied
