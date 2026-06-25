# -*- coding: utf-8 -*-
"""Avatar backend coordinator for MAICA GUI."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import QLabel

from assets import AssetManager, normalize_emotion
from avatar_png import PngAvatarDriver
from avatar_vts import VTubeStudioDriver


StatusCallback = Callable[[str], None]
TokenCallback = Callable[[str], None]


class AvatarController:
    def __init__(
        self,
        assets: AssetManager,
        label: QLabel,
        config: dict[str, Any] | None = None,
        on_status: StatusCallback | None = None,
        on_token: TokenCallback | None = None,
    ) -> None:
        self.assets = assets
        self.label = label
        self.on_status = on_status
        self.on_token = on_token
        self.png = PngAvatarDriver(assets, label)
        self.vts: VTubeStudioDriver | None = None
        self.backend = 'png'
        self.current_emotion = 'smile'
        self.speaking = False
        self._mouth_phase = 0
        self._status = 'png'
        self._signature: tuple[str, str, str, str] | None = None
        self.configure(config or {})

    def configure(self, config: dict[str, Any]) -> None:
        backend = str(config.get('avatar_backend') or 'png').strip().lower()
        if backend not in {'png', 'vtube_studio', 'auto'}:
            backend = 'png'
        signature = (
            backend,
            str(config.get('vts_url') or ''),
            str(config.get('vts_auth_token') or ''),
            str(config.get('vts_parameter_prefix') or 'Maica'),
        )
        if signature == self._signature and (backend == 'png' or self.vts is not None):
            return
        self.stop_vts()
        self.backend = backend
        self._signature = signature
        self.png.start()
        if backend in {'vtube_studio', 'auto'}:
            self.vts = VTubeStudioDriver(config, on_status=self._set_vts_status, on_token=self.on_token)
            self.vts.start()
            self.vts.set_emotion(self.current_emotion)
        else:
            self.vts = None
            self._set_status('png')

    def stop(self) -> None:
        self.stop_vts()
        self.png.stop()

    def stop_vts(self) -> None:
        if self.vts is not None:
            self.vts.stop()
            self.vts = None

    def set_emotion(self, emotion: str) -> None:
        self.current_emotion = normalize_emotion(emotion)
        self.png.set_emotion(self.current_emotion)
        if self.vts is not None:
            self.vts.set_emotion(self.current_emotion)

    def play_action(self, action: dict[str, Any] | str | None) -> None:
        self.png.play_action(action)
        if self.vts is not None:
            self.vts.play_action(action)

    def set_speaking(self, speaking: bool) -> None:
        self.speaking = bool(speaking)
        self.png.set_speaking(self.speaking)
        if self.vts is not None:
            self.vts.set_speaking(self.speaking)
            if not self.speaking:
                self.vts.set_mouth_open(0.0)

    def set_mouth_open(self, value: float) -> None:
        self.png.set_mouth_open(value)
        if self.vts is not None:
            self.vts.set_mouth_open(value)

    def refresh(self) -> None:
        self.png.refresh()
        if self.vts is not None:
            self.vts.refresh()

    def tick(self) -> None:
        if self.speaking:
            self._mouth_phase = (self._mouth_phase + 1) % 4
            self.set_mouth_open((0.15, 0.62, 0.32, 0.78)[self._mouth_phase])
        else:
            self.set_mouth_open(0.0)
        if self.vts is not None:
            self.vts.tick()

    def status_text(self) -> str:
        if self.backend == 'png':
            return 'png'
        return f'{self.backend}: {self._status}'

    def _set_vts_status(self, text: str) -> None:
        self._set_status(text)

    def _set_status(self, text: str) -> None:
        self._status = text
        if self.on_status:
            self.on_status(self.status_text())
