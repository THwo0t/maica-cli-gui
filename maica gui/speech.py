# -*- coding: utf-8 -*-
"""Event-driven speech synthesis, ordered playback, and audio-level tracking."""

from __future__ import annotations

import array
import concurrent.futures
import math
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import (
    QAudioBuffer,
    QAudioBufferOutput,
    QAudioFormat,
    QAudioOutput,
    QMediaPlayer,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
CLI_DIR = ROOT_DIR / 'maica cli'
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from language_runtime import conforms_to_language  # noqa: E402
from runtime_events import CancellationToken, TurnCancelled  # noqa: E402
from tts import clean_tts_text, compact_tts_error, create_tts  # noqa: E402


@dataclass(frozen=True)
class AudioArtifact:
    path: Path
    text: str
    sequence: int


class SentenceSegmenter:
    """Incrementally split Chinese and English dialogue without losing tails."""

    def __init__(self) -> None:
        self.buffer = ''

    def feed(self, text: str, final: bool = False) -> list[str]:
        self.buffer += str(text or '')
        ready: list[str] = []
        start = 0
        index = 0
        while index < len(self.buffer):
            char = self.buffer[index]
            boundary = char in '。！？!?\n'
            if char == '.' and (index + 1 == len(self.buffer) or self.buffer[index + 1].isspace()):
                boundary = True
            if char == '…' and index + 1 < len(self.buffer) and self.buffer[index + 1] == '…':
                index += 1
                boundary = True
            if boundary:
                segment = self.buffer[start:index + 1].strip()
                if segment:
                    ready.append(segment)
                start = index + 1
            index += 1
        self.buffer = self.buffer[start:]
        if final and self.buffer.strip():
            ready.append(self.buffer.strip())
            self.buffer = ''
        return ready


class QtAudioPlayer(QObject):
    started = Signal(str)
    amplitude = Signal(float)
    finished = Signal(str)
    failed = Signal(str, str)

    def __init__(self, sensitivity: float = 1.0, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Audio backends can block while probing PipeWire/PulseAudio. Delay all
        # multimedia objects until the first real playback request.
        self.player: QMediaPlayer | None = None
        self.output: QAudioOutput | None = None
        self.buffer_output: QAudioBufferOutput | None = None
        self.sensitivity = max(0.1, float(sensitivity or 1.0))
        self.current_path = ''
        self._started = False
        self._smooth_level = 0.0

    def configure(self, sensitivity: float) -> None:
        self.sensitivity = max(0.1, float(sensitivity or 1.0))

    def play(self, path: str | Path) -> None:
        self.stop(emit_finished=False)
        self._ensure_player()
        if self.player is None:
            self.failed.emit(str(path), 'Qt audio player could not be initialized')
            return
        self.current_path = str(Path(path).resolve())
        self._started = False
        self._smooth_level = 0.0
        self.player.setSource(QUrl.fromLocalFile(self.current_path))
        self.player.play()

    def stop(self, emit_finished: bool = False) -> None:
        path = self.current_path
        if self.player is not None:
            self.player.stop()
        self.current_path = ''
        self._started = False
        self._smooth_level = 0.0
        self.amplitude.emit(0.0)
        if emit_finished and path:
            self.finished.emit(path)

    def close(self) -> None:
        self.stop()
        if self.player is not None:
            self.player.setSource(QUrl())
            self.player.deleteLater()
        if self.output is not None:
            self.output.deleteLater()
        if self.buffer_output is not None:
            self.buffer_output.deleteLater()
        self.player = None
        self.output = None
        self.buffer_output = None

    def _ensure_player(self) -> None:
        if self.player is not None:
            return
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.buffer_output = QAudioBufferOutput(self)
        self.player.setAudioOutput(self.output)
        self.player.setAudioBufferOutput(self.buffer_output)
        self.player.playbackStateChanged.connect(self._on_playback_state)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.player.errorOccurred.connect(self._on_error)
        self.buffer_output.audioBufferReceived.connect(self._on_buffer)

    def _on_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState and self.current_path and not self._started:
            self._started = True
            self.started.emit(self.current_path)

    def _on_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status != QMediaPlayer.MediaStatus.EndOfMedia or not self.current_path:
            return
        path = self.current_path
        self.current_path = ''
        self._started = False
        self._smooth_level = 0.0
        self.amplitude.emit(0.0)
        self.finished.emit(path)

    def _on_error(self, _error: QMediaPlayer.Error, text: str) -> None:
        if not self.current_path:
            return
        path = self.current_path
        self.current_path = ''
        self._started = False
        self.amplitude.emit(0.0)
        self.failed.emit(path, compact_tts_error(text or 'Qt audio playback failed'))

    def _on_buffer(self, buffer: QAudioBuffer) -> None:
        level = self.buffer_rms(buffer)
        # Fast attack and gentler release keep the mouth responsive but stable.
        alpha = 0.68 if level > self._smooth_level else 0.32
        self._smooth_level += (level - self._smooth_level) * alpha
        self.amplitude.emit(max(0.0, min(1.0, self._smooth_level * self.sensitivity)))

    @staticmethod
    def buffer_rms(buffer: QAudioBuffer) -> float:
        try:
            raw = bytes(buffer.data())
            sample_format = buffer.format().sampleFormat()
        except Exception:
            return 0.0
        if not raw:
            return 0.0
        try:
            if sample_format == QAudioFormat.SampleFormat.UInt8:
                values = ((value - 128) / 128.0 for value in raw[:8192])
            elif sample_format == QAudioFormat.SampleFormat.Int16:
                samples = array.array('h')
                samples.frombytes(raw[:8192 - (len(raw[:8192]) % 2)])
                values = (value / 32768.0 for value in samples)
            elif sample_format == QAudioFormat.SampleFormat.Int32:
                samples = array.array('i')
                samples.frombytes(raw[:8192 - (len(raw[:8192]) % 4)])
                values = (value / 2147483648.0 for value in samples)
            elif sample_format == QAudioFormat.SampleFormat.Float:
                samples = array.array('f')
                samples.frombytes(raw[:8192 - (len(raw[:8192]) % 4)])
                values = (float(value) for value in samples)
            else:
                return 0.0
            total = 0.0
            count = 0
            for value in values:
                if not math.isfinite(value):
                    continue
                total += value * value
                count += 1
            return math.sqrt(total / count) if count else 0.0
        except Exception:
            return 0.0


@dataclass
class SpeechSession:
    turn_id: str
    generation: int
    token: CancellationToken
    segmenter: SentenceSegmenter = field(default_factory=SentenceSegmenter)
    raw_text: str = ''
    queued_text: list[str] = field(default_factory=list)
    ready: dict[int, AudioArtifact] = field(default_factory=dict)
    failed: dict[int, str] = field(default_factory=dict)
    next_sequence: int = 0
    next_play: int = 0
    pending: int = 0
    input_finished: bool = False
    playing: bool = False
    blocked: bool = False


@dataclass
class DeferredSpeech:
    turn_id: str
    raw_text: str = ''
    final_text: str = ''
    input_finished: bool = False


class SpeechController(QObject):
    event = Signal(dict)
    _synthesis_done = Signal(int, int, str, str, str)

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        parent: QObject | None = None,
        provider_factory: Callable[[dict[str, Any]], Any] = create_tts,
        player: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = dict(config or {})
        self._provider_factory = provider_factory
        self.player = player or QtAudioPlayer(float(self.config.get('lip_sync_sensitivity', 1.0)), self)
        self.player.started.connect(self._on_audio_started)
        self.player.amplitude.connect(self._on_audio_amplitude)
        self.player.finished.connect(self._on_audio_finished)
        self.player.failed.connect(self._on_audio_failed)
        self._synthesis_done.connect(self._on_synthesis_done)
        self._max_workers = self._configured_workers(self.config)
        self._executor = self._new_executor(self._max_workers)
        self._generation = 0
        self._session: SpeechSession | None = None
        self._deferred: dict[str, DeferredSpeech] = {}
        self._lock = threading.Lock()

    def configure(self, config: dict[str, Any]) -> None:
        next_workers = self._configured_workers(config)
        if next_workers != self._max_workers:
            self.cancel('speech concurrency changed', quiet=True)
            previous = self._executor
            self._max_workers = next_workers
            self._executor = self._new_executor(next_workers)
            previous.shutdown(wait=False, cancel_futures=True)
        self.config = dict(config)
        self.player.configure(float(self.config.get('lip_sync_sensitivity', 1.0)))

    def begin(self, turn_id: str) -> None:
        turn_id = str(turn_id)
        behavior = str(self.config.get('speech_queue_behavior') or 'replace').strip().lower()
        if self._session is not None and behavior == 'queue':
            self._deferred.setdefault(turn_id, DeferredSpeech(turn_id))
            return
        self.cancel('replaced by a new turn', quiet=True)
        self._begin_now(turn_id)

    def _begin_now(self, turn_id: str) -> None:
        self._generation += 1
        session = SpeechSession(str(turn_id), self._generation, CancellationToken())
        with self._lock:
            self._session = session
        self._emit('speech.started', {'turn_id': session.turn_id})

    def append_text(self, turn_id: str, text: str) -> None:
        session = self._current(turn_id)
        if session is None:
            deferred = self._deferred.get(str(turn_id))
            if deferred is not None:
                deferred.raw_text += str(text or '')
            return
        if not self.config.get('speech_streaming_enabled', True):
            return
        session.raw_text += str(text or '')
        if session.raw_text.lstrip().startswith('{'):
            session.blocked = True
            return
        for segment in session.segmenter.feed(text):
            self._queue_if_safe(session, segment)

    def finish(self, turn_id: str, final_text: str) -> None:
        session = self._current(turn_id)
        if session is None:
            deferred = self._deferred.get(str(turn_id))
            if deferred is not None:
                deferred.final_text = str(final_text or '')
                deferred.input_finished = True
                return
            self.begin(turn_id)
            session = self._current(turn_id)
        if session is None:
            return
        clean_final = clean_tts_text(final_text)
        clean_stream = clean_tts_text(session.raw_text)
        if session.blocked or (clean_stream and self._normalise(clean_stream) != self._normalise(clean_final)):
            self._restart_with_final(session, clean_final)
            return
        for segment in session.segmenter.feed('', final=True):
            self._queue_if_safe(session, segment)
        queued = self._normalise(' '.join(session.queued_text))
        if not queued and clean_final:
            for segment in SentenceSegmenter().feed(clean_final, final=True):
                self._queue_if_safe(session, segment)
        session.input_finished = True
        self._maybe_finish(session)

    def cancel(self, reason: str = 'cancelled', quiet: bool = False) -> None:
        self._deferred.clear()
        with self._lock:
            session = self._session
            self._session = None
        if session is None:
            self.player.stop()
            return
        session.token.cancel(reason)
        self.player.stop()
        self._delete_artifacts(session)
        if not quiet:
            self._emit('speech.cancelled', {'turn_id': session.turn_id, 'reason': reason})

    def close(self) -> None:
        self.cancel('shutdown', quiet=True)
        self._executor.shutdown(wait=False, cancel_futures=True)
        close = getattr(self.player, 'close', None)
        if callable(close):
            close()

    def _restart_with_final(self, session: SpeechSession, final_text: str) -> None:
        turn_id = session.turn_id
        self.cancel('final dialogue replaced the streamed draft', quiet=True)
        self.begin(turn_id)
        replacement = self._current(turn_id)
        if replacement is None:
            return
        for segment in SentenceSegmenter().feed(final_text, final=True):
            self._queue_if_safe(replacement, segment)
        replacement.input_finished = True
        self._maybe_finish(replacement)

    def _queue_if_safe(self, session: SpeechSession, segment: str) -> None:
        clean = clean_tts_text(segment)
        if not clean:
            return
        language = str(self.config.get('language') or 'en')
        if not conforms_to_language(clean, language):
            session.blocked = True
            return
        sequence = session.next_sequence
        session.next_sequence += 1
        session.pending += 1
        session.queued_text.append(clean)
        generation = session.generation
        token = session.token
        config = dict(self.config)

        def synthesize() -> None:
            path = ''
            error = ''
            try:
                token.check()
                provider = self._provider_factory(config)
                path = str(provider.synthesize_file(clean, token))
            except TurnCancelled:
                error = 'cancelled'
            except Exception as exc:
                error = compact_tts_error(
                    str(exc),
                    config.get('tts_bailian_api_key', ''),
                    config.get('stt_bailian_api_key', ''),
                )
            self._synthesis_done.emit(generation, sequence, clean, path, error)

        self._executor.submit(synthesize)

    def _on_synthesis_done(self, generation: int, sequence: int, text: str, path: str, error: str) -> None:
        session = self._session
        if session is None or generation != session.generation:
            if path:
                Path(path).unlink(missing_ok=True)
            return
        session.pending = max(0, session.pending - 1)
        if error:
            session.failed[sequence] = error
        elif path:
            artifact = AudioArtifact(Path(path), text, sequence)
            session.ready[sequence] = artifact
            self._emit('speech.segment_ready', {'turn_id': session.turn_id, 'sequence': sequence, 'text': text})
        self._play_next(session)
        self._maybe_finish(session)

    def _play_next(self, session: SpeechSession) -> None:
        if session.playing:
            return
        while session.next_play in session.failed:
            error = session.failed.pop(session.next_play)
            session.next_play += 1
            if error != 'cancelled':
                self._emit('speech.failed', {'turn_id': session.turn_id, 'error': error})
        artifact = session.ready.pop(session.next_play, None)
        if artifact is None:
            return
        session.playing = True
        self.player.play(artifact.path)

    def _on_audio_started(self, path: str) -> None:
        session = self._session
        if session is not None:
            self._emit('audio.started', {'turn_id': session.turn_id, 'path': Path(path).name})

    def _on_audio_amplitude(self, value: float) -> None:
        session = self._session
        if session is not None:
            self._emit('audio.amplitude', {'turn_id': session.turn_id, 'value': float(value)})

    def _on_audio_finished(self, path: str) -> None:
        Path(path).unlink(missing_ok=True)
        session = self._session
        if session is None:
            return
        self._emit('audio.finished', {'turn_id': session.turn_id})
        session.playing = False
        session.next_play += 1
        self._play_next(session)
        self._maybe_finish(session)

    def _on_audio_failed(self, path: str, error: str) -> None:
        Path(path).unlink(missing_ok=True)
        session = self._session
        if session is None:
            return
        session.playing = False
        session.next_play += 1
        self._emit('speech.failed', {'turn_id': session.turn_id, 'error': error})
        self._play_next(session)
        self._maybe_finish(session)

    def _maybe_finish(self, session: SpeechSession) -> None:
        if self._session is not session or not session.input_finished:
            return
        if session.pending or session.playing or session.ready:
            return
        while session.next_play in session.failed:
            error = session.failed.pop(session.next_play)
            session.next_play += 1
            if error != 'cancelled':
                self._emit('speech.failed', {'turn_id': session.turn_id, 'error': error})
        if session.next_play < session.next_sequence:
            return
        self._emit('speech.finished', {'turn_id': session.turn_id})
        with self._lock:
            if self._session is session:
                self._session = None
        self._promote_deferred()

    def _promote_deferred(self) -> None:
        if self._session is not None or not self._deferred:
            return
        first_turn_id = next(iter(self._deferred))
        deferred = self._deferred.pop(first_turn_id)
        self._begin_now(deferred.turn_id)
        if deferred.input_finished:
            self.finish(deferred.turn_id, deferred.final_text)
        elif deferred.raw_text:
            self.append_text(deferred.turn_id, deferred.raw_text)

    def _current(self, turn_id: str) -> SpeechSession | None:
        session = self._session
        return session if session is not None and session.turn_id == str(turn_id) else None

    def _delete_artifacts(self, session: SpeechSession) -> None:
        for artifact in session.ready.values():
            artifact.path.unlink(missing_ok=True)
        session.ready.clear()

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        self.event.emit({'kind': kind, 'payload': dict(payload)})

    @staticmethod
    def _configured_workers(config: dict[str, Any]) -> int:
        return max(1, min(4, int(config.get('speech_max_concurrency', 2) or 2)))

    @staticmethod
    def _new_executor(max_workers: int) -> concurrent.futures.ThreadPoolExecutor:
        return concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix='maica-tts',
        )

    @staticmethod
    def _normalise(text: str) -> str:
        return ''.join(str(text or '').split())
