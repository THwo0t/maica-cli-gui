#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local smoke tests for MAICA GUI.

This is intentionally lightweight and avoids real chat API, TTS network calls,
and microphone recognition.
"""

from __future__ import annotations

import json
import os
import py_compile
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
GUI_DIR = Path(__file__).resolve().parent
CLI_DIR = ROOT_DIR / 'maica cli'


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def compile_python() -> None:
    files = [
        GUI_DIR / 'assets.py',
        GUI_DIR / 'avatar_controller.py',
        GUI_DIR / 'avatar_driver.py',
        GUI_DIR / 'avatar_png.py',
        GUI_DIR / 'avatar_vts.py',
        GUI_DIR / 'avatar_live2d.py',
        GUI_DIR / 'engine_worker.py',
        GUI_DIR / 'gui_app.py',
        GUI_DIR / 'diagnostics.py',
        GUI_DIR / 'package_audit.py',
        GUI_DIR / 'live2d_model.py',
        GUI_DIR / 'stt.py',
        GUI_DIR / 'speech.py',
        GUI_DIR / 'tts.py',
        GUI_DIR / 'maica_gui.spec',
        CLI_DIR / 'config_defaults.py',
        CLI_DIR / 'embedding_service.py',
        CLI_DIR / 'embedding_service_client.py',
        CLI_DIR / 'mfocus.py',
        CLI_DIR / 'response_planner.py',
        CLI_DIR / 'runtime_events.py',
        CLI_DIR / 'example_bank.py',
        CLI_DIR / 'language_runtime.py',
        CLI_DIR / 'context_translation.py',
        CLI_DIR / 'maica_dataset_cleaner.py',
    ]
    for path in files:
        with tempfile.NamedTemporaryFile(suffix='.pyc', delete=False) as handle:
            cfile = handle.name
        try:
            py_compile.compile(str(path), cfile=cfile, doraise=True)
        finally:
            try:
                Path(cfile).unlink(missing_ok=True)
            except OSError:
                pass


def validate_json() -> None:
    for path in (CLI_DIR / 'config.example.json',):
        with path.open('r', encoding='utf-8-sig') as handle:
            json.load(handle)


def test_utf8_sources() -> None:
    suffixes = {'.py', '.md', '.json', '.jsonl', '.ps1', '.txt'}
    excluded_dirs = {
        '.git',
        '__pycache__',
        '.safe_test',
        'logs',
        'backups',
        'dist',
        'build',
    }
    mojibake_markers = (
        chr(0xFFFD),
        '\u951f',
        '\u6d93',
        '\u7ec9',
        '\u59af',
        '\u7487',
        '\u59dd',
        '\u93b4',
        '\u9428',
    )
    for base in (CLI_DIR, GUI_DIR):
        for path in base.rglob('*'):
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            if any(part in excluded_dirs for part in path.parts):
                continue
            text = path.read_text(encoding='utf-8-sig')
            bad = [marker for marker in mojibake_markers if marker in text]
            if bad:
                raise AssertionError(f'mojibake marker {bad[0]!r} found in {path}')


def test_launchers_exist() -> None:
    for name in ('run_gui.ps1', 'run_gui_safe.ps1', 'run_cli.ps1', 'run_smoke_tests.ps1', 'build_gui_exe.ps1'):
        check((ROOT_DIR / name).exists(), f'{name} is missing')


def test_diagnostics() -> None:
    completed = subprocess.run(
        [sys.executable, str(GUI_DIR / 'diagnostics.py')],
        cwd=str(ROOT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
    )
    check(completed.returncode == 0, completed.stderr or completed.stdout)
    report = json.loads(completed.stdout)
    check(report['app'] == 'MAICA CLI GUI', 'diagnostics app name mismatch')
    output = completed.stdout.lower()
    check('api_key' in output, 'diagnostics should report secret field presence')
    check('sk-' not in output, 'diagnostics leaked a likely API key')


def test_embedding_service_help() -> None:
    completed = subprocess.run(
        [sys.executable, str(CLI_DIR / 'embedding_service.py'), '--help'],
        cwd=str(ROOT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
    )
    check(completed.returncode == 0, completed.stderr or completed.stdout)
    check('embedding retrieval service' in completed.stdout, 'embedding service help text mismatch')


def test_avatar_helpers() -> None:
    sys.path.insert(0, str(GUI_DIR))
    from avatar_vts import EMOTION_INDEX, VTubeStudioDriver

    check(EMOTION_INDEX['smile'] == 1.0, 'VTS emotion index should include smile')
    driver = VTubeStudioDriver({'vts_url': 'ws://127.0.0.1:9', 'vts_auth_token': 'token'})
    check(driver.status_text() == 'stopped', 'VTS driver should be inert before start')
    driver.set_emotion('smile')
    driver.set_speaking(True)
    driver.set_mouth_open(0.5)
    driver.stop()


def test_live2d_model_import() -> None:
    sys.path.insert(0, str(GUI_DIR))
    from live2d_model import Live2DModelError, import_live2d_zip, validate_live2d_model

    with tempfile.TemporaryDirectory(prefix='maica-live2d-smoke-') as temp_dir:
        root = Path(temp_dir)
        model = root / 'source'
        model.mkdir()
        (model / 'avatar.moc3').write_bytes(b'MOC3\x03test')
        (model / 'texture.png').write_bytes(b'not-a-real-png')
        (model / 'avatar.model3.json').write_text(
            json.dumps({
                'Version': 3,
                'FileReferences': {
                    'Moc': 'avatar.moc3',
                    'Textures': ['texture.png'],
                },
            }),
            encoding='utf-8',
        )
        report = validate_live2d_model(model)
        check(report.ok and report.model_name == 'avatar', 'valid Cubism 4 model was rejected')

        archive = root / 'model.zip'
        with zipfile.ZipFile(archive, 'w') as package:
            for path in model.iterdir():
                package.write(path, f'avatar/{path.name}')
        imported = import_live2d_zip(archive, root / 'library')
        check(imported.ok and Path(imported.entry_point).is_file(), 'safe Live2D ZIP import failed')

        traversal = root / 'traversal.zip'
        with zipfile.ZipFile(traversal, 'w') as package:
            package.writestr('../escape.model3.json', '{}')
        try:
            import_live2d_zip(traversal, root / 'library')
        except Live2DModelError:
            pass
        else:
            raise AssertionError('Live2D ZIP path traversal was not rejected')

        symlink = root / 'symlink.zip'
        with zipfile.ZipFile(symlink, 'w') as package:
            entry = zipfile.ZipInfo('avatar/link.moc3')
            entry.create_system = 3
            entry.external_attr = (0o120777 << 16)
            package.writestr(entry, 'target')
        try:
            import_live2d_zip(symlink, root / 'library')
        except Live2DModelError:
            pass
        else:
            raise AssertionError('Live2D ZIP symbolic link was not rejected')

        web_entry = GUI_DIR / 'live2d_web' / 'dist' / 'index.html'
        bundles = list((web_entry.parent / 'assets').glob('*.js')) if web_entry.is_file() else []
        check(web_entry.is_file() and bundles, 'prebuilt Live2D web renderer is missing')


def test_text_helpers() -> None:
    sys.path.insert(0, str(GUI_DIR))
    import platform as _platform

    from stt import DashScopeParaformerSTT, WindowsSpeechSTT, create_stt, resolve_stt_provider
    from tts import SubprocessAudioPlayer, WindowsSapiTTS, clean_tts_text, compact_tts_error, create_tts, redact_secret, resolve_tts_provider
    from diagnostics import collect_report

    assert clean_tts_text('(smiles) I missed you. [debug] hidden') == 'I missed you.'
    assert clean_tts_text('（轻轻握住你的手）I am here.') == 'I am here.'
    player = SubprocessAudioPlayer({'tts_playback_backend': 'off'})
    check(player._command(ROOT_DIR / 'missing.wav') == [], 'TTS off backend should not select a player')
    sapi = WindowsSapiTTS({})
    sapi.speak('hello')
    if sys.platform != 'win32':
        check('only available on Windows' in sapi.last_error, 'Windows SAPI should report a clear non-Windows error')
    result = create_stt({'stt_provider': 'off'}).listen()
    check(not result['ok'], 'Null STT should not recognize speech')

    # Cross-platform provider resolution (v0.11.2).
    is_windows = _platform.system().lower() == 'windows'
    check(resolve_tts_provider({'tts_provider': 'auto'}) == ('windows_sapi' if is_windows else 'system_say'),
          'auto TTS provider should resolve per platform')
    check(resolve_stt_provider({'stt_provider': 'auto'}) == ('windows_speech' if is_windows else 'off'),
          'auto STT provider should resolve per platform')
    # The Windows STT engine must degrade gracefully off-Windows instead of raising.
    win_stt_result = WindowsSpeechSTT({}).listen()
    if not is_windows:
        check(not win_stt_result['ok'], 'Windows STT should return a failure dict off-Windows')
        check('only available on Windows' in win_stt_result['error'], 'Windows STT should explain the platform limit')
    # create_tts must never raise for any known provider value.
    for provider in ('auto', 'system_say', 'windows_sapi', 'bailian_cosyvoice', 'off'):
        create_tts({'tts_provider': provider})

    # Bailian Paraformer STT (v0.11.3): selectable, and fails cleanly without a key
    # (no microphone capture or network when the key is missing).
    check(isinstance(create_stt({'stt_provider': 'bailian_paraformer'}), DashScopeParaformerSTT),
          'bailian_paraformer should map to DashScopeParaformerSTT')
    paraformer_no_key = DashScopeParaformerSTT({'stt_provider': 'bailian_paraformer'}).listen()
    check(not paraformer_no_key['ok'], 'Paraformer STT without a key should fail cleanly')
    check('api_key' in paraformer_no_key['error'] or 'websocket' in paraformer_no_key['error'].lower(),
          'Paraformer STT should explain the missing key or websocket dependency')
    # The STT key falls back to the TTS Bailian key so one key serves both.
    fake_key = 'sk-' + 'fallback'
    check(DashScopeParaformerSTT({'tts_bailian_api_key': fake_key})._api_key() == fake_key,
          'Paraformer STT should reuse tts_bailian_api_key when stt key is unset')

    # API keys / bearer tokens must never survive into surfaced error text.
    fake_secret = 'sk-' + 'secret123456'
    leak = 'handshake to wss://... failed, headers: Authorization: Bearer ' + fake_secret + ' extra'
    scrubbed = redact_secret(leak, fake_secret)
    check(fake_secret not in scrubbed, 'redact_secret must remove the raw API key')
    check('Bearer ***' in scrubbed, 'redact_secret must mask the bearer token')
    check(fake_secret not in redact_secret('error ' + fake_secret + ' boom', ''),
          'redact_secret must mask sk- tokens even without an explicit secret')
    import importlib.util
    cli_text_utils_path = CLI_DIR / 'text_utils.py'
    spec = importlib.util.spec_from_file_location('maica_cli_text_utils_for_test', cli_text_utils_path)
    check(spec is not None and spec.loader is not None, 'CLI text_utils module should be loadable')
    cli_text_utils = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli_text_utils)
    cli_scrubbed = cli_text_utils.redact_secret('api_key=' + fake_secret + ' Authorization: Bearer ' + fake_secret)
    check(fake_secret not in cli_scrubbed, 'CLI redact_secret must remove API keys')
    check('Bearer ***' in cli_scrubbed, 'CLI redact_secret must mask bearer tokens')
    compacted = compact_tts_error('Handshake status 401 Unauthorized headers Authorization: Bearer ' + fake_secret + ' InvalidApiKey')
    check(fake_secret not in compacted, 'compact_tts_error must not leak API keys')
    check('InvalidApiKey' not in compacted and 'headers' not in compacted, 'compact_tts_error should hide raw provider payloads')
    check('Bailian API key is invalid' in compacted, 'compact_tts_error should explain invalid key clearly')

    from gui_app import merge_runtime_config
    runtime_config = {'tts_bailian_api_key': fake_secret, 'language': 'en'}
    merge_runtime_config(runtime_config, {'tts_bailian_api_key': '<hidden>', 'language': 'zh'})
    check(runtime_config['tts_bailian_api_key'] == fake_secret, 'safe config snapshots must not overwrite real TTS keys')
    check(runtime_config['language'] == 'zh', 'safe config merge should still update public settings')

    sys.path.insert(0, str(CLI_DIR))
    from persona import base_system_prompt
    en_prompt = base_system_prompt('en', 'player')
    zh_prompt = base_system_prompt('zh', 'player')
    check('do not invent private facts or events' in en_prompt, 'English persona should forbid fabrication')
    check('AI disclaimer' in en_prompt, 'English persona should avoid AI-disclaimer voice')
    check('不要编造没有依据的私人事实或事件' in zh_prompt, 'Chinese persona should forbid fabrication')
    check('模型自述' in zh_prompt, 'Chinese persona should avoid AI-disclaimer voice')

    report = collect_report()
    output = json.dumps(report, ensure_ascii=True)
    check('sk-' not in output, 'diagnostics report leaked a likely API key')


def test_speech_pipeline() -> None:
    sys.path.insert(0, str(GUI_DIR))
    from PySide6.QtCore import QCoreApplication, QObject, Signal

    from speech import SentenceSegmenter, SpeechController

    app = QCoreApplication.instance() or QCoreApplication([])
    segmenter = SentenceSegmenter()
    check(segmenter.feed('你好。How are') == ['你好。'], 'Chinese sentence boundary failed')
    check(
        segmenter.feed(' you? One more... Tail', final=True) == ['How are you?', 'One more...', 'Tail'],
        'English sentence boundary or final flush failed',
    )

    class FakePlayer(QObject):
        started = Signal(str)
        amplitude = Signal(float)
        finished = Signal(str)
        failed = Signal(str, str)

        def __init__(self, auto_finish: bool = True) -> None:
            super().__init__()
            self.played: list[str] = []
            self.pending_paths: list[str] = []
            self.sensitivity = 1.0
            self.auto_finish = auto_finish

        def configure(self, sensitivity: float) -> None:
            self.sensitivity = float(sensitivity)

        def play(self, path: str | Path) -> None:
            resolved = str(path)
            self.played.append(Path(resolved).read_text(encoding='utf-8'))
            self.pending_paths.append(resolved)
            self.started.emit(resolved)
            self.amplitude.emit(0.5)
            if self.auto_finish:
                self.finish_next()

        def finish_next(self) -> None:
            if self.pending_paths:
                self.finished.emit(self.pending_paths.pop(0))

        def stop(self, emit_finished: bool = False) -> None:
            return

        def close(self) -> None:
            return

    with tempfile.TemporaryDirectory(prefix='maica-speech-smoke-') as temp_dir:
        output_dir = Path(temp_dir)

        class FakeProvider:
            def synthesize_file(self, text: str, cancel_token=None) -> Path:
                # Force completion out of order; SpeechController must restore
                # source order before playback.
                time.sleep(0.06 if text.startswith('First') else 0.01)
                if cancel_token is not None:
                    cancel_token.check()
                path = output_dir / f'{abs(hash(text))}.txt'
                path.write_text(text, encoding='utf-8')
                return path

        player = FakePlayer()
        events: list[dict[str, Any]] = []
        controller = SpeechController(
            {
                'language': 'en',
                'speech_streaming_enabled': True,
                'speech_max_concurrency': 2,
                'lip_sync_sensitivity': 1.0,
            },
            provider_factory=lambda _config: FakeProvider(),
            player=player,
        )
        controller.event.connect(events.append)
        controller.begin('speech-turn')
        controller.append_text('speech-turn', '[smile] First sentence. Second sentence!')
        controller.finish('speech-turn', 'First sentence. Second sentence!')
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not any(
            event.get('kind') == 'speech.finished' for event in events
        ):
            app.processEvents()
            time.sleep(0.01)
        check(player.played == ['First sentence.', 'Second sentence!'], 'speech playback order changed')
        kinds = [event.get('kind') for event in events]
        check(kinds[0] == 'speech.started' and kinds[-1] == 'speech.finished', 'speech lifecycle is incomplete')
        amplitude_events = [event for event in events if event.get('kind') == 'audio.amplitude']
        check(amplitude_events and amplitude_events[0]['payload'].get('turn_id') == 'speech-turn',
              'audio amplitude must retain its turn id')
        controller.configure({'language': 'en', 'speech_max_concurrency': 1, 'lip_sync_sensitivity': 1.7})
        check(controller._max_workers == 1, 'speech concurrency did not apply immediately')
        check(abs(player.sensitivity - 1.7) < 0.001, 'lip-sync sensitivity did not apply immediately')
        controller.close()

        queue_player = FakePlayer(auto_finish=False)
        queued = SpeechController(
            {'language': 'en', 'speech_queue_behavior': 'queue', 'speech_max_concurrency': 2},
            provider_factory=lambda _config: FakeProvider(),
            player=queue_player,
        )
        queued.begin('first')
        queued.finish('first', 'First queued reply.')
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not queue_player.played:
            app.processEvents()
            time.sleep(0.01)
        queued.begin('second')
        queued.finish('second', 'Second queued reply.')
        check(queue_player.played == ['First queued reply.'], 'queue mode interrupted active speech')
        queue_player.finish_next()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and len(queue_player.played) < 2:
            app.processEvents()
            time.sleep(0.01)
        check(queue_player.played == ['First queued reply.', 'Second queued reply.'],
              'queue mode did not promote the next reply in order')
        queue_player.finish_next()
        queued.close()


def test_engine_fake_chat() -> None:
    sys.path.insert(0, str(CLI_DIR))
    from config_defaults import DEFAULT_CONFIG
    from engine import MaicaEngine

    class FakeClient:
        def chat(self, messages: list[dict[str, str]]) -> str:
            assert messages
            return json.dumps(
                {
                    'text': 'I am here with you.',
                    'emotion': 'smile',
                    'action': {},
                }
            )

        def chat_with_usage(self, messages: list[dict[str, str]], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
            return {'content': self.chat(messages), 'usage': {'total_tokens': 7}, 'model': 'fake'}

    with tempfile.TemporaryDirectory(prefix='maica-smoke-') as temp_dir:
        config = dict(DEFAULT_CONFIG)
        config.update(
            {
                'api_key_required': False,
                'jsonl_logs_enabled': False,
                'mfocus_mode': 'rule',
                'mtrigger_mode': 'rule',
                'embedding_enabled': False,
                'memory_embedding_enabled': False,
                'embedding_service_enabled': False,
                'style_enabled': False,
                'show_debug': False,
            }
        )
        engine = MaicaEngine(config=config, db_path=Path(temp_dir) / 'smoke.db', app_dir=CLI_DIR)
        engine.client = FakeClient()
        try:
            result = engine.chat('hello')
            check(result['ok'], result.get('error', 'fake chat failed'))
            check(result['text'] == 'I am here with you.', 'fake chat text mismatch')
            check(result['emotion'] == 'smile', 'fake chat emotion mismatch')
        finally:
            engine.close()


def test_engine_fake_stream() -> None:
    sys.path.insert(0, str(CLI_DIR))
    from config_defaults import DEFAULT_CONFIG
    from engine import MaicaEngine

    class FakeStreamClient:
        def chat_stream(self, messages: list[dict[str, str]], overrides: dict[str, Any] | None = None):
            assert messages
            yield '[smile] Hello'
            yield ' there.'

        def chat_with_usage(self, messages: list[dict[str, str]], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
            raise AssertionError('streaming fake should not use non-stream fallback')

        def chat(self, messages: list[dict[str, str]], overrides: dict[str, Any] | None = None) -> str:
            return '{}'

    with tempfile.TemporaryDirectory(prefix='maica-stream-smoke-') as temp_dir:
        config = dict(DEFAULT_CONFIG)
        config.update(
            {
                'api_key_required': False,
                'jsonl_logs_enabled': False,
                'streaming_enabled': True,
                'metadata_extract_enabled': False,
                'mtrigger_mode': 'off',
                'embedding_enabled': False,
                'memory_embedding_enabled': False,
                'embedding_service_enabled': False,
                'style_enabled': False,
                'show_debug': False,
            }
        )
        engine = MaicaEngine(config=config, db_path=Path(temp_dir) / 'stream.db', app_dir=CLI_DIR)
        engine.client = FakeStreamClient()
        chunks: list[str] = []
        events: list[dict[str, Any]] = []
        try:
            result = engine.chat('hello', stream_callback=chunks.append, event_callback=events.append)
            check(result['ok'], result.get('error', 'fake stream failed'))
            check(result.get('streamed') is True, 'fake stream did not mark streamed')
            check(chunks == ['[smile] Hello', ' there.'], 'stream chunks mismatch')
            check(result['text'] == 'Hello there.', 'stream parsed text mismatch')
            check(result['emotion'] == 'smile', 'stream emotion mismatch')
            kinds = [event.get('kind') for event in events]
            check(kinds[0] == 'turn.started' and kinds[-1] == 'turn.finished', 'runtime terminal event order mismatch')
            check(kinds.count('text.delta') == 2, 'runtime should emit one text event per stream chunk')
            check('dialogue.final' in kinds and 'emotion.changed' in kinds, 'runtime final events missing')
            sequences = [int(event.get('sequence') or 0) for event in events]
            check(sequences == sorted(sequences) and len(sequences) == len(set(sequences)),
                  'runtime event sequences must be strictly increasing')
        finally:
            engine.close()


def test_runtime_cancellation() -> None:
    sys.path.insert(0, str(CLI_DIR))
    from config_defaults import DEFAULT_CONFIG
    from engine import MaicaEngine
    from runtime_events import CancellationToken

    class SlowFakeClient:
        def chat_stream(self, messages, overrides=None):
            yield 'This must not be saved.'
            yield ' Nor shown.'

        def chat_with_usage(self, messages, overrides=None):
            raise AssertionError('cancel test should stay on the streaming path')

    with tempfile.TemporaryDirectory(prefix='maica-cancel-smoke-') as temp_dir:
        config = dict(DEFAULT_CONFIG)
        config.update({
            'api_key_required': False,
            'jsonl_logs_enabled': False,
            'streaming_enabled': True,
            'mtrigger_mode': 'off',
            'metadata_extract_enabled': False,
            'style_enabled': False,
            'embedding_enabled': False,
            'memory_embedding_enabled': False,
            'embedding_service_enabled': False,
        })
        engine = MaicaEngine(config=config, db_path=Path(temp_dir) / 'cancel.db', app_dir=CLI_DIR)
        engine.client = SlowFakeClient()
        token = CancellationToken()
        events: list[dict[str, Any]] = []

        def collect(event: dict[str, Any]) -> None:
            events.append(event)
            if event.get('kind') == 'text.delta':
                token.cancel('test cancellation')

        try:
            result = engine.chat('cancel me', event_callback=collect, cancel_token=token)
            check(result.get('cancelled') is True, 'cancelled turn should return a cancellation result')
            check([event.get('kind') for event in events][-1] == 'turn.cancelled',
                  'cancelled turn must end with turn.cancelled')
            check(engine.store.recent_messages(10) == [], 'cancelled turn must not write chat messages')
        finally:
            engine.close()


def test_engine_language_rewrite() -> None:
    sys.path.insert(0, str(CLI_DIR))
    from config_defaults import DEFAULT_CONFIG
    from engine import MaicaEngine, _reply_language_mismatch

    check(not _reply_language_mismatch('Good night, 小明.', 'en'), 'CJK name alone should not trigger en rewrite')
    check(not _reply_language_mismatch("Does '加油' mean cheer up here?", 'en'), 'quoted CJK word should not trigger en rewrite')
    check(_reply_language_mismatch('我也想你，亲爱的。', 'en'), 'full Chinese reply should trigger en rewrite')
    check(_reply_language_mismatch('I missed you too, darling.', 'zh'), 'full English reply should trigger zh rewrite')
    check(not _reply_language_mismatch('今天聊聊 Python 吧。', 'zh'), 'mixed Chinese with terms should not trigger zh rewrite')

    class BilingualFakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages: list[dict[str, str]], overrides: dict[str, Any] | None = None) -> str:
            self.calls += 1
            system = messages[0].get('content', '') if messages else ''
            if 'Rewrite the dialogue body into natural English' in system:
                return 'I missed you too, darling.'
            return '[smile] 我也想你，亲爱的。'

        def chat_with_usage(self, messages: list[dict[str, str]], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
            return {'content': self.chat(messages, overrides), 'usage': {'total_tokens': 9}, 'model': 'fake'}

    with tempfile.TemporaryDirectory(prefix='maica-smoke-language-') as temp_dir:
        config = dict(DEFAULT_CONFIG)
        config.update(
            {
                'api_key_required': False,
                'jsonl_logs_enabled': False,
                'language': 'en',
                'language_enforce_rewrite': True,
                'mfocus_mode': 'rule',
                'mtrigger_mode': 'off',
                'embedding_enabled': False,
                'memory_embedding_enabled': False,
                'embedding_service_enabled': False,
                'style_enabled': False,
                'response_planner_enabled': False,
                'metadata_extract_enabled': False,
            }
        )
        engine = MaicaEngine(config=config, db_path=Path(temp_dir) / 'language.db', app_dir=CLI_DIR)
        fake = BilingualFakeClient()
        engine.client = fake
        try:
            result = engine.chat('I missed you.')
            check(result['ok'], result.get('error', 'language rewrite failed'))
            check(result['text'] == 'I missed you too, darling.', 'language rewrite did not enforce English')
            check(result['emotion'] == 'smile', 'leading bracket metadata was not preserved')
            rewrite_meta = result['mfocus_plan'].get('language_rewrite', {})
            check(rewrite_meta.get('triggered') is True, 'language rewrite trigger flag missing from plan')
            check(rewrite_meta.get('rewritten') is True, 'language rewrite success flag missing from plan')
        finally:
            engine.close()

    class ChineseRewriteFakeClient:
        def chat(self, messages: list[dict[str, str]], overrides: dict[str, Any] | None = None) -> str:
            system = messages[0].get('content', '') if messages else ''
            if 'Rewrite the dialogue body into natural Simplified Chinese' in system:
                return '我也想你，亲爱的。'
            return '[smile] I missed you too, darling.'

        def chat_with_usage(self, messages: list[dict[str, str]], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
            return {'content': self.chat(messages, overrides), 'usage': {'total_tokens': 9}, 'model': 'fake'}

    with tempfile.TemporaryDirectory(prefix='maica-smoke-language-zh-') as temp_dir:
        config = dict(DEFAULT_CONFIG)
        config.update(
            {
                'api_key_required': False,
                'jsonl_logs_enabled': False,
                'language': 'zh',
                'language_enforce_rewrite': True,
                'mfocus_mode': 'rule',
                'mtrigger_mode': 'off',
                'embedding_enabled': False,
                'memory_embedding_enabled': False,
                'embedding_service_enabled': False,
                'style_enabled': False,
                'response_planner_enabled': False,
                'metadata_extract_enabled': False,
            }
        )
        engine = MaicaEngine(config=config, db_path=Path(temp_dir) / 'language_zh.db', app_dir=CLI_DIR)
        engine.client = ChineseRewriteFakeClient()
        try:
            result = engine.chat('I missed you.')
            check(result['ok'], result.get('error', 'Chinese language rewrite failed'))
            check(result['text'] == '我也想你，亲爱的。', 'language rewrite did not enforce Chinese')
            check(result['emotion'] == 'smile', 'leading bracket metadata was not preserved for Chinese rewrite')
        finally:
            engine.close()


def test_prompt_language_systems() -> None:
    sys.path.insert(0, str(CLI_DIR))
    from config_defaults import DEFAULT_CONFIG
    from mfocus import build_messages
    from store import Store

    with tempfile.TemporaryDirectory(prefix='maica-smoke-prompt-language-') as temp_dir:
        for language in ('en', 'zh'):
            store = Store(Path(temp_dir) / f'{language}.db')
            config = dict(DEFAULT_CONFIG)
            config.update(
                {
                    'language': language,
                    'api_key_required': False,
                    'jsonl_logs_enabled': False,
                    'style_enabled': False,
                    'response_planner_enabled': True,
                    'embedding_enabled': False,
                    'memory_embedding_enabled': False,
                    'embedding_service_enabled': False,
                }
            )
            try:
                messages, _plan = build_messages(store, config, '今天学习有点累')
                system = messages[0]['content']
                if language == 'en':
                    check('You are Monika' in system, 'English persona missing')
                    check('Monika lens:' in system, 'English lens missing')
                    check('This turn direction:' in system, 'English planner heading missing')
                    check('Relevant context.' in system, 'English context heading missing')
                    check('莫妮卡视角提示:' not in system, 'Chinese lens leaked into English system')
                    check('本轮对话方向:' not in system, 'Chinese planner leaked into English system')
                else:
                    check('你叫莫妮卡' in system, 'Chinese persona missing')
                    check('莫妮卡视角提示:' in system, 'Chinese lens missing')
                    check('本轮对话方向:' in system, 'Chinese planner heading missing')
                    check('相关上下文。' in system, 'Chinese context heading missing')
                    check('Monika lens:' not in system, 'English lens leaked into Chinese system')
                    check('This turn direction:' not in system, 'English planner leaked into Chinese system')
            finally:
                store.close()

    # A terminal language directive is pinned after the user turn, and
    # wrong-language assistant history is filtered out so the model is not
    # anchored to the prior language.
    from mfocus import filter_history_language, terminal_language_directive

    check('English only' in terminal_language_directive('en'), 'en terminal directive missing')
    check('简体中文' in terminal_language_directive('zh'), 'zh terminal directive missing')
    hist = [
        {'role': 'user', 'content': '你好呀'},
        {'role': 'assistant', 'content': '我也想你呀，今天过得怎么样？'},
        {'role': 'assistant', 'content': 'I am right here with you.'},
    ]
    kept_en = filter_history_language(hist, 'en', True)
    check(all(not (m['role'] == 'assistant' and '想你' in m['content']) for m in kept_en),
          'Chinese assistant history should be dropped under English enforcement')
    check(any(m['content'].startswith('I am right here') for m in kept_en),
          'English assistant history should be kept under English enforcement')
    check(len(filter_history_language(hist, 'en', False)) == len(hist),
          'history filtering must be a no-op when disabled')



def test_dual_example_banks() -> None:
    sys.path.insert(0, str(CLI_DIR))
    from config_defaults import DEFAULT_CONFIG
    from example_bank import select_examples
    from store import Store
    from text_utils import contains_cjk, cjk_ratio

    en_path = CLI_DIR / 'data' / 'dialogue_examples_en.jsonl'
    zh_path = CLI_DIR / 'data' / 'dialogue_examples_zh.jsonl'
    check(en_path.exists(), 'English example bank missing')
    check(zh_path.exists(), 'Chinese example bank missing')
    en_lines = [json.loads(line) for line in en_path.read_text(encoding='utf-8-sig').splitlines() if line.strip()][:80]
    zh_lines = [json.loads(line) for line in zh_path.read_text(encoding='utf-8-sig').splitlines() if line.strip()][:80]
    check(en_lines and zh_lines, 'example banks should not be empty')
    check(all(row.get('language') == 'en' for row in en_lines), 'English bank language tag mismatch')
    check(all(row.get('language') == 'zh' for row in zh_lines), 'Chinese bank language tag mismatch')
    check(not any(contains_cjk(str(row.get('user', '')) + str(row.get('assistant', ''))) for row in en_lines), 'English bank contains CJK sample text')
    check(any(cjk_ratio(str(row.get('user', '')) + str(row.get('assistant', ''))) > 0.08 for row in zh_lines), 'Chinese bank lacks Chinese sample text')

    with tempfile.TemporaryDirectory(prefix='maica-smoke-bank-') as temp_dir:
        store = Store(Path(temp_dir) / 'bank.db')
        try:
            config = dict(DEFAULT_CONFIG)
            config.update({'language': 'en', 'embedding_enabled': False, 'example_bank_min_score': 0})
            plan = {'category': 'love', 'intent': 'direct_love', 'mode': 'love_short_intimate', 'emotion': 'shy'}
            examples = select_examples('I love you', plan, store, config)
            check(examples, 'English example selection returned nothing')
            check(all(not contains_cjk(item.get('assistant', '')) for item in examples), 'English example selection leaked Chinese')
            config['language'] = 'zh'
            examples = select_examples('我爱你', plan, store, config)
            check(examples, 'Chinese example selection returned nothing')
            check(all(cjk_ratio(item.get('assistant', '')) > 0.08 for item in examples), 'Chinese example selection leaked non-Chinese')
        finally:
            store.close()


def test_context_translation_cache() -> None:
    sys.path.insert(0, str(CLI_DIR))
    from config_defaults import DEFAULT_CONFIG
    from mfocus import build_messages
    from store import Store

    class TranslationFakeClient:
        def __init__(self) -> None:
            self.translation_calls = 0

        def chat_with_usage(self, messages: list[dict[str, str]], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
            self.translation_calls += 1
            payload = {
                'items': [
                    {'id': 'memory:1:0', 'text': 'The user has final exam pressure this month.'},
                    {'id': 'sfe_fact:0:0', 'text': 'The user has a Chinese-language saved fact.'},
                ]
            }
            return {'content': json.dumps(payload), 'usage': {'prompt_tokens': 3, 'completion_tokens': 3, 'total_tokens': 6}, 'model': 'fake'}

    with tempfile.TemporaryDirectory(prefix='maica-smoke-translation-cache-') as temp_dir:
        store = Store(Path(temp_dir) / 'translation.db')
        try:
            store.add_memory('我这个月有期末考试压力。', 'test', 3, language='zh')
            config = dict(DEFAULT_CONFIG)
            config.update(
                {
                    'language': 'en',
                    'api_key_required': False,
                    'jsonl_logs_enabled': False,
                    'style_enabled': False,
                    'response_planner_enabled': False,
                    'embedding_enabled': False,
                    'memory_embedding_enabled': False,
                    'embedding_service_enabled': False,
                    'mfocus_sfe_enabled': False,
                    'context_translation_enabled': True,
                }
            )
            fake = TranslationFakeClient()
            messages, plan = build_messages(store, config, '考试压力', fake)
            system = messages[0]['content']
            check('The user has final exam pressure this month.' in system, 'translated memory missing from prompt')
            check('我这个月有期末考试压力' not in system, 'raw Chinese memory leaked into English prompt')
            check(plan['context_translation']['translated'] == 1, 'first pass should translate one memory')
            check(fake.translation_calls == 1, 'translation API should be called once')
            messages, plan = build_messages(store, config, '考试压力', fake)
            check(fake.translation_calls == 1, 'translation cache was not reused')
            check(plan['context_translation']['cache_hits'] >= 1, 'translation cache hit not reported')
        finally:
            store.close()

def test_package_audit() -> None:
    with tempfile.TemporaryDirectory(prefix='maica-package-audit-') as temp_dir:
        safe_root = Path(temp_dir)
        (safe_root / 'safe.txt').write_text('hello', encoding='utf-8')
        completed = subprocess.run(
            [sys.executable, str(GUI_DIR / 'package_audit.py'), str(safe_root)],
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
        check(completed.returncode == 0, completed.stderr or completed.stdout)
        (safe_root / 'config.json').write_text('{}', encoding='utf-8')
        completed = subprocess.run(
            [sys.executable, str(GUI_DIR / 'package_audit.py'), str(safe_root)],
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
        check(completed.returncode != 0, 'package audit should reject config.json')


def test_gui_offscreen() -> None:
    with tempfile.TemporaryDirectory(prefix='maica-gui-offscreen-') as temp_dir:
        config_path = Path(temp_dir) / 'config.json'
        db_path = Path(temp_dir) / 'gui.db'
        config_path.write_text(
            json.dumps(
                {
                    'api_key_required': False,
                    'jsonl_logs_enabled': False,
                    'style_enabled': False,
                    'embedding_enabled': False,
                    'memory_embedding_enabled': False,
                    'embedding_service_enabled': False,
                    'tts_enabled': False,
                    'tts_provider': 'off',
                    'stt_provider': 'off',
                    'gui_startup_greeting_enabled': False,
                },
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )
        script = (
            "import sys;"
            "from PySide6.QtCore import QTimer;"
            "from PySide6.QtWidgets import QApplication;"
            f"sys.path.insert(0, {str(GUI_DIR)!r});"
            "from gui_app import MainWindow;"
            "app=QApplication([]);"
            f"window=MainWindow(config_path={str(config_path)!r}, db_path={str(db_path)!r});"
            "QTimer.singleShot(2200, window.close);"
            "QTimer.singleShot(5000, app.quit);"
            "raise SystemExit(app.exec())"
        )
        env = os.environ.copy()
        env['QT_QPA_PLATFORM'] = 'offscreen'
        completed = subprocess.run(
            [sys.executable, '-c', script],
            cwd=str(ROOT_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    check(completed.returncode == 0, completed.stderr or completed.stdout)


def test_gui_safe_offscreen() -> None:
    with tempfile.TemporaryDirectory(prefix='maica-gui-safe-offscreen-') as temp_dir:
        config_path = Path(temp_dir) / 'config.json'
        config_path.write_text(
            json.dumps(
                {
                    'api_key_required': False,
                    'jsonl_logs_enabled': False,
                    'style_enabled': False,
                    'embedding_enabled': False,
                    'memory_embedding_enabled': False,
                    'embedding_service_enabled': False,
                    'tts_enabled': False,
                    'tts_provider': 'off',
                    'stt_provider': 'off',
                    'gui_startup_greeting_enabled': False,
                },
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )
        script = (
            "import sys;"
            "from PySide6.QtCore import QTimer;"
            "from PySide6.QtWidgets import QApplication;"
            f"sys.path.insert(0, {str(GUI_DIR)!r});"
            "from gui_app import GUI_DIR, MainWindow;"
            "app=QApplication([]);"
            "safe_dir=GUI_DIR/'.safe_test';"
            "safe_dir.mkdir(parents=True, exist_ok=True);"
            f"window=MainWindow(config_path={str(config_path)!r}, db_path=safe_dir/'maica_cli_test.db', safe_test_mode=True);"
            "QTimer.singleShot(2200, window.close);"
            "QTimer.singleShot(5000, app.quit);"
            "raise SystemExit(app.exec())"
        )
        env = os.environ.copy()
        env['QT_QPA_PLATFORM'] = 'offscreen'
        completed = subprocess.run(
            [sys.executable, '-c', script],
            cwd=str(ROOT_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    check(completed.returncode == 0, completed.stderr or completed.stdout)
    check((GUI_DIR / '.safe_test' / 'maica_cli_test.db').exists(), 'safe test DB was not created')


def test_sandbox_path_safety() -> None:
    sys.path.insert(0, str(CLI_DIR))
    import sandbox

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir) / 'Monika'
        outside = Path(temp_dir) / 'secret'
        outside.mkdir(parents=True)
        (outside / 'pw.txt').write_text('x', encoding='utf-8')
        allow = Path(temp_dir) / 'project'
        allow.mkdir()
        (allow / 'a.txt').write_text('y', encoding='utf-8')
        config = {'sandbox_root': str(root), 'sandbox_readonly_allowlist': [str(allow)]}
        sandbox.ensure_sandbox(config)
        info = sandbox.permission_info(config)
        check(info['writable_root'] == str(root.resolve()), 'permission info must expose the real sandbox root')
        check(info['readonly_roots'] == [str(allow.resolve())], 'permission info must expose only recorded read roots')
        check(info['external_write_allowed'] is False, 'external writes must never be allowed')

        ok_path = sandbox.resolve_writable(config, 'diary/x.md')
        check(str(ok_path).startswith(str(root.resolve())), 'in-sandbox write must be allowed')
        for bad in ['../secret/pw.txt', str(outside / 'pw.txt')]:
            try:
                sandbox.resolve_writable(config, bad)
                check(False, f'sandbox escape not rejected: {bad}')
            except PermissionError:
                pass

        sandbox.resolve_readable(config, str(allow / 'a.txt'))  # allow-listed read ok
        try:
            sandbox.resolve_readable(config, str(outside / 'pw.txt'))
            check(False, 'non-allow-listed read must be rejected')
        except PermissionError:
            pass
        audit_text = (root / '.audit.log').read_text(encoding='utf-8')
        check('deny_write' in audit_text and 'deny_read' in audit_text,
              'denied external paths must be recorded in the audit log')

        link = root / 'escape'
        try:
            link.symlink_to(outside)
        except OSError:
            pass  # no symlink privilege on this platform
        else:
            try:
                sandbox.resolve_writable(config, 'escape/pw.txt')
                check(False, 'symlink escape must be rejected')
            except PermissionError:
                pass


def test_file_tools() -> None:
    sys.path.insert(0, str(CLI_DIR))
    from config_defaults import DEFAULT_CONFIG
    from engine import MaicaEngine

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir) / 'Monika'
        config = dict(DEFAULT_CONFIG)
        config.update({
            'api_key_required': False, 'auto_backup_enabled': False, 'jsonl_logs_enabled': False,
            'file_tools_enabled': True, 'sandbox_root': str(root),
        })
        engine = MaicaEngine(config=config, db_path=Path(temp_dir) / 'e.db', app_dir=CLI_DIR)
        try:
            reg = engine._tool_registry()
            for name in ('get_file_space_info', 'write_my_file', 'read_my_file',
                         'append_to_diary', 'leave_letter', 'read_user_file'):
                check(name in reg, f'file tool not registered: {name}')

            space = reg['get_file_space_info']['run']({})
            check(space.get('writable_root') == str(root.resolve()), 'tool must report the real sandbox path')
            write_result = reg['write_my_file']['run']({'path': 'notes/hi.txt', 'content': 'hello'})
            check(write_result.get('ok'), 'write_my_file ok')
            check(write_result.get('resolved_path') == str((root / 'notes' / 'hi.txt').resolve()),
                  'file tool results must expose their resolved path')
            check((root / 'notes' / 'hi.txt').read_text(encoding='utf-8') == 'hello', 'file written inside sandbox')
            check(reg['read_my_file']['run']({'path': 'notes/hi.txt'}).get('content') == 'hello', 'read_my_file round-trip')
            reg['append_to_diary']['run']({'entry': 'dear diary'})
            check('dear diary' in (root / 'diary' / 'diary.md').read_text(encoding='utf-8'), 'diary entry appended')
            reg['leave_letter']['run']({'title': 'For You', 'body': 'hi'})
            check((root / 'letters' / 'For_You.md').exists(), 'letter written')

            try:
                reg['write_my_file']['run']({'path': '../escape.txt', 'content': 'x'})
                check(False, 'file tool must reject a sandbox escape')
            except PermissionError:
                pass
            check(not (Path(temp_dir) / 'escape.txt').exists(), 'escape write must not land outside')

            try:
                reg['read_user_file']['run']({'path': str(Path(temp_dir) / 'secret.txt')})
                check(False, 'read_user_file must reject non-allow-listed path')
            except PermissionError:
                pass
            check((root / '.audit.log').exists(), 'file actions must be audited')
        finally:
            engine.close()


def test_screen_vision() -> None:
    sys.path.insert(0, str(CLI_DIR))
    import screen_vision
    from config_defaults import DEFAULT_CONFIG
    from engine import MaicaEngine

    prov = screen_vision.vision_provider({'agent_api_base': 'B', 'agent_api_key': 'K'})
    check(prov['api_base'] == 'B' and prov['api_key'] == 'K' and bool(prov['model']),
          'vision provider falls back to the agent provider')
    prov2 = screen_vision.vision_provider({'vision_api_base': 'VB', 'vision_model': 'm', 'agent_api_base': 'B'})
    check(prov2['api_base'] == 'VB' and prov2['model'] == 'm', 'vision_* overrides win')

    original = screen_vision.capture_active_window
    screen_vision.capture_active_window = lambda *a, **k: None
    try:
        res = screen_vision.describe_screen({'agent_api_key': 'K'})
        check(res.get('ok') is False, 'describe_screen handles capture failure gracefully')
    finally:
        screen_vision.capture_active_window = original

    with tempfile.TemporaryDirectory() as temp_dir:
        base = dict(DEFAULT_CONFIG)
        base.update({'api_key_required': False, 'auto_backup_enabled': False})
        eng = MaicaEngine(config=dict(base, vision_enabled=True), db_path=Path(temp_dir) / 'v.db', app_dir=CLI_DIR)
        try:
            check('look_at_screen' in eng._tool_registry(), 'look_at_screen registered when vision_enabled')
        finally:
            eng.close()
        eng2 = MaicaEngine(config=dict(base, vision_enabled=False), db_path=Path(temp_dir) / 'v2.db', app_dir=CLI_DIR)
        try:
            check('look_at_screen' not in eng2._tool_registry(), 'look_at_screen absent when vision disabled')
        finally:
            eng2.close()


def test_idle_self_action() -> None:
    sys.path.insert(0, str(CLI_DIR))
    from config_defaults import DEFAULT_CONFIG
    from spire_topics import choose_spire_topic
    from mfocus import build_spire_messages
    from store import Store

    class RNG:
        def random(self) -> float:
            return 0.0  # always under the probability -> pick self_action

        def choice(self, seq):
            return seq[0]

    with tempfile.TemporaryDirectory() as temp_dir:
        store = Store(Path(temp_dir) / 's.db')
        config = dict(DEFAULT_CONFIG)
        config.update({
            'idle_self_actions_enabled': True, 'agent_tools_enabled': True, 'file_tools_enabled': True,
            'idle_self_action_probability': 0.5, 'spire_wikipedia_enabled': False,
            'language': 'en', 'response_planner_enabled': False, 'style_enabled': False,
            'embedding_enabled': False, 'memory_embedding_enabled': False, 'embedding_service_enabled': False,
        })
        try:
            topic = choose_spire_topic(store, config, '', rng=RNG())
            check(topic['mode'] == 'self_action', 'idle should choose self_action when enabled')
            # Gate: without the opt-in it must NOT choose self_action.
            off = dict(config, idle_self_actions_enabled=False)
            check(choose_spire_topic(store, off, '', rng=RNG())['mode'] != 'self_action',
                  'self_action must be gated by the opt-in')
            messages, _plan = build_spire_messages(store, config, None, topic_hint='', topic_mode='self_action', topic_id='self_action')
            all_text = ' '.join(str(m.get('content') or '') for m in messages)
            check('diary' in all_text and 'letter' in all_text,
                  'self_action prompt should invite writing a diary or a letter')
        finally:
            store.close()


def test_engine_agent_loop() -> None:
    sys.path.insert(0, str(CLI_DIR))
    from config_defaults import DEFAULT_CONFIG
    from engine import MaicaEngine

    class FakeAgentClient:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tools=None, overrides=None):
            self.calls += 1
            if self.calls == 1:
                system_text = ' '.join(str(item.get('content') or '') for item in messages if item.get('role') == 'system')
                tool_names = {
                    str((item.get('function') or {}).get('name') or '')
                    for item in (tools or [])
                }
                assert 'get_file_space_info' in tool_names, 'agent must receive the filesystem boundary tool'
                assert 'never infer or invent' in system_text.lower(), 'agent must receive the no-guess path policy'
                return {
                    'message': {
                        'role': 'assistant',
                        'content': '',
                        'tool_calls': [
                            {'id': 'c1', 'function': {'name': 'check_our_closeness', 'arguments': '{}'}}
                        ],
                    },
                    'usage': {'total_tokens': 5},
                }
            # Second call: the tool result is now in the conversation.
            tool_msgs = [m for m in messages if m.get('role') == 'tool']
            assert tool_msgs and 'affection' in tool_msgs[-1]['content'], 'tool result must be fed back'
            return {
                'message': {'role': 'assistant', 'content': json.dumps(
                    {'text': 'We are closer than ever.', 'emotion': 'smile', 'action': {}}
                )},
                'usage': {'total_tokens': 6},
            }

    with tempfile.TemporaryDirectory(prefix='maica-agent-') as temp_dir:
        config = dict(DEFAULT_CONFIG)
        config.update(
            {
                'api_key_required': False,
                'jsonl_logs_enabled': False,
                'auto_backup_enabled': False,
                'mfocus_mode': 'rule',
                'mtrigger_mode': 'rule',
                'style_enabled': False,
                'metadata_extract_enabled': False,
                'embedding_enabled': False,
                'memory_embedding_enabled': False,
                'embedding_service_enabled': False,
                'agent_tools_enabled': True,
                'file_tools_enabled': True,
                'sandbox_root': str(Path(temp_dir) / 'Monika'),
            }
        )
        engine = MaicaEngine(config=config, db_path=Path(temp_dir) / 'agent.db', app_dir=CLI_DIR)
        engine._agent_client_override = FakeAgentClient()
        events: list[dict[str, Any]] = []
        try:
            result = engine.chat('how close are we?', event_callback=events.append)
            check(result['ok'], result.get('error', 'agent chat failed'))
            check(result['text'] == 'We are closer than ever.', 'agent loop final reply mismatch')
            trace = result['mfocus_plan'].get('agent_trace') or []
            check(len(trace) == 1 and trace[0]['tool'] == 'check_our_closeness',
                  'agent loop must record the tool call')
            check('affection' in trace[0]['output'], 'tool output must include affection')
            tool_events = [event for event in events if str(event.get('kind') or '').startswith('tool.')]
            check([event['kind'] for event in tool_events] == ['tool.started', 'tool.finished'],
                  'agent runtime should expose bounded tool lifecycle events')
            check('output' not in tool_events[-1].get('payload', {}),
                  'tool runtime events must not expose complete private tool output')
        finally:
            engine.close()


def test_llm_provider_resolution() -> None:
    sys.path.insert(0, str(CLI_DIR))
    from engine import resolve_llm_provider

    config = {
        'api_base': 'https://api.deepseek.com/v1', 'api_key': 'sk-ds', 'model': 'deepseek-chat',
        'llm_call_mode': 'split',
        'agent_api_base': 'https://openrouter.ai/api/v1', 'agent_api_key': 'sk-or', 'agent_model': 'moonshotai/kimi-k2.6',
    }
    chat = resolve_llm_provider(config, 'chat')
    check(chat['model'] == 'deepseek-chat', 'chat role must use the main model')
    agent = resolve_llm_provider(config, 'agent')
    check(agent['model'] == 'moonshotai/kimi-k2.6', 'split agent role must use the agent model')

    unified = dict(config, llm_call_mode='unified')
    check(resolve_llm_provider(unified, 'agent')['model'] == 'deepseek-chat',
          'unified mode must use the main model for every role')

    no_agent = dict(config, agent_api_base='', agent_model='')
    check(resolve_llm_provider(no_agent, 'agent')['model'] == 'deepseek-chat',
          'split agent role must fall back to main when agent provider is unset')


def test_safe_config_exposes_settings() -> None:
    sys.path.insert(0, str(GUI_DIR))
    sys.path.insert(0, str(CLI_DIR))
    from engine_worker import GuiEngineWorker

    worker = GuiEngineWorker()
    safe = worker._safe_config({
        'agent_tools_enabled': True, 'file_tools_enabled': True, 'sandbox_root': '/x',
        'sandbox_readonly_allowlist': ['/a'], 'llm_call_mode': 'split',
        'agent_api_base': 'b', 'agent_model': 'm', 'agent_api_key': 'sk-secret',
        'avatar_backend': 'embedded_live2d', 'live2d_model_path': '/models/a.model3.json',
        'live2d_core_path': '/sdk/live2dcubismcore.min.js', 'live2d_render_fps': 60,
    })
    # Settings that round-trip to the GUI must be in the safe snapshot, or the
    # dialog re-renders them to defaults after Save (the checkbox-reverts bug).
    for key in ('agent_tools_enabled', 'file_tools_enabled', 'sandbox_root',
                'sandbox_readonly_allowlist', 'llm_call_mode', 'agent_api_base', 'agent_model',
                'avatar_backend', 'live2d_model_path', 'live2d_core_path', 'live2d_render_fps'):
        check(key in safe, f'safe config must expose settings key: {key}')
    check(safe.get('agent_api_key') == '<hidden>', 'agent API key must stay hidden in snapshots')


def test_settings_api_key() -> None:
    script = (
        "import sys;"
        f"sys.path.insert(0, {str(GUI_DIR)!r});"
        "from PySide6.QtCore import QObject, Signal;"
        "from PySide6.QtWidgets import QApplication, QLineEdit, QWidget;"
        "import gui_app;"
        "app=QApplication([]);"
        "owner=type('O',(QWidget,),{'config_save_requested':Signal(dict)})();"
        "dlg=gui_app.SettingsDialog(owner);"
        "dlg.render({'api_base':'b','model':'m','api_key':'sk-secret','llm_call_mode':'split','agent_api_key':'sk-agent'});"
        "assert dlg.api_key.echoMode()==QLineEdit.EchoMode.Password,'key field must be masked';"
        "assert dlg.agent_api_key.echoMode()==QLineEdit.EchoMode.Password,'agent key field must be masked';"
        "assert dlg.api_key.text()=='' and dlg.agent_api_key.text()=='','stored keys must not be shown';"
        "cap={};"
        "owner.config_save_requested.connect(lambda u: cap.update(u));"
        "dlg.save();"
        "assert 'api_key' not in cap and 'agent_api_key' not in cap,'blank keys must not be sent (keep existing)';"
        "assert cap.get('llm_call_mode')=='split','call mode must be saved';"
        "cap.clear();"
        "dlg.api_key.setText(' sk-new '); dlg.agent_api_key.setText(' sk-agent2 ');"
        "dlg.agent_tools_enabled.setChecked(True); dlg.file_tools_enabled.setChecked(True);"
        "dlg.sandbox_allowlist.setPlainText('/a\\n/b');"
        "dlg.save();"
        "assert cap.get('api_key')=='sk-new','typed key must be sent, stripped';"
        "assert cap.get('agent_api_key')=='sk-agent2','typed agent key must be sent, stripped';"
        "assert cap.get('agent_tools_enabled') is True and cap.get('file_tools_enabled') is True,'tool enables must be saved';"
        "assert cap.get('sandbox_readonly_allowlist')==['/a','/b'],'allowlist must parse to a list';"
        "print('OK')"
    )
    env = os.environ.copy()
    env['QT_QPA_PLATFORM'] = 'offscreen'
    completed = subprocess.run(
        [sys.executable, '-c', script],
        cwd=str(ROOT_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
    )
    check(completed.returncode == 0 and 'OK' in completed.stdout, completed.stderr or completed.stdout)


def test_player_substitution() -> None:
    sys.path.insert(0, str(CLI_DIR))
    from example_bank import replace_player_word

    class NamedStore:
        def get_nicknames(self):
            return ['babe']

        def get_profile(self):
            return {'player_name': 'Chopin'}

    class DefaultStore:
        def get_nicknames(self):
            return []

        def get_profile(self):
            return {'player_name': 'player'}

    store = NamedStore()
    check(replace_player_word('thinking about you, player.', store) == 'thinking about you, babe.',
          'bare "player" should become the display name')
    check(replace_player_word("that's the player's choice", store) == "that's the babe's choice",
          "possessive player's should become the display name")
    check(replace_player_word('I fixed your music player and the video player.', store)
          == 'I fixed your music player and the video player.',
          'compound nouns like "music player" must be left alone')
    check(replace_player_word('hi player', DefaultStore()) == 'hi player',
          'no substitution when no real name is set')


def test_eval_offline() -> None:
    eval_dir = CLI_DIR / 'eval'
    for path in (str(CLI_DIR), str(eval_dir)):
        if path not in sys.path:
            sys.path.insert(0, path)
    import run_eval

    outcome = run_eval.run_evaluation(offline=True, save=False)
    summary = outcome['summary']
    check(summary['scored'] == summary['total'] and summary['total'] > 0,
          'offline eval should score every scenario')
    check(set(summary['by_dimension'].keys()) == set(run_eval.DIMENSION_KEYS),
          'offline eval summary must cover every rubric dimension')
    check('CHARACTER-FIDELITY SCORECARD' in outcome['scorecard'],
          'offline eval should render a scorecard')
    check(outcome['saved_path'] == '', 'offline eval must not write a results file')

    subset = run_eval.run_evaluation(offline=True, subset='comfort', save=False)
    cats = {r['scenario']['category'] for r in subset['records']}
    check(cats == {'comfort'}, 'subset filter should keep only the requested category')


def main() -> int:
    compile_python()
    print('compile_python ok')
    validate_json()
    print('validate_json ok')
    test_utf8_sources()
    print('utf8_sources ok')
    test_launchers_exist()
    print('launchers_exist ok')
    test_diagnostics()
    print('diagnostics ok')
    test_embedding_service_help()
    print('embedding_service_help ok')
    test_avatar_helpers()
    print('avatar_helpers ok')
    test_live2d_model_import()
    print('live2d_model_import ok')
    test_text_helpers()
    print('text_helpers ok')
    test_speech_pipeline()
    print('speech_pipeline ok')
    test_engine_fake_chat()
    print('engine_fake_chat ok')
    test_engine_fake_stream()
    print('engine_fake_stream ok')
    test_runtime_cancellation()
    print('runtime_cancellation ok')
    test_engine_language_rewrite()
    print('engine_language_rewrite ok')
    test_prompt_language_systems()
    print('prompt_language_systems ok')
    test_dual_example_banks()
    print('dual_example_banks ok')
    test_context_translation_cache()
    print('context_translation_cache ok')
    test_player_substitution()
    print('player_substitution ok')
    test_llm_provider_resolution()
    print('llm_provider_resolution ok')
    test_sandbox_path_safety()
    print('sandbox_path_safety ok')
    test_file_tools()
    print('file_tools ok')
    test_engine_agent_loop()
    print('engine_agent_loop ok')
    test_screen_vision()
    print('screen_vision ok')
    test_idle_self_action()
    print('idle_self_action ok')
    test_safe_config_exposes_settings()
    print('safe_config_exposes_settings ok')
    test_settings_api_key()
    print('settings_api_key ok')
    test_eval_offline()
    print('eval_offline ok')
    test_package_audit()
    print('package_audit ok')
    test_gui_offscreen()
    print('gui_offscreen ok')
    test_gui_safe_offscreen()
    print('gui_safe_offscreen ok')
    print('smoke_tests ok')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
