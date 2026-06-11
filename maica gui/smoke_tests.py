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
        GUI_DIR / 'stt.py',
        GUI_DIR / 'tts.py',
        CLI_DIR / 'config_defaults.py',
        CLI_DIR / 'embedding_service.py',
        CLI_DIR / 'embedding_service_client.py',
        CLI_DIR / 'mfocus.py',
        CLI_DIR / 'response_planner.py',
        CLI_DIR / 'example_bank.py',
    ]
    for path in files:
        py_compile.compile(str(path), doraise=True)


def validate_json() -> None:
    for path in (CLI_DIR / 'config.example.json', ROOT_DIR / 'maica cli factory' / 'config.example.json'):
        with path.open('r', encoding='utf-8-sig') as handle:
            json.load(handle)


def test_launchers_exist() -> None:
    for name in ('run_gui.ps1', 'run_cli.ps1', 'run_smoke_tests.ps1'):
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

    assert clean_tts_text('(smiles) I missed you. [debug] hidden') == 'I missed you.'
    assert clean_tts_text('（轻轻握住你的手）I am here.') == 'I am here.'
    result = create_stt({'stt_provider': 'off'}).listen()
    check(not result['ok'], 'Null STT should not recognize speech')


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
    test_gui_offscreen()
    print('gui_offscreen ok')
    print('smoke_tests ok')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
