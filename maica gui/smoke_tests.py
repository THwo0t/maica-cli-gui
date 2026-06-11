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
    for path in (CLI_DIR / 'config.example.json', ROOT_DIR / 'maica cli factory' / 'config.example.json'):
        with path.open('r', encoding='utf-8-sig') as handle:
            json.load(handle)


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
    from stt import create_stt
    from tts import clean_tts_text
    from diagnostics import collect_report

    assert clean_tts_text('(smiles) I missed you. [debug] hidden') == 'I missed you.'
    assert clean_tts_text('（轻轻握住你的手）I am here.') == 'I am here.'
    result = create_stt({'stt_provider': 'off'}).listen()
    check(not result['ok'], 'Null STT should not recognize speech')
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
    script = (
        "import sys;"
        "from pathlib import Path;"
        "from PySide6.QtCore import QTimer;"
        "from PySide6.QtWidgets import QApplication;"
        f"sys.path.insert(0, {str(GUI_DIR)!r});"
        "from gui_app import MainWindow;"
        "app=QApplication([]);"
        "window=MainWindow();"
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
    script = (
        "import sys;"
        "from pathlib import Path;"
        "from PySide6.QtCore import QTimer;"
        "from PySide6.QtWidgets import QApplication;"
        f"sys.path.insert(0, {str(GUI_DIR)!r});"
        "from gui_app import GUI_DIR, MainWindow;"
        "app=QApplication([]);"
        "safe_dir=GUI_DIR/'.safe_test';"
        "safe_dir.mkdir(parents=True, exist_ok=True);"
        "window=MainWindow(db_path=safe_dir/'maica_cli_test.db', safe_test_mode=True);"
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


def main() -> int:
    compile_python()
    print('compile_python ok')
    validate_json()
    print('validate_json ok')
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
