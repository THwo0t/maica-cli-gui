# -*- coding: utf-8 -*-
"""Persistent GUI worker that owns MaicaEngine on a background thread."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot


ROOT_DIR = Path(__file__).resolve().parents[1]
CLI_DIR = ROOT_DIR / 'maica cli'

if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from embedding_index import prewarm_embedding_model  # noqa: E402
from engine import MaicaEngine  # noqa: E402
from mfocus import status_summary  # noqa: E402
from config_io import save_json  # noqa: E402


class GuiEngineWorker(QObject):
    ready = Signal(dict)
    config_ready = Signal(dict)
    status = Signal(str)
    finished = Signal(dict)
    data_ready = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.engine: MaicaEngine | None = None

    @Slot()
    def initialize(self) -> None:
        try:
            self.engine = MaicaEngine()
            self._apply_gui_safety_overrides()
            self.config_ready.emit(dict(self.engine.config))
            self.ready.emit({'ok': True, 'error': ''})
            self._prewarm_embeddings_if_needed()
        except Exception as exc:
            self.ready.emit({'ok': False, 'error': f'{exc}\n{traceback.format_exc()}'})

    def _apply_gui_safety_overrides(self) -> None:
        if self.engine is None:
            return
        config = self.engine.config
        if not config.get('gui_disable_thread_embeddings', True):
            return
        disabled = []
        for key in ('embedding_enabled', 'memory_embedding_enabled'):
            if config.get(key):
                config[key] = False
                disabled.append(key)
        config['gui_prewarm_embeddings'] = False
        if disabled:
            self.status.emit(
                'GUI 已禁用线程内向量检索以避免 Qt worker 闪退；CLI Debugger 仍可使用向量检索。'
            )

    def _prewarm_embeddings_if_needed(self) -> None:
        if self.engine is None:
            return
        config = self.engine.config
        if not config.get('gui_prewarm_embeddings', False):
            return
        if not (config.get('embedding_enabled') or config.get('memory_embedding_enabled')):
            return
        self.status.emit('正在后台加载向量模型...')
        report = prewarm_embedding_model(
            config,
            quiet=bool(config.get('gui_quiet_embedding_load', True)),
        )
        if report.get('ok'):
            dim = report.get('dimension') or '?'
            self.status.emit(f'向量模型已就绪：{dim}d')
        else:
            self.status.emit(f'向量模型预热失败：{report.get("error")}')

    @Slot(str)
    def chat(self, text: str) -> None:
        self._run_request('chat', text)

    @Slot(str)
    def spire(self, hint: str) -> None:
        self._run_request('spire', hint)

    @Slot()
    def shutdown(self) -> None:
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

    @Slot(dict)
    def save_config(self, updates: dict) -> None:
        self._run_data_action('save_config', updates=updates)

    def _run_request(self, mode: str, text: str) -> None:
        try:
            if self.engine is None:
                self.engine = MaicaEngine()
            if mode == 'spire':
                result = self.engine.spire(text)
            else:
                result = self.engine.chat(text)
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
                    'error': f'{exc}\n{traceback.format_exc()}',
                }
            )

    def _ensure_engine(self) -> MaicaEngine:
        if self.engine is None:
            self.engine = MaicaEngine()
            self._apply_gui_safety_overrides()
        return self.engine

    def _run_data_action(self, action: str, **kwargs: Any) -> None:
        try:
            engine = self._ensure_engine()
            notice = ''
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
            elif action == 'save_config':
                updates = kwargs.get('updates') if isinstance(kwargs.get('updates'), dict) else {}
                applied = self._apply_config_updates(engine, updates)
                if applied:
                    save_json(engine.config_path, engine.config)
                    notice = f'Saved config: {", ".join(applied)}'

            payload = self._data_snapshot(engine)
            payload.update({'ok': True, 'action': action, 'notice': notice, 'error': ''})
            self.data_ready.emit(payload)
        except Exception as exc:
            self.data_ready.emit(
                {
                    'ok': False,
                    'action': action,
                    'notice': '',
                    'error': f'{exc}\n{traceback.format_exc()}',
                }
            )

    def _data_snapshot(self, engine: MaicaEngine) -> dict[str, Any]:
        profile = engine.store.get_profile()
        return {
            'profile': profile,
            'nicknames': engine.store.get_nicknames(),
            'memories': [dict(row) for row in engine.store.all_memories()],
            'facts': [dict(row) for row in engine.store.search_facts('', 200)],
            'events': [dict(row) for row in engine.store.recent_events(30)],
            'status': status_summary(engine.store, engine.config),
            'config': self._safe_config(engine.config),
        }

    def _safe_config(self, config: dict[str, Any]) -> dict[str, Any]:
        safe = {}
        secret_words = ('key', 'token', 'secret', 'password')
        public_keys = {
            'api_base',
            'model',
            'language',
            'temperature',
            'top_p',
            'max_tokens',
            'mfocus_mode',
            'mtrigger_mode',
            'tts_enabled',
            'tts_provider',
            'tts_bailian_model',
            'tts_bailian_voice',
            'tts_bailian_format',
            'tts_bailian_instruction',
            'embedding_enabled',
            'memory_embedding_enabled',
            'gui_disable_thread_embeddings',
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
            'model': str,
            'language': str,
            'temperature': float,
            'top_p': float,
            'max_tokens': int,
            'mfocus_mode': str,
            'mtrigger_mode': str,
            'show_debug': bool,
            'tts_enabled': bool,
            'tts_provider': str,
            'tts_bailian_model': str,
            'tts_bailian_voice': str,
            'tts_bailian_format': str,
            'tts_bailian_instruction': str,
            'gui_disable_thread_embeddings': bool,
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
                else:
                    value = str(value).strip()
            except (TypeError, ValueError):
                continue
            engine.config[key] = value
            applied.append(key)
        return applied
