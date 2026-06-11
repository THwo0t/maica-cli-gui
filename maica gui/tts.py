# -*- coding: utf-8 -*-
"""Lightweight TTS adapters for the MAICA GUI."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]


class WindowsSapiTTS:
    """Small Windows SAPI wrapper using PowerShell and System.Speech.

    This keeps v0.8.2 dependency-light. Later TTS providers can implement the
    same speak/stop interface.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.process: subprocess.Popen[str] | None = None
        self.lock = threading.Lock()

    def speak(self, text: str) -> None:
        clean_text = text.strip()
        if not clean_text:
            return
        try:
            self.stop()
            thread = threading.Thread(target=self._speak_blocking, args=(clean_text,), daemon=True)
            thread.start()
        except Exception:
            return

    def stop(self) -> None:
        with self.lock:
            if self.process is None:
                return
            try:
                if self.process.poll() is None:
                    self.process.terminate()
            except Exception:
                pass
            self.process = None

    def _speak_blocking(self, text: str) -> None:
        rate = int(self.config.get('tts_rate', 0) or 0)
        volume = int(self.config.get('tts_volume', 90) or 90)
        voice = str(self.config.get('tts_voice') or '').strip()
        rate = max(-10, min(10, rate))
        volume = max(0, min(100, volume))

        voice_line = ''
        if voice:
            safe_voice = voice.replace("'", "''")
            voice_line = f"$s.SelectVoice('{safe_voice}');"

        script = (
            "Add-Type -AssemblyName System.Speech;"
            "$text=[Console]::In.ReadToEnd();"
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"$s.Rate={rate};"
            f"$s.Volume={volume};"
            f"{voice_line}"
            "$s.Speak($text);"
        )
        creation_flags = 0
        if hasattr(subprocess, 'CREATE_NO_WINDOW'):
            creation_flags = subprocess.CREATE_NO_WINDOW
        try:
            process = subprocess.Popen(
                [
                    'powershell',
                    '-NoProfile',
                    '-ExecutionPolicy',
                    'Bypass',
                    '-Command',
                    script,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                creationflags=creation_flags,
            )
            with self.lock:
                self.process = process
            if process.stdin is not None:
                try:
                    process.stdin.write(text)
                    process.stdin.close()
                except Exception:
                    pass
            process.wait()
        except Exception:
            return
        finally:
            with self.lock:
                self.process = None


class NullTTS:
    def speak(self, text: str) -> None:
        return

    def stop(self) -> None:
        return


def create_tts(config: dict[str, Any]) -> WindowsSapiTTS | NullTTS:
    provider = str(config.get('tts_provider') or 'windows_sapi').lower()
    if provider == 'windows_sapi':
        return WindowsSapiTTS(config)
    return NullTTS()
