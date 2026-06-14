# -*- coding: utf-8 -*-
"""Lightweight STT adapters for the MAICA GUI."""

from __future__ import annotations

import json
import platform
import subprocess
import time
import uuid
from typing import Any


class WindowsSpeechSTT:
    """Small Windows dictation wrapper using PowerShell and System.Speech."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def listen(self) -> dict[str, Any]:
        if platform.system().lower() != 'windows':
            return {
                'ok': False,
                'text': '',
                'error': 'Windows Speech STT is only available on Windows. '
                         'Set stt_provider to off on this OS, or use a cross-platform STT.',
            }
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


class DashScopeParaformerSTT:
    """Aliyun Bailian (DashScope) Paraformer real-time ASR over WebSocket.

    Cross-platform: captures the microphone with sounddevice, streams 16 kHz
    mono PCM to DashScope, and returns the recognized text. Reuses the same
    DashScope endpoint and (optionally) the same API key as Bailian CosyVoice
    TTS, so users only configure one key.
    """

    SAMPLE_RATE = 16000
    CHUNK_MS = 100

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def _api_key(self) -> str:
        # Fall back to the TTS Bailian key so a single key serves both features.
        return str(
            self.config.get('stt_bailian_api_key')
            or self.config.get('tts_bailian_api_key')
            or ''
        ).strip()

    def _language_hints(self) -> list[str]:
        language = str(self.config.get('stt_language') or self.config.get('language') or 'en').lower()
        if language.startswith('zh'):
            return ['zh']
        if language.startswith('en'):
            return ['en']
        return [language[:2]] if language else ['en']

    def _record(self, seconds: int) -> tuple[bytes, str]:
        try:
            import numpy  # noqa: F401
            import sounddevice as sd
        except Exception:
            return b'', (
                'sounddevice + numpy are required to capture the microphone for Paraformer STT. '
                'Install them in the GUI environment: pip install sounddevice numpy '
                '(Linux also needs the PortAudio system library, e.g. libportaudio2).'
            )
        try:
            frames = sd.rec(
                int(seconds * self.SAMPLE_RATE),
                samplerate=self.SAMPLE_RATE,
                channels=1,
                dtype='int16',
            )
            sd.wait()
        except Exception as exc:
            return b'', f'microphone capture failed: {exc}'
        return bytes(frames.tobytes()), ''

    def listen(self) -> dict[str, Any]:
        try:
            import websocket
        except Exception:
            return {'ok': False, 'text': '', 'error': 'websocket-client is required for Bailian Paraformer STT.'}

        api_key = self._api_key()
        if not api_key:
            return {'ok': False, 'text': '', 'error': 'stt_bailian_api_key (or tts_bailian_api_key) is empty.'}

        seconds = max(2, min(30, int(self.config.get('stt_timeout', 8) or 8)))
        audio, record_error = self._record(seconds)
        if record_error:
            return {'ok': False, 'text': '', 'error': record_error}
        if not audio:
            return {'ok': False, 'text': '', 'error': 'No audio captured.'}

        endpoint = str(
            self.config.get('stt_bailian_endpoint')
            or self.config.get('tts_bailian_endpoint')
            or 'wss://dashscope.aliyuncs.com/api-ws/v1/inference'
        ).strip()
        model = str(self.config.get('stt_bailian_model') or 'paraformer-realtime-v2').strip()
        task_id = str(uuid.uuid4())
        net_timeout = float(self.config.get('stt_bailian_timeout', 15) or 15)

        try:
            ws = websocket.create_connection(
                endpoint,
                header=[f'Authorization: Bearer {api_key}', 'User-Agent: maica-gui'],
                timeout=net_timeout,
            )
        except Exception as exc:
            return {'ok': False, 'text': '', 'error': f'Paraformer connection failed: {exc}'}

        try:
            ws.send(json.dumps(self._run_task_payload(task_id, model), ensure_ascii=False))
            if not self._wait_for(ws, 'task-started', net_timeout):
                return {'ok': False, 'text': '', 'error': 'Paraformer task did not start.'}

            chunk_bytes = int(self.SAMPLE_RATE * 2 * self.CHUNK_MS / 1000)  # int16 mono
            for start in range(0, len(audio), chunk_bytes):
                ws.send_binary(audio[start:start + chunk_bytes])
            ws.send(json.dumps(self._finish_task_payload(task_id), ensure_ascii=False))

            return self._collect_text(ws, net_timeout)
        except Exception as exc:
            return {'ok': False, 'text': '', 'error': f'Paraformer recognition failed: {exc}'}
        finally:
            try:
                ws.close()
            except Exception:
                pass

    def _run_task_payload(self, task_id: str, model: str) -> dict[str, Any]:
        return {
            'header': {'action': 'run-task', 'task_id': task_id, 'streaming': 'duplex'},
            'payload': {
                'task_group': 'audio',
                'task': 'asr',
                'function': 'recognition',
                'model': model,
                'parameters': {
                    'format': 'pcm',
                    'sample_rate': self.SAMPLE_RATE,
                    'language_hints': self._language_hints(),
                    'disfluency_removal_enabled': False,
                },
                'input': {},
            },
        }

    def _finish_task_payload(self, task_id: str) -> dict[str, Any]:
        return {
            'header': {'action': 'finish-task', 'task_id': task_id, 'streaming': 'duplex'},
            'payload': {'input': {}},
        }

    def _event_name(self, frame: Any) -> str:
        if not isinstance(frame, str):
            return ''
        try:
            data = json.loads(frame)
        except Exception:
            return ''
        header = data.get('header') if isinstance(data, dict) else {}
        return str(header.get('event') or '') if isinstance(header, dict) else ''

    def _wait_for(self, ws: Any, event: str, timeout: float) -> bool:
        ws.settimeout(timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            frame = ws.recv()
            name = self._event_name(frame)
            if name == event:
                return True
            if name == 'task-failed':
                raise RuntimeError(self._frame_error(frame))
        return False

    def _collect_text(self, ws: Any, timeout: float) -> dict[str, Any]:
        ws.settimeout(timeout)
        sentences: list[str] = []
        last_partial = ''
        deadline = time.time() + timeout + 10
        while time.time() < deadline:
            frame = ws.recv()
            if isinstance(frame, bytes):
                continue
            name = self._event_name(frame)
            if name == 'result-generated':
                text, ended = self._extract_sentence(frame)
                if text:
                    last_partial = text
                    if ended:
                        sentences.append(text)
                        last_partial = ''
            elif name == 'task-finished':
                break
            elif name == 'task-failed':
                return {'ok': False, 'text': '', 'error': self._frame_error(frame)}
        if last_partial:
            sentences.append(last_partial)
        text = ' '.join(s.strip() for s in sentences if s.strip()).strip()
        if not text:
            return {'ok': False, 'text': '', 'error': 'No speech recognized.'}
        return {'ok': True, 'text': text, 'error': ''}

    def _extract_sentence(self, frame: str) -> tuple[str, bool]:
        try:
            data = json.loads(frame)
        except Exception:
            return '', False
        output = (data.get('payload') or {}).get('output') or {}
        sentence = output.get('sentence') if isinstance(output, dict) else {}
        if not isinstance(sentence, dict):
            return '', False
        return str(sentence.get('text') or ''), bool(sentence.get('sentence_end'))

    def _frame_error(self, frame: str) -> str:
        try:
            data = json.loads(frame)
        except Exception:
            return 'Paraformer task failed.'
        header = data.get('header') if isinstance(data, dict) else {}
        if isinstance(header, dict):
            code = header.get('error_code') or ''
            message = header.get('error_message') or ''
            return f'Paraformer task failed: {code} {message}'.strip()
        return 'Paraformer task failed.'


class NullSTT:
    def listen(self) -> dict[str, Any]:
        return {'ok': False, 'text': '', 'error': 'STT provider is disabled.'}


def resolve_stt_provider(config: dict[str, Any]) -> str:
    """Resolve 'auto' (and empty) to a provider that works on this OS."""
    provider = str(config.get('stt_provider') or 'auto').lower()
    if provider in {'auto', ''}:
        # No cross-platform local STT yet, so only Windows has a usable engine.
        return 'windows_speech' if platform.system().lower() == 'windows' else 'off'
    return provider


def create_stt(config: dict[str, Any]) -> WindowsSpeechSTT | DashScopeParaformerSTT | NullSTT:
    provider = resolve_stt_provider(config)
    if provider == 'windows_speech':
        return WindowsSpeechSTT(config)
    if provider in {'bailian_paraformer', 'paraformer', 'dashscope_paraformer'}:
        return DashScopeParaformerSTT(config)
    return NullSTT()
