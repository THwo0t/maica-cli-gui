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
        GUI_DIR / 'engine_worker.py',
        GUI_DIR / 'gui_app.py',
        GUI_DIR / 'diagnostics.py',
        GUI_DIR / 'package_audit.py',
        GUI_DIR / 'stt.py',
        GUI_DIR / 'tts.py',
        GUI_DIR / 'maica_gui.spec',
        CLI_DIR / 'config_defaults.py',
        CLI_DIR / 'embedding_service.py',
        CLI_DIR / 'embedding_service_client.py',
        CLI_DIR / 'mfocus.py',
        CLI_DIR / 'response_planner.py',
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
        try:
            result = engine.chat('hello', stream_callback=chunks.append)
            check(result['ok'], result.get('error', 'fake stream failed'))
            check(result.get('streamed') is True, 'fake stream did not mark streamed')
            check(chunks == ['[smile] Hello', ' there.'], 'stream chunks mismatch')
            check(result['text'] == 'Hello there.', 'stream parsed text mismatch')
            check(result['emotion'] == 'smile', 'stream emotion mismatch')
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
    test_text_helpers()
    print('text_helpers ok')
    test_engine_fake_chat()
    print('engine_fake_chat ok')
    test_engine_fake_stream()
    print('engine_fake_stream ok')
    test_engine_language_rewrite()
    print('engine_language_rewrite ok')
    test_prompt_language_systems()
    print('prompt_language_systems ok')
    test_dual_example_banks()
    print('dual_example_banks ok')
    test_context_translation_cache()
    print('context_translation_cache ok')
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
