# -*- coding: utf-8 -*-
"""Lightweight STT adapters for the MAICA GUI."""

from __future__ import annotations

import subprocess
from typing import Any


class WindowsSpeechSTT:
    """Small Windows dictation wrapper using PowerShell and System.Speech."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def listen(self) -> dict[str, Any]:
        timeout = int(self.config.get('stt_timeout', 8) or 8)
        timeout = max(2, min(30, timeout))
        language = str(self.config.get('stt_language') or self.config.get('language') or 'en').lower()
        culture = 'zh-CN' if language.startswith('zh') else 'en-US'
        script = (
            "Add-Type -AssemblyName System.Speech;"
            f"$culture=[System.Globalization.CultureInfo]::GetCultureInfo('{culture}');"
            "$recognizer=New-Object System.Speech.Recognition.SpeechRecognitionEngine $culture;"
            "$grammar=New-Object System.Speech.Recognition.DictationGrammar;"
            "$recognizer.LoadGrammar($grammar);"
            "$recognizer.SetInputToDefaultAudioDevice();"
            f"$result=$recognizer.Recognize([TimeSpan]::FromSeconds({timeout}));"
            "if($null -ne $result){[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;Write-Output $result.Text};"
            "$recognizer.Dispose();"
        )
        creation_flags = 0
        if hasattr(subprocess, 'CREATE_NO_WINDOW'):
            creation_flags = subprocess.CREATE_NO_WINDOW
        process = subprocess.run(
            [
                'powershell',
                '-NoProfile',
                '-ExecutionPolicy',
                'Bypass',
                '-Command',
                script,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=creation_flags,
            timeout=timeout + 8,
        )
        if process.returncode != 0:
            return {'ok': False, 'text': '', 'error': (process.stderr or process.stdout or '').strip()}
        text = (process.stdout or '').strip()
        if not text:
            return {'ok': False, 'text': '', 'error': 'No speech recognized.'}
        return {'ok': True, 'text': text, 'error': ''}


class NullSTT:
    def listen(self) -> dict[str, Any]:
        return {'ok': False, 'text': '', 'error': 'STT provider is disabled.'}


def create_stt(config: dict[str, Any]) -> WindowsSpeechSTT | NullSTT:
    provider = str(config.get('stt_provider') or 'windows_speech').lower()
    if provider == 'windows_speech':
        return WindowsSpeechSTT(config)
    return NullSTT()
