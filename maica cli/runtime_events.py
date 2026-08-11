# -*- coding: utf-8 -*-
"""Structured, thread-safe runtime events shared by CLI and GUI frontends."""

from __future__ import annotations

import datetime as dt
import threading
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable


EventCallback = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class RuntimeEvent:
    turn_id: str
    sequence: int
    kind: str
    payload: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TurnCancelled(RuntimeError):
    """Raised at safe boundaries when the active turn has been cancelled."""


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason = 'cancelled'
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = []

    def cancel(self, reason: str = 'cancelled') -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._reason = str(reason or 'cancelled')
            self._event.set()
            callbacks = list(self._callbacks)
            self._callbacks.clear()
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass

    def add_cancel_callback(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)
                return
        try:
            callback()
        except Exception:
            pass

    def remove_cancel_callback(self, callback: Callable[[], None]) -> None:
        with self._lock:
            try:
                self._callbacks.remove(callback)
            except ValueError:
                pass

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    def check(self) -> None:
        if self.cancelled:
            raise TurnCancelled(self.reason)


class TurnRuntime:
    """Own event ordering and cancellation state for one assistant turn."""

    def __init__(
        self,
        callback: EventCallback | None = None,
        token: CancellationToken | None = None,
        turn_id: str | None = None,
    ) -> None:
        self.turn_id = str(turn_id or uuid.uuid4())
        self.callback = callback
        self.token = token or CancellationToken()
        self._sequence = 0
        self._lock = threading.Lock()
        self._terminal = False

    def check(self) -> None:
        self.token.check()

    def emit(self, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            event = RuntimeEvent(
                turn_id=self.turn_id,
                sequence=self._sequence,
                kind=str(kind),
                payload=dict(payload or {}),
                created_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec='milliseconds'),
            ).to_dict()
        if self.callback is not None:
            try:
                self.callback(event)
            except Exception:
                # Observers must never break the conversation runtime.
                pass
        return event

    def finish(self, kind: str, payload: dict[str, Any] | None = None) -> None:
        with self._lock:
            if self._terminal:
                return
            self._terminal = True
        self.emit(kind, payload)

    def cancel(self, reason: str = 'cancelled') -> None:
        self.token.cancel(reason)
        self.finish('turn.cancelled', {'reason': self.token.reason})
