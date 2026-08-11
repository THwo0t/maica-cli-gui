# -*- coding: utf-8 -*-
"""Generation-safe projection of engine runtime events into the GUI."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal, Slot


TERMINAL_EVENT_KINDS = {
    'turn.finished',
    'turn.failed',
    'turn.cancelled',
}


class DialogueEventController(QObject):
    """Own turn identity and reject stale, duplicate, or post-terminal events."""

    event = Signal(dict)
    turn_started = Signal(str, int)
    turn_terminal = Signal(str, str)
    stale_event = Signal(dict)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._turn_id = ''
        self._sequence = 0
        self._generation = 0
        self._terminal_kind = ''
        self._closed = False

    @property
    def active_turn_id(self) -> str:
        return self._turn_id

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def sequence(self) -> int:
        return self._sequence

    @Slot(dict)
    def accept(self, raw_event: dict[str, Any]) -> None:
        if self._closed or not isinstance(raw_event, dict):
            return
        event = dict(raw_event)
        turn_id = str(event.get('turn_id') or '')
        kind = str(event.get('kind') or '')
        try:
            sequence = int(event.get('sequence') or 0)
        except (TypeError, ValueError):
            sequence = 0
        if kind == 'turn.started' and turn_id:
            if turn_id != self._turn_id:
                self._generation += 1
                self._turn_id = turn_id
                self._sequence = sequence
                self._terminal_kind = ''
                self.turn_started.emit(turn_id, self._generation)
                self.event.emit(event)
                return
            self.stale_event.emit(event)
            return
        if (
            not turn_id
            or turn_id != self._turn_id
            or sequence <= self._sequence
            or self._terminal_kind
        ):
            self.stale_event.emit(event)
            return
        self._sequence = sequence
        if kind in TERMINAL_EVENT_KINDS:
            self._terminal_kind = kind
            self.turn_terminal.emit(turn_id, kind)
        self.event.emit(event)

    def accept_result(self, result: dict[str, Any]) -> bool:
        """Accept only the result belonging to the current generation."""
        if self._closed or not isinstance(result, dict):
            return False
        turn_id = str(result.get('turn_id') or '')
        if self._turn_id:
            if not turn_id or turn_id != self._turn_id:
                return False
        elif turn_id:
            return False
        self.reset()
        return True

    def mark_cancel_requested(self) -> None:
        if self._turn_id and not self._terminal_kind:
            self._terminal_kind = 'cancel.requested'

    def reset(self) -> None:
        self._turn_id = ''
        self._sequence = 0
        self._terminal_kind = ''

    def close(self) -> None:
        self._closed = True
        self._generation += 1
        self.reset()
