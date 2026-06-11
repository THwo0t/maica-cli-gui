# -*- coding: utf-8 -*-
"""Lightweight TTS adapters for the MAICA GUI."""

from __future__ import annotations

import json
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
GUI_DIR = Path(__file__).resolve().parent
TTS_CACHE_DIR = GUI_DIR / '.tts_cache'


class WindowsSapiTTS:
    """Small Windows SAPI wrapper using PowerShell and System.Speech.

    This keeps the GUI dependency-light. Later TTS providers can implement the
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


class BailianCosyVoiceTTS:
    """Aliyun Bailian CosyVoice WebSocket TTS provider.

    The provider synthesizes into a local WAV file, then plays it in a child
    PowerShell process. Keeping both network synthesis and playback out of the
    Qt main thread prevents the GUI from freezing while Monika speaks.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.process: subprocess.Popen[str] | None = None
        self.websocket: Any | None = None
        self.lock = threading.Lock()
        self.generation = 0
        self.last_error = ''

    def speak(self, text: str) -> None:
        clean_text = self._clean_text(text)
        if not clean_text:
            return
        self.stop()
        with self.lock:
            self.generation += 1
            generation = self.generation
        thread = threading.Thread(target=self._speak_blocking, args=(clean_text, generation), daemon=True)
        thread.start()

    def stop(self) -> None:
        with self.lock:
            self.generation += 1
            process = self.process
            ws = self.websocket
            self.process = None
            self.websocket = None
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
            except Exception:
                pass
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _speak_blocking(self, text: str, generation: int) -> None:
        try:
            audio_path = self._synthesize_to_wav(text, generation)
            if audio_path is None or not self._is_current(generation):
                return
            self._play_wav(audio_path, generation)
        except Exception as exc:
            self.last_error = str(exc)

    def _synthesize_to_wav(self, text: str, generation: int) -> Path | None:
        try:
            import websocket
        except Exception as exc:
            raise RuntimeError('websocket-client is required for Bailian CosyVoice TTS') from exc

        api_key = str(self.config.get('tts_bailian_api_key') or '').strip()
        if not api_key:
            raise RuntimeError('tts_bailian_api_key is empty')

        endpoint = str(
            self.config.get('tts_bailian_endpoint')
            or 'wss://dashscope.aliyuncs.com/api-ws/v1/inference'
        ).strip()
        model = str(self.config.get('tts_bailian_model') or 'cosyvoice-v3.5-plus').strip()
        voice = str(self.config.get('tts_bailian_voice') or '').strip()
        if not voice:
            raise RuntimeError('tts_bailian_voice is empty')

        task_id = str(uuid.uuid4())
        timeout = float(self.config.get('tts_bailian_timeout', 30) or 30)
        headers = [
            f'Authorization: Bearer {api_key}',
            'User-Agent: maica-gui',
        ]
        ws = websocket.create_connection(endpoint, header=headers, timeout=timeout)
        with self.lock:
            if not self._is_current_unlocked(generation):
                ws.close()
                return None
            self.websocket = ws

        audio_chunks: list[bytes] = []
        try:
            ws.send(json.dumps(self._run_task_payload(task_id, model, voice), ensure_ascii=False))
            started = False
            finished = False
            while self._is_current(generation) and not finished:
                frame = ws.recv()
                if isinstance(frame, bytes):
                    audio_chunks.append(frame)
                    continue

                event = self._event_name(frame)
                if event == 'task-started' and not started:
                    started = True
                    ws.send(json.dumps(self._continue_task_payload(task_id, text), ensure_ascii=False))
                    ws.send(json.dumps(self._finish_task_payload(task_id), ensure_ascii=False))
                elif event == 'task-finished':
                    finished = True
                elif event == 'task-failed':
                    raise RuntimeError(self._event_error(frame))

            if not audio_chunks or not self._is_current(generation):
                return None

            TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            audio_path = TTS_CACHE_DIR / 'last_tts.wav'
            audio_path.write_bytes(b''.join(audio_chunks))
            return audio_path
        finally:
            with self.lock:
                if self.websocket is ws:
                    self.websocket = None
            try:
                ws.close()
            except Exception:
                pass

    def _run_task_payload(self, task_id: str, model: str, voice: str) -> dict[str, Any]:
        instruction = str(self.config.get('tts_bailian_instruction') or '').strip()
        parameters: dict[str, Any] = {
            'text_type': 'PlainText',
            'voice': voice,
            'format': 'wav',
            'sample_rate': int(self.config.get('tts_bailian_sample_rate', 22050) or 22050),
            'volume': int(self.config.get('tts_bailian_volume', 50) or 50),
            'rate': float(self.config.get('tts_bailian_rate', 1.0) or 1.0),
            'pitch': float(self.config.get('tts_bailian_pitch', 1.0) or 1.0),
            'enable_ssml': False,
        }
        if instruction:
            parameters['instruction'] = instruction[:100]
        return {
            'header': {
                'action': 'run-task',
                'task_id': task_id,
                'streaming': 'duplex',
            },
            'payload': {
                'task_group': 'audio',
                'task': 'tts',
                'function': 'SpeechSynthesizer',
                'model': model,
                'parameters': parameters,
                'input': {},
            },
        }

    def _continue_task_payload(self, task_id: str, text: str) -> dict[str, Any]:
        return {
            'header': {
                'action': 'continue-task',
                'task_id': task_id,
                'streaming': 'duplex',
            },
            'payload': {'input': {'text': text}},
        }

    def _finish_task_payload(self, task_id: str) -> dict[str, Any]:
        return {
            'header': {
                'action': 'finish-task',
                'task_id': task_id,
                'streaming': 'duplex',
            },
            'payload': {'input': {}},
        }

    def _play_wav(self, audio_path: Path, generation: int) -> None:
        safe_path = str(audio_path).replace("'", "''")
        script = (
            f"$p=New-Object System.Media.SoundPlayer '{safe_path}';"
            "$p.Load();"
            "$p.PlaySync();"
        )
        creation_flags = 0
        if hasattr(subprocess, 'CREATE_NO_WINDOW'):
            creation_flags = subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen(
            [
                'powershell',
                '-NoProfile',
                '-ExecutionPolicy',
                'Bypass',
                '-Command',
                script,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            creationflags=creation_flags,
        )
        with self.lock:
            if not self._is_current_unlocked(generation):
                try:
                    process.terminate()
                except Exception:
                    pass
                return
            self.process = process
        try:
            process.wait()
        finally:
            with self.lock:
                if self.process is process:
                    self.process = None

    def _is_current(self, generation: int) -> bool:
        with self.lock:
            return self._is_current_unlocked(generation)

    def _is_current_unlocked(self, generation: int) -> bool:
        return self.generation == generation

    def _event_name(self, frame: str) -> str:
        try:
            data = json.loads(frame)
        except Exception:
            return ''
        header = data.get('header') if isinstance(data, dict) else {}
        if not isinstance(header, dict):
            return ''
        return str(header.get('event') or '')

    def _event_error(self, frame: str) -> str:
        try:
            data = json.loads(frame)
        except Exception:
            return 'Bailian CosyVoice task failed'
        header = data.get('header') if isinstance(data, dict) else {}
        payload = data.get('payload') if isinstance(data, dict) else {}
        code = header.get('error_code') if isinstance(header, dict) else ''
        message = header.get('error_message') if isinstance(header, dict) else ''
        if isinstance(payload, dict):
            code = code or payload.get('code') or ''
            message = message or payload.get('message') or ''
        return f'Bailian CosyVoice task failed: {code} {message}'.strip()

    def _clean_text(self, text: str) -> str:
        return ' '.join(line.strip() for line in text.splitlines() if line.strip())


class NullTTS:
    def speak(self, text: str) -> None:
        return

    def stop(self) -> None:
        return


def create_tts(config: dict[str, Any]) -> WindowsSapiTTS | BailianCosyVoiceTTS | NullTTS:
    provider = str(config.get('tts_provider') or 'windows_sapi').lower()
    if provider == 'windows_sapi':
        return WindowsSapiTTS(config)
    if provider in {'bailian_cosyvoice', 'aliyun_bailian', 'cosyvoice'}:
        return BailianCosyVoiceTTS(config)
    return NullTTS()
