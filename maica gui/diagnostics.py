#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local diagnostics for MAICA GUI.

The report is designed for troubleshooting and GitHub issues. It never prints
API keys, database rows, memories, logs, or full private config contents.
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
GUI_DIR = Path(__file__).resolve().parent
CLI_DIR = ROOT_DIR / 'maica cli'
ASSET_DIR = ROOT_DIR / 'maica gui assets' / 'runtime'
CONFIG_PATH = CLI_DIR / 'config.json'
DB_PATH = CLI_DIR / 'maica_cli.db'

SAFE_CONFIG_KEYS = (
    'api_base',
    'model',
    'language',
    'mfocus_mode',
    'mtrigger_mode',
    'show_debug',
    'embedding_enabled',
    'memory_embedding_enabled',
    'embedding_service_enabled',
    'embedding_service_autostart',
    'embedding_service_host',
    'embedding_service_port',
    'embedding_service_timeout',
    'gui_disable_thread_embeddings',
    'gui_background_mode',
    'tts_enabled',
    'tts_provider',
    'tts_bailian_model',
    'tts_bailian_format',
    'stt_provider',
    'stt_language',
    'stt_timeout',
)

SECRET_KEYS = {
    'api_key',
    'tts_bailian_api_key',
    'access_token',
    'refresh_token',
    'database_reset_password',
}
SECRET_MARKERS = ('secret', 'password')
KNOWN_GH_PATH = Path('C:/Program Files/GitHub CLI/gh.exe')


def command_output(args: list[str], timeout: int = 5) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            args,
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
        )
        return {
            'ok': completed.returncode == 0,
            'returncode': completed.returncode,
            'stdout': completed.stdout.strip(),
            'stderr': completed.stderr.strip(),
        }
    except Exception as exc:
        return {'ok': False, 'error': repr(exc)}


def resolve_command(name: str) -> str:
    if name == 'gh' and KNOWN_GH_PATH.exists():
        return str(KNOWN_GH_PATH)
    return name


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def path_status(path: Path) -> dict[str, Any]:
    exists = path.exists()
    return {
        'exists': exists,
        'is_dir': path.is_dir() if exists else False,
        'size': path.stat().st_size if exists and path.is_file() else None,
    }


def safe_config_snapshot() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {'exists': False}
    try:
        with CONFIG_PATH.open('r', encoding='utf-8-sig') as handle:
            config = json.load(handle)
    except Exception as exc:
        return {'exists': True, 'load_error': repr(exc)}

    snapshot: dict[str, Any] = {'exists': True, 'safe_values': {}, 'secret_fields_present': []}
    for key in SAFE_CONFIG_KEYS:
        if key in config:
            snapshot['safe_values'][key] = config.get(key)
    if 'tts_bailian_voice' in config:
        snapshot['safe_values']['tts_bailian_voice_set'] = bool(config.get('tts_bailian_voice'))
    for key, value in config.items():
        lowered = str(key).lower()
        if lowered in SECRET_KEYS or any(marker in lowered for marker in SECRET_MARKERS):
            snapshot['secret_fields_present'].append({'key': key, 'has_value': bool(value)})
    return snapshot


def git_snapshot() -> dict[str, Any]:
    branch = command_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD'])
    commit = command_output(['git', 'rev-parse', '--short', 'HEAD'])
    status = command_output(['git', 'status', '--short'])
    return {
        'branch': branch.get('stdout') if branch.get('ok') else None,
        'commit': commit.get('stdout') if commit.get('ok') else None,
        'dirty': bool(status.get('stdout')) if status.get('ok') else None,
    }


def gh_auth_snapshot() -> dict[str, Any]:
    result = command_output([resolve_command('gh'), 'auth', 'status'], timeout=10)
    return {
        'ok': bool(result.get('ok')),
        'returncode': result.get('returncode'),
        'available': 'error' not in result,
    }


def collect_report() -> dict[str, Any]:
    return {
        'app': 'MAICA CLI GUI',
        'diagnostics_version': '0.11.4',
        'python': {
            'executable': sys.executable,
            'version': sys.version,
            'platform': platform.platform(),
            'cwd': str(ROOT_DIR),
        },
        'environment': {
            'qt_qpa_platform': os.environ.get('QT_QPA_PLATFORM', ''),
        },
        'git': git_snapshot(),
        'paths': {
            'config': path_status(CONFIG_PATH),
            'database': path_status(DB_PATH),
            'gui_dir': path_status(GUI_DIR),
            'cli_dir': path_status(CLI_DIR),
            'asset_runtime_dir': path_status(ASSET_DIR),
            'asset_manifest': path_status(ASSET_DIR / 'manifest.json'),
            'example_config': path_status(CLI_DIR / 'config.example.json'),
        },
        'modules': {
            'PySide6': module_available('PySide6'),
            'requests': module_available('requests'),
            'websocket': module_available('websocket'),
            'numpy': module_available('numpy'),
            'torch': module_available('torch'),
            'transformers': module_available('transformers'),
            'sentence_transformers': module_available('sentence_transformers'),
            'faiss': module_available('faiss'),
        },
        'tools': {
            'python_version': command_output([sys.executable, '--version']),
            **(
                {'powershell': command_output(['powershell', '-NoProfile', '-Command', '$PSVersionTable.PSVersion.ToString()'])}
                if platform.system().lower() == 'windows'
                else {}
            ),
            'audio_playback': {
                'ffplay': bool(shutil.which('ffplay')),
                'mpv': bool(shutil.which('mpv')),
                'afplay': bool(shutil.which('afplay')),
                'paplay': bool(shutil.which('paplay')),
                'aplay': bool(shutil.which('aplay')),
            },
            'system_tts': {
                'say': bool(shutil.which('say')),
                'spd-say': bool(shutil.which('spd-say')),
                'espeak-ng': bool(shutil.which('espeak-ng')),
                'espeak': bool(shutil.which('espeak')),
            },
            'gh_auth': gh_auth_snapshot(),
        },
        'config': safe_config_snapshot(),
    }


def main() -> int:
    report = collect_report()
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
