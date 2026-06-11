#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MAICA GUI v0.8.2.

The GUI calls the shared MaicaEngine through a persistent background worker.
The CLI remains a debugger and is not started in the background.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
GUI_DIR = Path(__file__).resolve().parent
ASSET_DIR = ROOT_DIR / 'maica gui assets' / 'runtime'
MANIFEST_PATH = ASSET_DIR / 'manifest.json'
APP_VERSION = '0.8.2'

if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

from assets import AssetManager, normalize_emotion  # noqa: E402
from engine_worker import GuiEngineWorker  # noqa: E402
from tts import create_tts  # noqa: E402


def html_escape(text: str) -> str:
    return (
        text.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )


class BackgroundWidget(QWidget):
    def __init__(self, background: QPixmap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.background = background

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if not self.background.isNull():
            scaled = self.background.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        painter.fillRect(self.rect(), QColor(15, 18, 22, 84))
        painter.end()
        super().paintEvent(event)


class MainWindow(QMainWindow):
    chat_requested = Signal(str)
    spire_requested = Signal(str)
    shutdown_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        if not MANIFEST_PATH.exists():
            raise FileNotFoundError(f'Missing GUI asset manifest: {MANIFEST_PATH}')

        self.assets = AssetManager(MANIFEST_PATH)
        self.thread = QThread(self)
        self.worker = GuiEngineWorker()
        self.worker.moveToThread(self.thread)
        self.tts = create_tts({})
        self.tts_enabled = False

        self.setWindowTitle(f'MAICA GUI v{APP_VERSION}')
        self.resize(1180, 760)
        self.setMinimumSize(980, 640)
        self._build_ui()
        self._connect_worker()
        self.set_emotion('smile')
        self.add_system_message('MAICA GUI 已启动。CLI 现在只作为 debugger 使用。')
        self.thread.start()

    def _connect_worker(self) -> None:
        self.thread.started.connect(self.worker.initialize)
        self.worker.ready.connect(self._handle_ready)
        self.worker.status.connect(self.add_system_message)
        self.worker.finished.connect(self._handle_result)
        self.worker.config_ready.connect(self._handle_config_ready)
        self.chat_requested.connect(self.worker.chat)
        self.spire_requested.connect(self.worker.spire)
        self.shutdown_requested.connect(self.worker.shutdown)

    def _build_ui(self) -> None:
        root = BackgroundWidget(self.assets.background())
        self.setCentralWidget(root)

        main_layout = QHBoxLayout(root)
        main_layout.setContentsMargins(28, 24, 28, 24)
        main_layout.setSpacing(22)

        left_panel = QFrame()
        left_panel.setObjectName('leftPanel')
        left_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(10, 10, 10, 10)

        self.avatar_label = QLabel()
        self.avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_label.setMinimumWidth(440)
        self.avatar_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left_layout.addWidget(self.avatar_label, 1)

        self.status_label = QLabel('emotion: smile')
        self.status_label.setObjectName('statusLabel')
        left_layout.addWidget(self.status_label)
        main_layout.addWidget(left_panel, 5)

        right_panel = QFrame()
        right_panel.setObjectName('rightPanel')
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(18, 18, 18, 18)
        right_layout.setSpacing(12)

        title = QLabel('Monika')
        title.setObjectName('titleLabel')
        right_layout.addWidget(title)

        self.chat_view = QTextBrowser()
        self.chat_view.setObjectName('chatView')
        self.chat_view.setOpenExternalLinks(False)
        right_layout.addWidget(self.chat_view, 1)

        self.input_box = QTextEdit()
        self.input_box.setObjectName('inputBox')
        self.input_box.setPlaceholderText('输入想对 Monika 说的话。Ctrl+Enter 发送。')
        self.input_box.setMaximumHeight(92)
        self.input_box.installEventFilter(self)
        right_layout.addWidget(self.input_box)

        button_row = QHBoxLayout()
        self.send_button = QPushButton('发送')
        self.spire_button = QPushButton('/spire')
        self.tts_button = QPushButton('TTS: off')
        self.stop_tts_button = QPushButton('停止语音')
        self.clear_button = QPushButton('清屏')
        self.send_button.clicked.connect(self.send_chat)
        self.spire_button.clicked.connect(self.send_spire)
        self.tts_button.clicked.connect(self.toggle_tts)
        self.stop_tts_button.clicked.connect(self.stop_tts)
        self.clear_button.clicked.connect(self.chat_view.clear)
        button_row.addWidget(self.send_button)
        button_row.addWidget(self.spire_button)
        button_row.addWidget(self.tts_button)
        button_row.addWidget(self.stop_tts_button)
        button_row.addWidget(self.clear_button)
        right_layout.addLayout(button_row)

        main_layout.addWidget(right_panel, 4)
        self.setStyleSheet(STYLE_SHEET)

    def eventFilter(self, watched: QObject, event: Any) -> bool:
        if watched is self.input_box and event.type() == QEvent.Type.KeyPress:
            if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter} and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.send_chat()
                return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self.set_emotion(self.status_label.property('emotion') or 'smile')

    def closeEvent(self, event: Any) -> None:
        self.tts.stop()
        self.shutdown_requested.emit()
        self.thread.quit()
        if not self.thread.wait(30000):
            event.ignore()
            self.add_system_message('后台引擎仍在关闭，请稍等几秒后再退出。')
            return
        super().closeEvent(event)

    def add_system_message(self, text: str) -> None:
        self.chat_view.append(f'<div class="system">{html_escape(text)}</div>')

    def add_user_message(self, text: str) -> None:
        self.chat_view.append(f'<div class="user"><b>you</b><br>{html_escape(text)}</div>')

    def add_monika_message(self, text: str, emotion: str, response_time: Any = '') -> None:
        lines = '<br>'.join(html_escape(line) for line in text.splitlines() if line.strip())
        meta = f'emotion: {html_escape(emotion or "neutral")}'
        if response_time != '':
            meta += f' · {response_time}s'
        self.chat_view.append(f'<div class="monika"><b>monika</b><br>{lines}<br><span>{meta}</span></div>')

    def set_busy(self, busy: bool) -> None:
        self.send_button.setEnabled(not busy)
        self.spire_button.setEnabled(not busy)
        self.input_box.setEnabled(not busy)
        if busy:
            self.status_label.setText('thinking...')

    def set_emotion(self, emotion: str) -> None:
        normalized = normalize_emotion(emotion)
        self.status_label.setProperty('emotion', normalized)
        self.status_label.setText(f'emotion: {normalized}')
        avatar = self.assets.compose_avatar(normalized)
        if avatar.isNull():
            return
        scaled = avatar.scaled(
            self.avatar_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.avatar_label.setPixmap(scaled)

    def visual_state_from_result(self, result: dict[str, Any]) -> dict[str, Any]:
        emotion = str(result.get('emotion') or 'neutral')
        action = result.get('action') if isinstance(result.get('action'), dict) else {}
        normalized = normalize_emotion(emotion)
        return {
            'emotion': normalized,
            'raw_emotion': emotion,
            'pose': str(action.get('pose') or 'default'),
            'mouth': str(action.get('mouth') or 'default'),
            'action': action,
        }

    def send_chat(self) -> None:
        text = self.input_box.toPlainText().strip()
        if not text:
            return
        self.input_box.clear()
        self.add_user_message(text)
        self.set_busy(True)
        self.chat_requested.emit(text)

    def send_spire(self) -> None:
        hint = self.input_box.toPlainText().strip()
        self.input_box.clear()
        self.add_system_message('/spire 正在生成主动话题...')
        self.set_busy(True)
        self.spire_requested.emit(hint)

    def _handle_ready(self, result: dict[str, Any]) -> None:
        if result.get('ok'):
            self.add_system_message('后台引擎已就绪。')
        else:
            self.add_system_message(f'后台引擎初始化失败：{result.get("error", "unknown error")}')

    def _handle_config_ready(self, config: dict[str, Any]) -> None:
        self.tts = create_tts(config)
        self.tts_enabled = bool(config.get('tts_enabled', False))
        self.tts_button.setText('TTS: on' if self.tts_enabled else 'TTS: off')

    def toggle_tts(self) -> None:
        self.tts_enabled = not self.tts_enabled
        self.tts_button.setText('TTS: on' if self.tts_enabled else 'TTS: off')
        if not self.tts_enabled:
            self.tts.stop()
        self.add_system_message('TTS 已开启。' if self.tts_enabled else 'TTS 已关闭。')

    def stop_tts(self) -> None:
        self.tts.stop()

    def _handle_result(self, result: dict[str, Any]) -> None:
        self.set_busy(False)
        if not result.get('ok'):
            self.set_emotion('concerned')
            self.add_system_message(f'调用失败：{result.get("error", "unknown error")}')
            return

        visual_state = self.visual_state_from_result(result)
        reply_text = str(result.get('text') or '')
        emotion = str(visual_state.get('raw_emotion') or 'neutral')
        self.set_emotion(str(visual_state.get('emotion') or emotion))
        self.add_monika_message(reply_text, emotion, result.get('response_time', ''))
        if self.tts_enabled and reply_text:
            self.tts.speak(reply_text)
        for notice in result.get('mtrigger_notices') or []:
            self.chat_view.append(f'<div class="notice">{html_escape(str(notice))}</div>')


STYLE_SHEET = """
QMainWindow {
    background: #111318;
}
QFrame#leftPanel, QFrame#rightPanel {
    background: rgba(20, 24, 29, 172);
    border: 1px solid rgba(255, 255, 255, 72);
    border-radius: 18px;
}
QLabel#titleLabel {
    color: #f6f0e8;
    font-size: 28px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#statusLabel {
    color: #e9dacd;
    background: rgba(0, 0, 0, 88);
    padding: 8px 12px;
    border-radius: 10px;
}
QTextBrowser#chatView {
    background: rgba(250, 246, 239, 224);
    color: #2a2728;
    border: none;
    border-radius: 14px;
    padding: 12px;
    font-size: 15px;
}
QTextEdit#inputBox {
    background: rgba(255, 252, 248, 238);
    color: #292525;
    border: 1px solid rgba(120, 82, 85, 120);
    border-radius: 12px;
    padding: 10px;
    font-size: 15px;
}
QPushButton {
    background: #8d4d5a;
    color: #fff8f4;
    border: none;
    border-radius: 12px;
    padding: 10px 16px;
    font-size: 15px;
}
QPushButton:hover {
    background: #a65f6d;
}
QPushButton:disabled {
    background: #6a6267;
    color: #d1c5c1;
}
"""


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName('MAICA GUI')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
