# -*- coding: utf-8 -*-
"""Lightweight TTS adapters for the MAICA GUI."""

from __future__ import annotations

import json
import platform
import re
import shlex
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
GUI_DIR = Path(__file__).resolve().parent
TTS_CACHE_DIR = GUI_DIR / '.tts_cache'


META_LINE_PREFIXES = (
    'emotion:',
    'action:',
    'debug:',
    '[debug]',
    '[mtrigger',
    'mtrigger',
    'system:',
    'assistant:',
    'user:',
    'json:',
)


def redact_secret(text: str, *secrets: str) -> str:
    """Strip API keys / bearer tokens from text before it is surfaced to the UI.

    WebSocket handshake failures from websocket-client can echo the request
    headers (including ``Authorization: Bearer <key>``), so any error string
    derived from an exception must pass through here.
    """
    out = str(text or '')
    for secret in secrets:
        secret = str(secret or '').strip()
        if secret and len(secret) >= 6:
            out = out.replace(secret, '***')
    out = re.sub(r'(?i)(authorization\s*:\s*bearer\s+)\S+', r'\1***', out)
    out = re.sub(r'(?i)(bearer)\s+\S+', r'\1 ***', out)
    out = re.sub(r'(?i)(sk-)[A-Za-z0-9._\-]{6,}', r'\1***', out)
    out = re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password)\s*[=:]\s*)[A-Za-z0-9._\-]{6,}', r'\1***', out)
    return out


def compact_tts_error(text: str, *secrets: str) -> str:
    """Return a short, safe TTS error suitable for the chat UI."""
    safe = redact_secret(text, *secrets).strip()
    lowered = safe.lower()
    if 'invalidapikey' in lowered or 'invalid api-key' in lowered or '401 unauthorized' in lowered:
        return 'Bailian API key is invalid or expired. Please update tts_bailian_api_key in local config.json.'
    if 'tts_bailian_api_key is empty' in lowered:
        return 'tts_bailian_api_key is empty. Please set it in local config.json.'
    if 'tts_bailian_voice is empty' in lowered:
        return 'tts_bailian_voice is empty. Please set a Bailian voice id in local config.json.'
    if 'websocket-client is required' in lowered:
        return 'websocket-client is required for Bailian CosyVoice TTS.'
    if len(safe) > 220:
        safe = safe[:217].rstrip() + '...'
    return safe or 'Unknown TTS error.'


def clean_tts_text(text: str) -> str:
    """Remove stage directions and metadata before sending text to TTS."""
    cleaned_lines: list[str] = []
    for raw_line in str(text or '').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if any(lowered.startswith(prefix) for prefix in META_LINE_PREFIXES):
            continue
        cleaned_lines.append(line)
    text = ' '.join(cleaned_lines)
    text = re.split(r'\[(?:debug|mtrigger[^\]]*)\]', text, maxsplit=1, flags=re.I)[0]

    # Remove common stage directions: (smiles), （轻轻握住你的手）, [emotion], *sighs*.
    paired_patterns = [
        r'\([^()\n]{0,160}\)',
        r'（[^（）\n]{0,160}）',
        r'\[[^\[\]\n]{0,160}\]',
        r'【[^【】\n]{0,160}】',
        r'\{[^{}\n]{0,160}\}',
        r'\*[^*\n]{0,160}\*',
    ]
    for pattern in paired_patterns:
        text = re.sub(pattern, ' ', text)

    # Remove leaked code-fence or response-format fragments if a model misbehaves.
    text = re.sub(r'```(?:json)?|```', ' ', text, flags=re.I)
    text = re.sub(r'\b(?:emotion|action|segments|metadata)\s*[:=]\s*["\']?[\w -]{0,40}["\']?', ' ', text, flags=re.I)
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'\s+([,.!?;:])', r'\1', text)
    return text


class WindowsSapiTTS:
    """Small Windows SAPI wrapper using PowerShell and System.Speech.

    This keeps the GUI dependency-light. Later TTS providers can implement the
    same speak/stop interface.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.process: subprocess.Popen[str] | None = None
        self.lock = threading.Lock()
        self.last_error = ''

    def speak(self, text: str) -> None:
        clean_text = clean_tts_text(text)
        if not clean_text:
            return
        if platform.system().lower() != 'windows':
            self.last_error = 'Windows SAPI TTS is only available on Windows. Use Bailian CosyVoice on this OS.'
            return
        try:
            self.stop()
            thread = threading.Thread(target=self._speak_blocking, args=(clean_text,), daemon=True)
            thread.start()
        except Exception as exc:
            self.last_error = compact_tts_error(str(exc), self.config.get('tts_bailian_api_key'), self.config.get('stt_bailian_api_key'))
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

    def synthesize_file(self, text: str, cancel_token: Any | None = None) -> Path:
        clean_text = clean_tts_text(text)
        if not clean_text:
            raise RuntimeError('TTS text is empty after metadata cleanup')
        if platform.system().lower() != 'windows':
            raise RuntimeError('Windows SAPI TTS is only available on Windows')
        TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        target = TTS_CACHE_DIR / f'sapi-{uuid.uuid4()}.wav'
        rate = max(-10, min(10, int(self.config.get('tts_rate', 0) or 0)))
        volume = max(0, min(100, int(self.config.get('tts_volume', 90) or 90)))
        voice = str(self.config.get('tts_voice') or '').strip()
        voice_line = f"$s.SelectVoice('{voice.replace(chr(39), chr(39) * 2)}');" if voice else ''
        safe_path = str(target).replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Speech;"
            "$text=[Console]::In.ReadToEnd();"
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"$s.Rate={rate};$s.Volume={volume};{voice_line}"
            f"$s.SetOutputToWaveFile('{safe_path}');"
            "$s.Speak($text);$s.Dispose();"
        )
        creation_flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        process = subprocess.Popen(
            ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=creation_flags,
        )
        with self.lock:
            self.process = process
        if cancel_token is not None:
            cancel_token.add_cancel_callback(self.stop)
        try:
            _stdout, stderr = process.communicate(clean_text)
            if cancel_token is not None:
                cancel_token.check()
            if process.returncode not in (0, None) or not target.exists():
                raise RuntimeError(f'Windows SAPI synthesis failed: {(stderr or "").strip() or process.returncode}')
            return target
        finally:
            if cancel_token is not None:
                cancel_token.remove_cancel_callback(self.stop)
            with self.lock:
                if self.process is process:
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
        except Exception as exc:
            self.last_error = compact_tts_error(str(exc), self.config.get('tts_bailian_api_key'), self.config.get('stt_bailian_api_key'))
            return
        finally:
            with self.lock:
                self.process = None


class SubprocessAudioPlayer:
    """Cross-platform audio playback through a small child process."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.process: subprocess.Popen[str] | None = None
        self.lock = threading.Lock()
        self.last_error = ''

    def stop(self) -> None:
        with self.lock:
            process = self.process
            self.process = None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
        except Exception:
            pass

    def play(self, audio_path: Path, is_current: Any | None = None) -> None:
        self.last_error = ''
        backend = str(self.config.get('tts_playback_backend') or 'auto').strip().lower()
        if backend in {'off', 'none', 'null'}:
            return
        command = self._command(audio_path)
        if not command:
            self.last_error = (
                'No audio playback backend found. Install ffmpeg (ffplay) or mpv, '
                'or set tts_playback_backend in settings.'
            )
            return
        creation_flags = 0
        if hasattr(subprocess, 'CREATE_NO_WINDOW'):
            creation_flags = subprocess.CREATE_NO_WINDOW
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creation_flags,
            )
        except Exception as exc:
            self.last_error = compact_tts_error(str(exc), self.config.get('tts_bailian_api_key'), self.config.get('stt_bailian_api_key'))
            return
        with self.lock:
            self.process = process
        try:
            stdout, stderr = process.communicate()
            if is_current is not None and not is_current():
                return
            if process.returncode not in (0, None):
                detail = (stderr or stdout or '').strip()
                self.last_error = compact_tts_error(f'audio playback failed: {detail or process.returncode}', self.config.get('tts_bailian_api_key'), self.config.get('stt_bailian_api_key'))
        finally:
            with self.lock:
                if self.process is process:
                    self.process = None

    def _command(self, audio_path: Path) -> list[str]:
        custom = str(self.config.get('tts_playback_command') or '').strip()
        if custom:
            return shlex.split(custom) + [str(audio_path)]

        backend = str(self.config.get('tts_playback_backend') or 'auto').strip().lower()
        if backend in {'', 'auto'}:
            for candidate in self._auto_candidates(audio_path):
                command = self._command_for_backend(candidate, audio_path)
                if command:
                    return command
            return []
        if backend in {'off', 'none', 'null'}:
            return []
        return self._command_for_backend(backend, audio_path)

    def _auto_candidates(self, audio_path: Path) -> list[str]:
        system = platform.system().lower()
        if system == 'windows':
            return ['powershell', 'pwsh', 'ffplay', 'mpv']
        if system == 'darwin':
            return ['afplay', 'ffplay', 'mpv']
        candidates = ['ffplay', 'mpv']
        if audio_path.suffix.lower() == '.wav':
            candidates.extend(['paplay', 'aplay'])
        return candidates

    def _command_for_backend(self, backend: str, audio_path: Path) -> list[str]:
        suffix = audio_path.suffix.lower()
        if backend == 'ffplay':
            exe = shutil.which('ffplay')
            return [exe, '-nodisp', '-autoexit', '-loglevel', 'quiet', str(audio_path)] if exe else []
        if backend == 'mpv':
            exe = shutil.which('mpv')
            return [exe, '--no-video', '--really-quiet', str(audio_path)] if exe else []
        if backend == 'afplay':
            exe = shutil.which('afplay')
            return [exe, str(audio_path)] if exe else []
        if backend == 'paplay' and suffix == '.wav':
            exe = shutil.which('paplay')
            return [exe, str(audio_path)] if exe else []
        if backend == 'aplay' and suffix == '.wav':
            exe = shutil.which('aplay')
            return [exe, str(audio_path)] if exe else []
        if backend in {'powershell', 'pwsh'}:
            exe = shutil.which('powershell') if backend == 'powershell' else shutil.which('pwsh')
            if not exe:
                return []
            safe_path = str(audio_path).replace("'", "''")
            script = (
                "Add-Type -AssemblyName PresentationCore;"
                f"$path='{safe_path}';"
                "$p=New-Object System.Windows.Media.MediaPlayer;"
                "$p.Open([System.Uri]::new($path));"
                "$p.Volume=1.0;"
                "$p.Play();"
                "$deadline=(Get-Date).AddSeconds(60);"
                "while((-not $p.NaturalDuration.HasTimeSpan) -and ((Get-Date) -lt $deadline)){Start-Sleep -Milliseconds 80};"
                "if($p.NaturalDuration.HasTimeSpan){"
                "$ms=[int]$p.NaturalDuration.TimeSpan.TotalMilliseconds + 350;"
                "Start-Sleep -Milliseconds $ms"
                "}else{Start-Sleep -Seconds 8};"
                "$p.Stop();"
                "$p.Close();"
            )
            return [exe, '-Sta', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script]
        return []


class BailianCosyVoiceTTS:
    """Aliyun Bailian CosyVoice WebSocket TTS provider.

    The provider synthesizes into a local audio file, then plays it in a child
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
        self.audio_player = SubprocessAudioPlayer(config)

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

    def synthesize_file(self, text: str, cancel_token: Any | None = None) -> Path:
        clean_text = self._clean_text(text)
        if not clean_text:
            raise RuntimeError('TTS text is empty after metadata cleanup')
        with self.lock:
            self.generation += 1
            generation = self.generation
        if cancel_token is not None:
            cancel_token.add_cancel_callback(self.stop)
        try:
            path = self._synthesize_to_audio(clean_text, generation)
            if cancel_token is not None:
                cancel_token.check()
            if path is None:
                raise RuntimeError('Bailian CosyVoice returned no audio')
            return path
        finally:
            if cancel_token is not None:
                cancel_token.remove_cancel_callback(self.stop)

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
        self.audio_player.stop()
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _speak_blocking(self, text: str, generation: int) -> None:
        try:
            audio_path = self._synthesize_to_audio(text, generation)
            if audio_path is None or not self._is_current(generation):
                return
            self._play_audio(audio_path, generation)
        except Exception as exc:
            self.last_error = compact_tts_error(str(exc), self.config.get('tts_bailian_api_key'))

    def _synthesize_to_wav(self, text: str, generation: int) -> Path | None:
        """Compatibility helper for older smoke tests."""
        return self._synthesize_to_audio(text, generation, forced_format='wav')

    def _synthesize_to_audio(
        self,
        text: str,
        generation: int,
        forced_format: str | None = None,
    ) -> Path | None:
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
            audio_format = self._audio_format(forced_format)
            ws.send(json.dumps(self._run_task_payload(task_id, model, voice, audio_format), ensure_ascii=False))
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
            audio_path = TTS_CACHE_DIR / f'cosyvoice-{task_id}.{audio_format}'
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

    def _run_task_payload(self, task_id: str, model: str, voice: str, audio_format: str) -> dict[str, Any]:
        instruction = str(self.config.get('tts_bailian_instruction') or '').strip()
        parameters: dict[str, Any] = {
            'text_type': 'PlainText',
            'voice': voice,
            'format': audio_format,
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

    def _play_audio(self, audio_path: Path, generation: int) -> None:
        self.audio_player.play(audio_path, is_current=lambda: self._is_current(generation))
        if self.audio_player.last_error:
            raise RuntimeError(self.audio_player.last_error)

    def _audio_format(self, forced_format: str | None = None) -> str:
        raw_format = forced_format or str(self.config.get('tts_bailian_format') or 'mp3')
        audio_format = raw_format.strip().lower()
        if audio_format not in {'mp3', 'wav'}:
            audio_format = 'mp3'
        return audio_format

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
        return clean_tts_text(text)


class SystemSayTTS:
    """Local offline TTS for macOS (`say`) and Linux (`spd-say` / `espeak-ng`).

    This is the cross-platform counterpart of WindowsSapiTTS: it both
    synthesizes and speaks in one child process, so it needs no separate audio
    playback backend and no network or API key.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.process: subprocess.Popen[str] | None = None
        self.lock = threading.Lock()
        self.last_error = ''

    def speak(self, text: str) -> None:
        clean_text = clean_tts_text(text)
        if not clean_text:
            return
        command = self._command(clean_text)
        if not command:
            self.last_error = (
                'No system TTS found. On macOS use `say`; on Linux install '
                'speech-dispatcher (spd-say) or espeak-ng, or use Bailian CosyVoice.'
            )
            return
        try:
            self.stop()
            thread = threading.Thread(target=self._speak_blocking, args=(command,), daemon=True)
            thread.start()
        except Exception as exc:
            self.last_error = compact_tts_error(str(exc), self.config.get('tts_bailian_api_key'), self.config.get('stt_bailian_api_key'))

    def stop(self) -> None:
        with self.lock:
            process = self.process
            self.process = None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
        except Exception:
            pass

    def synthesize_file(self, text: str, cancel_token: Any | None = None) -> Path:
        clean_text = clean_tts_text(text)
        if not clean_text:
            raise RuntimeError('TTS text is empty after metadata cleanup')
        TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        system = platform.system().lower()
        if system == 'darwin' and shutil.which('say'):
            target = TTS_CACHE_DIR / f'say-{uuid.uuid4()}.aiff'
            rate = max(120, min(360, 180 + int(self.config.get('tts_rate', 0) or 0) * 12))
            command = [str(shutil.which('say')), '-r', str(rate), '-o', str(target), clean_text]
        else:
            exe = shutil.which('espeak-ng') or shutil.which('espeak')
            if not exe:
                raise RuntimeError('File-based system TTS needs say, espeak-ng, or espeak')
            target = TTS_CACHE_DIR / f'espeak-{uuid.uuid4()}.wav'
            command = [exe, '-w', str(target), clean_text]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        with self.lock:
            self.process = process
        if cancel_token is not None:
            cancel_token.add_cancel_callback(self.stop)
        try:
            _stdout, stderr = process.communicate()
            if cancel_token is not None:
                cancel_token.check()
            if process.returncode not in (0, None) or not target.exists():
                raise RuntimeError(f'system TTS synthesis failed: {(stderr or "").strip() or process.returncode}')
            return target
        finally:
            if cancel_token is not None:
                cancel_token.remove_cancel_callback(self.stop)
            with self.lock:
                if self.process is process:
                    self.process = None

    def _speak_blocking(self, command: list[str]) -> None:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            with self.lock:
                self.process = process
            _stdout, stderr = process.communicate()
            with self.lock:
                still_current = self.process is process
            if still_current and process.returncode not in (0, None):
                self.last_error = compact_tts_error(f'system TTS failed: {(stderr or "").strip() or process.returncode}', self.config.get('tts_bailian_api_key'), self.config.get('stt_bailian_api_key'))
        except Exception as exc:
            self.last_error = compact_tts_error(str(exc), self.config.get('tts_bailian_api_key'), self.config.get('stt_bailian_api_key'))
        finally:
            with self.lock:
                if self.process is process:
                    self.process = None

    def _command(self, text: str) -> list[str]:
        system = platform.system().lower()
        if system == 'darwin':
            exe = shutil.which('say')
            if not exe:
                return []
            rate = int(self.config.get('tts_rate', 0) or 0)  # -10..10 -> wpm offset
            words_per_min = max(120, min(360, 180 + rate * 12))
            return [exe, '-r', str(words_per_min), text]
        # Linux / other POSIX
        spd = shutil.which('spd-say')
        if spd:
            # -w waits for completion so the process lifetime matches playback.
            return [spd, '-w', text]
        for name in ('espeak-ng', 'espeak'):
            exe = shutil.which(name)
            if exe:
                return [exe, text]
        return []


class NullTTS:
    last_error = ''

    def speak(self, text: str) -> None:
        return

    def stop(self) -> None:
        return

    def synthesize_file(self, text: str, cancel_token: Any | None = None) -> Path:
        raise RuntimeError('TTS is disabled')


def resolve_tts_provider(config: dict[str, Any]) -> str:
    """Resolve 'auto' (and empty) to a provider that works on this OS."""
    provider = str(config.get('tts_provider') or 'auto').lower()
    if provider not in {'auto', ''}:
        return provider
    system = platform.system().lower()
    if system == 'windows':
        return 'windows_sapi'
    # macOS / Linux: prefer a local system voice; Bailian stays an explicit opt-in.
    return 'system_say'


def create_tts(config: dict[str, Any]) -> WindowsSapiTTS | BailianCosyVoiceTTS | SystemSayTTS | NullTTS:
    provider = resolve_tts_provider(config)
    if provider == 'windows_sapi':
        return WindowsSapiTTS(config)
    if provider in {'bailian_cosyvoice', 'aliyun_bailian', 'cosyvoice'}:
        return BailianCosyVoiceTTS(config)
    if provider in {'system_say', 'say', 'espeak', 'spd-say'}:
        return SystemSayTTS(config)
    return NullTTS()
