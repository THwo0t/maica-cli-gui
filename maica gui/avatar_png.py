# -*- coding: utf-8 -*-
"""PNG avatar driver for the existing layered Monika sprite."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from assets import AssetManager, normalize_emotion


class PngAvatarDriver:
    def __init__(self, assets: AssetManager, label: QLabel) -> None:
        self.assets = assets
        self.label = label
        self.current_emotion = 'smile'

    def start(self) -> None:
        self.refresh()

    def stop(self) -> None:
        pass

    def set_emotion(self, emotion: str) -> None:
        self.current_emotion = normalize_emotion(emotion)
        self.refresh()

    def play_action(self, action: dict[str, Any] | str | None) -> None:
        # PNG currently has expression layers only; gestures are handled by the
        # separate desktop pet and future Live2D/VTS drivers.
        return

    def set_speaking(self, speaking: bool) -> None:
        return

    def set_mouth_open(self, value: float) -> None:
        return

    def tick(self) -> None:
        return

    def refresh(self) -> None:
        avatar = self.assets.compose_avatar(self.current_emotion)
        if avatar.isNull():
            return
        scaled = avatar.scaled(
            self.label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.label.setPixmap(scaled)

    def status_text(self) -> str:
        return 'png'
