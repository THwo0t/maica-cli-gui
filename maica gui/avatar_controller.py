# -*- coding: utf-8 -*-
"""Avatar backend coordinator for MAICA GUI."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import QLabel, QStackedWidget

from assets import AssetManager, normalize_emotion
from avatar_png import PngAvatarDriver
from avatar_vts import VTubeStudioDriver

try:
    from avatar_live2d import EmbeddedLive2DDriver
except Exception:  # pragma: no cover - optional WebEngine installation path
    EmbeddedLive2DDriver = None  # type: ignore


StatusCallback = Callable[[str], None]
TokenCallback = Callable[[str], None]
HitCallback = Callable[[str], None]


class AvatarController:
    def __init__(
        self,
        assets: AssetManager,
        label: QLabel,
        stack: QStackedWidget | None = None,
        config: dict[str, Any] | None = None,
        on_status: StatusCallback | None = None,
        on_token: TokenCallback | None = None,
        on_hit: HitCallback | None = None,
    ) -> None:
        self.assets = assets
        self.label = label
        self.stack = stack
        self.on_status = on_status
        self.on_token = on_token
        self.on_hit = on_hit
        self.png = PngAvatarDriver(assets, label)
        self.vts: VTubeStudioDriver | None = None
        self.embedded: Any = None
        self.backend = 'png'
        self.current_emotion = 'smile'
        self.speaking = False
        self._status = 'png'
        self._config: dict[str, Any] = {}
        self._signature: tuple[Any, ...] | None = None
        self.configure(config or {})

    def configure(self, config: dict[str, Any], force: bool = False) -> None:
        self._config = dict(config)
        backend = str(config.get('avatar_backend') or 'png').strip().lower()
        if backend not in {'png', 'vtube_studio', 'embedded_live2d', 'auto'}:
            backend = 'png'
        signature = (
            backend,
            str(config.get('vts_url') or ''),
            str(config.get('vts_auth_token') or ''),
            str(config.get('vts_parameter_prefix') or 'Maica'),
            str(config.get('live2d_model_path') or ''),
            str(config.get('live2d_core_path') or ''),
            int(config.get('live2d_render_fps', 60) or 60),
            bool(config.get('live2d_eye_tracking', True)),
            bool(config.get('live2d_transparent_background', True)),
            str(config.get('live2d_expression_map_path') or ''),
            int(config.get('live2d_mouth_attack_ms', 60) or 60),
            int(config.get('live2d_mouth_release_ms', 120) or 120),
        )
        if signature == self._signature and not force:
            return
        self._stop_dynamic_drivers()
        self.backend = backend
        self._signature = signature
        self.png.start()
        if backend in {'embedded_live2d', 'auto'} and self._start_embedded(config):
            return
        if backend in {'vtube_studio', 'auto'}:
            self._start_vts(config)
        elif backend == 'png':
            self.vts = None
            self._set_status('png')

    def stop(self) -> None:
        self._stop_dynamic_drivers()
        self.png.stop()

    def stop_vts(self) -> None:
        if self.vts is not None:
            self.vts.stop()
            self.vts = None

    def _stop_dynamic_drivers(self) -> None:
        self.stop_vts()
        if self.embedded is not None:
            self.embedded.dispose()
            self.embedded = None

    def _start_embedded(self, config: dict[str, Any]) -> bool:
        if EmbeddedLive2DDriver is None or self.stack is None:
            self._set_status('embedded Live2D unavailable')
            return False
        ready, error, _entry = EmbeddedLive2DDriver.validate_config(config)
        if not ready:
            self._set_status(f'unavailable: {error}')
            return False
        driver = EmbeddedLive2DDriver(
            config,
            self.stack,
            self.label,
            on_status=self._set_live2d_status,
            on_hit=self.on_hit,
        )
        self.embedded = driver
        driver.start()
        driver.set_emotion(self.current_emotion)
        driver.set_speaking(self.speaking)
        return True

    def _start_vts(self, config: dict[str, Any]) -> None:
        self.vts = VTubeStudioDriver(config, on_status=self._set_vts_status, on_token=self.on_token)
        self.vts.start()
        self.vts.set_emotion(self.current_emotion)

    def set_emotion(self, emotion: str) -> None:
        self.current_emotion = normalize_emotion(emotion)
        self.png.set_emotion(self.current_emotion)
        if self.embedded is not None:
            self.embedded.set_emotion(self.current_emotion)
        if self.vts is not None:
            self.vts.set_emotion(self.current_emotion)

    def play_action(self, action: dict[str, Any] | str | None) -> None:
        self.png.play_action(action)
        if self.embedded is not None:
            self.embedded.play_action(action)
        if self.vts is not None:
            self.vts.play_action(action)

    def set_speaking(self, speaking: bool) -> None:
        self.speaking = bool(speaking)
        self.png.set_speaking(self.speaking)
        if self.embedded is not None:
            self.embedded.set_speaking(self.speaking)
        if self.vts is not None:
            self.vts.set_speaking(self.speaking)
            if not self.speaking:
                self.vts.set_mouth_open(0.0)

    def set_mouth_open(self, value: float) -> None:
        self.png.set_mouth_open(value)
        if self.embedded is not None:
            self.embedded.set_mouth_open(value)
        if self.vts is not None:
            self.vts.set_mouth_open(value)

    def refresh(self) -> None:
        self.png.refresh()
        if self.embedded is not None:
            self.embedded.refresh()
        if self.vts is not None:
            self.vts.refresh()

    def tick(self) -> None:
        if not self.speaking:
            self.set_mouth_open(0.0)
        if self.embedded is not None:
            self.embedded.tick()
        if self.vts is not None:
            self.vts.tick()

    def status_text(self) -> str:
        if self.backend == 'png':
            return 'png'
        return f'{self.backend}: {self._status}'

    def _set_vts_status(self, text: str) -> None:
        self._set_status(text)

    def _set_live2d_status(self, text: str) -> None:
        self._set_status(text)
        if self.backend == 'auto' and text.startswith('error:') and self.embedded is not None:
            failed = self.embedded
            self.embedded = None
            failed.dispose()
            self._start_vts(self._config)

    def _set_status(self, text: str) -> None:
        self._status = text
        if self.on_status:
            self.on_status(self.status_text())
