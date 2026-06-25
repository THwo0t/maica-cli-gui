# -*- coding: utf-8 -*-
"""Avatar driver interfaces shared by PNG, VTube Studio, and future Live2D."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class AvatarState:
    emotion: str = 'neutral'
    pose: str = 'default'
    mouth: str = 'default'
    action: dict[str, Any] = field(default_factory=dict)
    speaking: bool = False
    mouth_open: float = 0.0


class AvatarDriver(Protocol):
    def start(self) -> None:
        ...

    def stop(self) -> None:
        ...

    def set_emotion(self, emotion: str) -> None:
        ...

    def play_action(self, action: dict[str, Any] | str | None) -> None:
        ...

    def set_speaking(self, speaking: bool) -> None:
        ...

    def set_mouth_open(self, value: float) -> None:
        ...

    def refresh(self) -> None:
        ...

    def tick(self) -> None:
        ...

    def status_text(self) -> str:
        ...
