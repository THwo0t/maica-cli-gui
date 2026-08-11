# -*- coding: utf-8 -*-
"""Embedded Cubism 4 avatar driver backed by a restricted QWebEngineView."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QLabel, QStackedWidget

from avatar_mapping import AvatarMappingError, load_avatar_mapping
from live2d_model import validate_cubism_core, validate_live2d_model


GUI_DIR = Path(__file__).resolve().parent
WEB_ROOT = GUI_DIR / 'live2d_web' / 'dist'
WEB_ENTRY = WEB_ROOT / 'index.html'
StatusCallback = Callable[[str], None]
HitCallback = Callable[[str], None]


class RestrictedLive2DPage(QWebEnginePage):
    """Block top-level navigation away from the packaged local renderer."""

    def acceptNavigationRequest(self, url: QUrl, nav_type: Any, is_main_frame: bool) -> bool:
        if not is_main_frame:
            return True
        if url.scheme() in {'about', 'qrc'}:
            return True
        if url.isLocalFile():
            try:
                Path(url.toLocalFile()).resolve(strict=False).relative_to(WEB_ROOT.resolve(strict=False))
                return True
            except ValueError:
                return False
        return False

    def createWindow(self, _window_type: Any) -> QWebEnginePage | None:
        return None


class Live2DBridge(QObject):
    command = Signal(str)

    def __init__(self, initial_state: dict[str, Any], owner: 'EmbeddedLive2DDriver') -> None:
        super().__init__(owner.view)
        self.state = dict(initial_state)
        self.owner = owner

    @Slot(result=str)
    def initialState(self) -> str:
        return json.dumps(self.state, ensure_ascii=False)

    @Slot(str)
    def rendererStatus(self, status: str) -> None:
        self.owner._renderer_status(str(status or ''))

    @Slot(str)
    def rendererError(self, error: str) -> None:
        self.owner._renderer_error(str(error or 'Live2D renderer failed'))

    @Slot(str)
    def hitArea(self, area: str) -> None:
        self.owner._hit_area(str(area or ''))


class EmbeddedLive2DDriver:
    @staticmethod
    def validate_config(config: dict[str, Any]) -> tuple[bool, str, str]:
        if not WEB_ENTRY.is_file():
            return False, 'Live2D web assets are missing', ''
        report = validate_live2d_model(str(config.get('live2d_model_path') or ''))
        if not report.ok:
            error = report.errors[0] if report.errors else 'Live2D model is invalid'
            return False, error, ''
        core_ok, core_error = validate_cubism_core(config.get('live2d_core_path'))
        if not core_ok:
            return False, core_error, ''
        try:
            load_avatar_mapping(config.get('live2d_expression_map_path'), report.entry_point)
        except AvatarMappingError as exc:
            return False, str(exc), ''
        return True, '', report.entry_point

    def __init__(
        self,
        config: dict[str, Any],
        stack: QStackedWidget,
        png_widget: QLabel,
        on_status: StatusCallback | None = None,
        on_hit: HitCallback | None = None,
    ) -> None:
        self.config = dict(config)
        self.stack = stack
        self.png_widget = png_widget
        self.on_status = on_status
        self.on_hit = on_hit
        self.view = QWebEngineView(stack)
        self.view.setObjectName('live2dView')
        self.view.setStyleSheet('background: transparent;')
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.page = RestrictedLive2DPage(self.view)
        self.page.setBackgroundColor(QColor(0, 0, 0, 0))
        self.page.renderProcessTerminated.connect(self._render_process_terminated)
        self.view.setPage(self.page)
        self.stack.addWidget(self.view)
        self.channel: QWebChannel | None = None
        self.bridge: Live2DBridge | None = None
        self._status = 'stopped'
        self._emotion = 'neutral'
        self._speaking = False
        self._mouth_open = 0.0
        self._validation_error = ''
        self._entry_point = ''
        self._running = False
        self._disposed = False
        self._renderer_restarts = 0

    def can_start(self) -> bool:
        ready, error, entry_point = self.validate_config(self.config)
        self._validation_error = error
        self._entry_point = entry_point
        if not ready:
            self._status = f'unavailable: {error}'
        return ready

    def start(self) -> None:
        if not self.can_start():
            self._set_status(f'unavailable: {self._validation_error}')
            self.stack.setCurrentWidget(self.png_widget)
            return
        self._running = True
        self._disposed = False
        self._renderer_restarts = 0
        self._load_renderer()

    def _load_renderer(self) -> None:
        if not self._running or self._disposed:
            return
        if self.channel is not None and self.bridge is not None:
            self.channel.deregisterObject(self.bridge)
            self.bridge.deleteLater()
        self.channel = None
        self.bridge = None
        self._set_status('loading')
        self.stack.setCurrentWidget(self.png_widget)
        settings = self.page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        mapping, mapping_sources = load_avatar_mapping(
            self.config.get('live2d_expression_map_path'),
            self._entry_point,
        )
        initial_state = {
            'modelUrl': QUrl.fromLocalFile(self._entry_point).toString(),
            'coreUrl': QUrl.fromLocalFile(str(Path(self.config['live2d_core_path']).expanduser().resolve())).toString(),
            'renderFps': max(15, min(120, int(self.config.get('live2d_render_fps', 60) or 60))),
            'eyeTracking': bool(self.config.get('live2d_eye_tracking', True)),
            'transparent': bool(self.config.get('live2d_transparent_background', True)),
            'emotion': self._emotion,
            'speaking': self._speaking,
            'mouthOpen': self._mouth_open,
            'mouthAttackMs': max(10, min(1000, int(self.config.get('live2d_mouth_attack_ms', 60) or 60))),
            'mouthReleaseMs': max(10, min(1000, int(self.config.get('live2d_mouth_release_ms', 120) or 120))),
            'avatarMapping': mapping,
            'mappingSources': [Path(path).name for path in mapping_sources],
        }
        self.channel = QWebChannel(self.page)
        self.bridge = Live2DBridge(initial_state, self)
        self.channel.registerObject('maicaAvatar', self.bridge)
        self.page.setWebChannel(self.channel)
        self.view.load(QUrl.fromLocalFile(str(WEB_ENTRY)))

    def stop(self) -> None:
        self._running = False
        self._set_status('stopped')
        self.stack.setCurrentWidget(self.png_widget)
        self.view.stop()
        self.view.setUrl(QUrl('about:blank'))
        if self.channel is not None and self.bridge is not None:
            self.channel.deregisterObject(self.bridge)
        self.bridge = None
        self.channel = None

    def dispose(self) -> None:
        self._disposed = True
        self.stop()
        self.stack.removeWidget(self.view)
        self.view.deleteLater()

    def set_emotion(self, emotion: str) -> None:
        self._emotion = str(emotion or 'neutral')
        self._send('emotion', {'value': self._emotion})

    def play_action(self, action: dict[str, Any] | str | None) -> None:
        if not isinstance(action, dict):
            return
        gesture = str(action.get('gesture') or '').strip().lower()
        if gesture in {'wave', 'nod', 'surprise', 'pout'}:
            self._send('action', {'value': gesture})

    def set_speaking(self, speaking: bool) -> None:
        self._speaking = bool(speaking)
        self._send('speaking', {'value': self._speaking})
        if not self._speaking:
            self.set_mouth_open(0.0)

    def set_mouth_open(self, value: float) -> None:
        self._mouth_open = max(0.0, min(1.0, float(value or 0.0)))
        self._send('mouth', {'value': self._mouth_open})

    def refresh(self) -> None:
        self._send('refresh', {})

    def tick(self) -> None:
        return

    def status_text(self) -> str:
        return self._status

    def _send(self, kind: str, payload: dict[str, Any]) -> None:
        if self.bridge is None:
            return
        state_key = {'emotion': 'emotion', 'speaking': 'speaking', 'mouth': 'mouthOpen'}.get(kind)
        if state_key:
            self.bridge.state[state_key] = payload.get('value')
        command = {'kind': kind, 'payload': dict(payload)}
        self.bridge.command.emit(json.dumps(command, ensure_ascii=False))

    def _renderer_status(self, status: str) -> None:
        normalized = status.strip() or 'ready'
        self._set_status(normalized)
        if normalized == 'model.loaded':
            self.stack.setCurrentWidget(self.view)

    def _renderer_error(self, error: str) -> None:
        text = ' '.join(error.split())
        if len(text) > 120:
            text = text[:117] + '...'
        self.stack.setCurrentWidget(self.png_widget)
        self._set_status(f'error: {text}')

    def _render_process_terminated(self, status: Any, exit_code: int) -> None:
        if not self._running or self._disposed:
            return
        self.stack.setCurrentWidget(self.png_widget)
        detail = getattr(status, 'name', str(status))
        backend = str(self.config.get('avatar_backend') or 'embedded_live2d').strip().lower()
        if backend == 'embedded_live2d' and self._renderer_restarts < 1:
            self._renderer_restarts += 1
            self._set_status(f'recovering renderer ({detail}, code {int(exit_code)})')
            QTimer.singleShot(750, self._reload_after_crash)
            return
        self._set_status(f'error: renderer process terminated ({detail}, code {int(exit_code)})')

    def _reload_after_crash(self) -> None:
        if not self._running or self._disposed:
            return
        self.view.stop()
        self.view.setUrl(QUrl('about:blank'))
        self._load_renderer()

    def _hit_area(self, area: str) -> None:
        if self.on_hit and area:
            self.on_hit(area[:80])

    def _set_status(self, status: str) -> None:
        self._status = status
        if self.on_status:
            self.on_status(status)
