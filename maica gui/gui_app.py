#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MAICA GUI v0.9.6.

The GUI calls the shared MaicaEngine through a persistent background worker.
The CLI remains a debugger and is not started in the background.
"""

from __future__ import annotations

import argparse
import sys
import datetime as dt
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
GUI_DIR = Path(__file__).resolve().parent
ASSET_DIR = ROOT_DIR / 'maica gui assets' / 'runtime'
MANIFEST_PATH = ASSET_DIR / 'manifest.json'
APP_VERSION = '0.9.6'

if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

from assets import AssetManager, normalize_emotion  # noqa: E402
from engine_worker import GuiEngineWorker  # noqa: E402
from stt import create_stt  # noqa: E402
from tts import create_tts  # noqa: E402


PROFILE_FIELDS = ('player_name', 'birthday', 'location', 'nicknames', 'affection')
LANGUAGE_OPTIONS = ('en', 'zh')
MODE_OPTIONS = ('hybrid', 'rule', 'off')
TTS_PROVIDERS = ('bailian_cosyvoice', 'windows_sapi', 'off')
STT_PROVIDERS = ('windows_speech', 'off')
BACKGROUND_MODES = ('auto', 'day', 'night', 'rain')


def html_escape(text: str) -> str:
    return (
        text.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )


class DataManagerDialog(QDialog):
    def __init__(self, owner: 'MainWindow') -> None:
        super().__init__(owner)
        self.owner = owner
        self.snapshot: dict[str, Any] = {}
        self.setWindowTitle('MAICA Data Manager')
        self.resize(760, 620)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self._build_profile_tab()
        self._build_memory_tab()
        self._build_fact_tab()
        self._build_debug_tab()

        button_row = QHBoxLayout()
        refresh = QPushButton('Refresh')
        close = QPushButton('Close')
        refresh.clicked.connect(self.owner.request_data_snapshot)
        close.clicked.connect(self.close)
        button_row.addStretch(1)
        button_row.addWidget(refresh)
        button_row.addWidget(close)
        layout.addLayout(button_row)

    def _build_profile_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form = QFormLayout()
        self.profile_inputs: dict[str, QLineEdit] = {}
        placeholders = {
            'player_name': 'player',
            'birthday': 'YYYY-MM-DD',
            'location': 'City or region',
            'nicknames': 'comma,separated,names',
            'affection': '200',
        }
        for key in PROFILE_FIELDS:
            field = QLineEdit()
            field.setPlaceholderText(placeholders.get(key, ''))
            self.profile_inputs[key] = field
            form.addRow(key, field)
        layout.addLayout(form)
        save = QPushButton('Save profile')
        save.clicked.connect(self.save_profile)
        layout.addWidget(save)
        layout.addStretch(1)
        self.tabs.addTab(tab, 'Profile')

    def _build_memory_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.memory_list = QListWidget()
        layout.addWidget(self.memory_list, 1)
        self.memory_text = QPlainTextEdit()
        self.memory_text.setPlaceholderText('New memory text')
        self.memory_text.setMaximumHeight(90)
        layout.addWidget(self.memory_text)
        row = QHBoxLayout()
        self.memory_tags = QLineEdit()
        self.memory_tags.setPlaceholderText('tags')
        self.memory_importance = QSpinBox()
        self.memory_importance.setRange(1, 5)
        self.memory_importance.setValue(2)
        add = QPushButton('Add memory')
        delete = QPushButton('Delete selected')
        add.clicked.connect(self.add_memory)
        delete.clicked.connect(self.delete_memory)
        row.addWidget(QLabel('Tags'))
        row.addWidget(self.memory_tags, 1)
        row.addWidget(QLabel('Importance'))
        row.addWidget(self.memory_importance)
        row.addWidget(add)
        row.addWidget(delete)
        layout.addLayout(row)
        self.tabs.addTab(tab, 'Memories')

    def _build_fact_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.fact_list = QListWidget()
        layout.addWidget(self.fact_list, 1)
        self.fact_text = QPlainTextEdit()
        self.fact_text.setPlaceholderText('New fact text')
        self.fact_text.setMaximumHeight(90)
        layout.addWidget(self.fact_text)
        row = QHBoxLayout()
        self.fact_category = QLineEdit()
        self.fact_category.setPlaceholderText('custom')
        self.fact_importance = QSpinBox()
        self.fact_importance.setRange(1, 5)
        self.fact_importance.setValue(2)
        add = QPushButton('Add fact')
        delete = QPushButton('Delete selected')
        add.clicked.connect(self.add_fact)
        delete.clicked.connect(self.delete_fact)
        row.addWidget(QLabel('Category'))
        row.addWidget(self.fact_category, 1)
        row.addWidget(QLabel('Importance'))
        row.addWidget(self.fact_importance)
        row.addWidget(add)
        row.addWidget(delete)
        layout.addLayout(row)
        self.tabs.addTab(tab, 'Facts')

    def _build_debug_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.debug_view = QPlainTextEdit()
        self.debug_view.setReadOnly(True)
        layout.addWidget(self.debug_view, 1)
        export = QPushButton('Export debug JSON')
        export.clicked.connect(self.export_debug)
        layout.addWidget(export)
        self.tabs.addTab(tab, 'Debug')

    def render(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot
        profile = snapshot.get('profile') if isinstance(snapshot.get('profile'), dict) else {}
        nicknames = snapshot.get('nicknames') if isinstance(snapshot.get('nicknames'), list) else []
        for key, field in self.profile_inputs.items():
            if key == 'nicknames':
                field.setText(', '.join(str(item) for item in nicknames))
            else:
                field.setText(str(profile.get(key, '')))
        self._render_memories(snapshot.get('memories') if isinstance(snapshot.get('memories'), list) else [])
        self._render_facts(snapshot.get('facts') if isinstance(snapshot.get('facts'), list) else [])
        self._render_debug(snapshot)

    def _render_memories(self, memories: list[dict[str, Any]]) -> None:
        self.memory_list.clear()
        for memory in memories:
            item = QListWidgetItem(
                f"#{memory.get('id')} [{memory.get('importance')}] {memory.get('text')}  ({memory.get('tags', '')})"
            )
            item.setData(Qt.ItemDataRole.UserRole, int(memory.get('id') or 0))
            self.memory_list.addItem(item)

    def _render_facts(self, facts: list[dict[str, Any]]) -> None:
        self.fact_list.clear()
        for fact in facts:
            item = QListWidgetItem(
                f"#{fact.get('id')} [{fact.get('importance')}] {fact.get('category')}: {fact.get('text')}"
            )
            item.setData(Qt.ItemDataRole.UserRole, int(fact.get('id') or 0))
            self.fact_list.addItem(item)

    def _render_debug(self, snapshot: dict[str, Any]) -> None:
        import json

        public_snapshot = {
            'status': snapshot.get('status', {}),
            'config': snapshot.get('config', {}),
            'counts': {
                'memories': len(snapshot.get('memories') or []),
                'facts': len(snapshot.get('facts') or []),
                'events': len(snapshot.get('events') or []),
            },
            'recent_events': snapshot.get('events', [])[:8],
        }
        self.debug_view.setPlainText(json.dumps(public_snapshot, ensure_ascii=False, indent=2, default=str))

    def save_profile(self) -> None:
        for key, field in self.profile_inputs.items():
            self.owner.profile_set_requested.emit(key, field.text().strip())
        self.owner.add_system_message('Profile update requested.')

    def add_memory(self) -> None:
        text = self.memory_text.toPlainText().strip()
        if not text:
            return
        self.owner.memory_add_requested.emit(text, self.memory_tags.text().strip(), self.memory_importance.value())
        self.memory_text.clear()

    def delete_memory(self) -> None:
        item = self.memory_list.currentItem()
        if item is None:
            return
        memory_id = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        if memory_id:
            self.owner.memory_delete_requested.emit(memory_id)

    def add_fact(self) -> None:
        text = self.fact_text.toPlainText().strip()
        if not text:
            return
        category = self.fact_category.text().strip() or 'custom'
        self.owner.fact_add_requested.emit(text, category, self.fact_importance.value())
        self.fact_text.clear()

    def delete_fact(self) -> None:
        item = self.fact_list.currentItem()
        if item is None:
            return
        fact_id = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        if fact_id:
            self.owner.fact_delete_requested.emit(fact_id)

    def export_debug(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, 'Export debug JSON', 'maica_gui_debug.json', 'JSON (*.json)')
        if path:
            self.owner.debug_export_requested.emit(path)


class SettingsDialog(QDialog):
    def __init__(self, owner: 'MainWindow') -> None:
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle('MAICA Settings')
        self.resize(560, 520)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.api_base = QLineEdit()
        self.model = QLineEdit()
        self.language = QComboBox()
        self.language.addItems(LANGUAGE_OPTIONS)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.01)
        self.temperature.setDecimals(2)
        self.top_p = QDoubleSpinBox()
        self.top_p.setRange(0.0, 1.0)
        self.top_p.setSingleStep(0.01)
        self.top_p.setDecimals(2)
        self.max_tokens = QSpinBox()
        self.max_tokens.setRange(64, 8192)
        self.mfocus_mode = QComboBox()
        self.mfocus_mode.addItems(MODE_OPTIONS)
        self.mtrigger_mode = QComboBox()
        self.mtrigger_mode.addItems(MODE_OPTIONS)
        self.show_debug = QCheckBox('Show debug in CLI/engine logs')
        self.embedding_enabled = QCheckBox('Enable Example Bank vector retrieval')
        self.memory_embedding_enabled = QCheckBox('Enable memory vector retrieval')
        self.embedding_service_enabled = QCheckBox('Use external embedding service')
        self.embedding_service_autostart = QCheckBox('Auto-start embedding service in GUI')
        self.gui_disable_thread_embeddings = QCheckBox('Disable GUI thread embeddings')
        self.embedding_service_port = QSpinBox()
        self.embedding_service_port.setRange(1024, 65535)
        self.gui_background_mode = QComboBox()
        self.gui_background_mode.addItems(BACKGROUND_MODES)

        self.tts_enabled = QCheckBox('Enable TTS by default')
        self.tts_provider = QComboBox()
        self.tts_provider.addItems(TTS_PROVIDERS)
        self.tts_model = QLineEdit()
        self.tts_voice = QLineEdit()
        self.tts_format = QComboBox()
        self.tts_format.addItems(('mp3', 'wav'))
        self.tts_instruction = QLineEdit()
        self.stt_provider = QComboBox()
        self.stt_provider.addItems(STT_PROVIDERS)
        self.stt_language = QComboBox()
        self.stt_language.addItems(('en', 'zh'))
        self.stt_timeout = QSpinBox()
        self.stt_timeout.setRange(2, 30)

        form.addRow('API base', self.api_base)
        form.addRow('Model', self.model)
        form.addRow('Language', self.language)
        form.addRow('Temperature', self.temperature)
        form.addRow('Top P', self.top_p)
        form.addRow('Max tokens', self.max_tokens)
        form.addRow('MFocus mode', self.mfocus_mode)
        form.addRow('MTrigger mode', self.mtrigger_mode)
        form.addRow('', self.show_debug)
        form.addRow('', self.embedding_enabled)
        form.addRow('', self.memory_embedding_enabled)
        form.addRow('', self.embedding_service_enabled)
        form.addRow('', self.embedding_service_autostart)
        form.addRow('Embedding service port', self.embedding_service_port)
        form.addRow('', self.gui_disable_thread_embeddings)
        form.addRow('Background mode', self.gui_background_mode)
        form.addRow('', self.tts_enabled)
        form.addRow('TTS provider', self.tts_provider)
        form.addRow('Bailian model', self.tts_model)
        form.addRow('Bailian voice', self.tts_voice)
        form.addRow('Bailian format', self.tts_format)
        form.addRow('Bailian instruction', self.tts_instruction)
        form.addRow('STT provider', self.stt_provider)
        form.addRow('STT language', self.stt_language)
        form.addRow('STT timeout', self.stt_timeout)
        layout.addLayout(form)

        note = QLabel('Secrets such as API keys are intentionally not shown here. Edit local config.json if needed.')
        note.setWordWrap(True)
        layout.addWidget(note)

        button_row = QHBoxLayout()
        save = QPushButton('Save settings')
        close = QPushButton('Close')
        save.clicked.connect(self.save)
        close.clicked.connect(self.close)
        button_row.addStretch(1)
        button_row.addWidget(save)
        button_row.addWidget(close)
        layout.addLayout(button_row)

    def render(self, config: dict[str, Any]) -> None:
        self.api_base.setText(str(config.get('api_base') or ''))
        self.model.setText(str(config.get('model') or ''))
        self._set_combo(self.language, str(config.get('language') or 'en'))
        self.temperature.setValue(float(config.get('temperature') or 0.22))
        self.top_p.setValue(float(config.get('top_p') or 0.7))
        self.max_tokens.setValue(int(config.get('max_tokens') or 900))
        self._set_combo(self.mfocus_mode, str(config.get('mfocus_mode') or 'hybrid'))
        self._set_combo(self.mtrigger_mode, str(config.get('mtrigger_mode') or 'hybrid'))
        self.show_debug.setChecked(bool(config.get('show_debug', True)))
        self.embedding_enabled.setChecked(bool(config.get('embedding_enabled', False)))
        self.memory_embedding_enabled.setChecked(bool(config.get('memory_embedding_enabled', False)))
        self.embedding_service_enabled.setChecked(bool(config.get('embedding_service_enabled', False)))
        self.embedding_service_autostart.setChecked(bool(config.get('embedding_service_autostart', True)))
        self.embedding_service_port.setValue(int(config.get('embedding_service_port') or 8766))
        self.gui_disable_thread_embeddings.setChecked(bool(config.get('gui_disable_thread_embeddings', True)))
        self._set_combo(self.gui_background_mode, str(config.get('gui_background_mode') or 'auto'))
        self.tts_enabled.setChecked(bool(config.get('tts_enabled', False)))
        self._set_combo(self.tts_provider, str(config.get('tts_provider') or 'windows_sapi'))
        self.tts_model.setText(str(config.get('tts_bailian_model') or ''))
        self.tts_voice.setText(str(config.get('tts_bailian_voice') or ''))
        self._set_combo(self.tts_format, str(config.get('tts_bailian_format') or 'mp3'))
        self.tts_instruction.setText(str(config.get('tts_bailian_instruction') or ''))
        self._set_combo(self.stt_provider, str(config.get('stt_provider') or 'windows_speech'))
        self._set_combo(self.stt_language, str(config.get('stt_language') or config.get('language') or 'en'))
        self.stt_timeout.setValue(int(config.get('stt_timeout') or 8))

    def _set_combo(self, combo: QComboBox, value: str) -> None:
        index = combo.findText(value)
        if index < 0:
            combo.addItem(value)
            index = combo.findText(value)
        combo.setCurrentIndex(max(0, index))

    def save(self) -> None:
        updates = {
            'api_base': self.api_base.text().strip(),
            'model': self.model.text().strip(),
            'language': self.language.currentText(),
            'temperature': self.temperature.value(),
            'top_p': self.top_p.value(),
            'max_tokens': self.max_tokens.value(),
            'mfocus_mode': self.mfocus_mode.currentText(),
            'mtrigger_mode': self.mtrigger_mode.currentText(),
            'show_debug': self.show_debug.isChecked(),
            'embedding_enabled': self.embedding_enabled.isChecked(),
            'memory_embedding_enabled': self.memory_embedding_enabled.isChecked(),
            'embedding_service_enabled': self.embedding_service_enabled.isChecked(),
            'embedding_service_autostart': self.embedding_service_autostart.isChecked(),
            'embedding_service_port': self.embedding_service_port.value(),
            'gui_disable_thread_embeddings': self.gui_disable_thread_embeddings.isChecked(),
            'gui_background_mode': self.gui_background_mode.currentText(),
            'tts_enabled': self.tts_enabled.isChecked(),
            'tts_provider': self.tts_provider.currentText(),
            'tts_bailian_model': self.tts_model.text().strip(),
            'tts_bailian_voice': self.tts_voice.text().strip(),
            'tts_bailian_format': self.tts_format.currentText(),
            'tts_bailian_instruction': self.tts_instruction.text().strip(),
            'stt_provider': self.stt_provider.currentText(),
            'stt_language': self.stt_language.currentText(),
            'stt_timeout': self.stt_timeout.value(),
        }
        self.owner.config_save_requested.emit(updates)


class BackgroundWidget(QWidget):
    def __init__(self, background: QPixmap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.background = background

    def set_background(self, background: QPixmap) -> None:
        self.background = background
        self.update()

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
    data_snapshot_requested = Signal()
    profile_set_requested = Signal(str, str)
    memory_add_requested = Signal(str, str, int)
    memory_delete_requested = Signal(int)
    fact_add_requested = Signal(str, str, int)
    fact_delete_requested = Signal(int)
    debug_export_requested = Signal(str)
    config_save_requested = Signal(dict)
    stt_finished = Signal(dict)

    def __init__(
        self,
        config_path: str | Path | None = None,
        db_path: str | Path | None = None,
        safe_test_mode: bool = False,
    ) -> None:
        super().__init__()
        if not MANIFEST_PATH.exists():
            raise FileNotFoundError(f'Missing GUI asset manifest: {MANIFEST_PATH}')

        self.safe_test_mode = safe_test_mode
        self.assets = AssetManager(MANIFEST_PATH)
        self.thread = QThread(self)
        self.worker = GuiEngineWorker(config_path=config_path, db_path=db_path, app_dir=ROOT_DIR / 'maica cli')
        self.worker.moveToThread(self.thread)
        self.tts = create_tts({})
        self.stt = create_stt({})
        self.tts_enabled = False
        self.last_tts_error = ''
        self.stt_busy = False
        self.data_dialog: DataManagerDialog | None = None
        self.settings_dialog: SettingsDialog | None = None
        self.current_config: dict[str, Any] = {}

        suffix = ' · SAFE TEST DB' if self.safe_test_mode else ''
        self.setWindowTitle(f'MAICA GUI v{APP_VERSION}{suffix}')
        self.resize(1180, 760)
        self.setMinimumSize(980, 640)
        self._build_ui()
        self._connect_worker()
        self.set_emotion('smile')
        self.add_system_message('MAICA GUI started. CLI is available as a debugger.')
        if self.safe_test_mode:
            self.add_system_message('Safe test DB mode is active. Real memories/profile are not being modified.')
        self.thread.start()

    def _connect_worker(self) -> None:
        self.thread.started.connect(self.worker.initialize)
        self.worker.ready.connect(self._handle_ready)
        self.worker.status.connect(self.add_system_message)
        self.worker.finished.connect(self._handle_result)
        self.worker.config_ready.connect(self._handle_config_ready)
        self.worker.data_ready.connect(self._handle_data_ready)
        self.chat_requested.connect(self.worker.chat)
        self.spire_requested.connect(self.worker.spire)
        self.shutdown_requested.connect(self.worker.shutdown)
        self.data_snapshot_requested.connect(self.worker.data_snapshot)
        self.profile_set_requested.connect(self.worker.set_profile_value)
        self.memory_add_requested.connect(self.worker.add_memory)
        self.memory_delete_requested.connect(self.worker.delete_memory)
        self.fact_add_requested.connect(self.worker.add_fact)
        self.fact_delete_requested.connect(self.worker.delete_fact)
        self.debug_export_requested.connect(self.worker.export_debug)
        self.config_save_requested.connect(self.worker.save_config)
        self.stt_finished.connect(self._handle_stt_finished)

    def _build_ui(self) -> None:
        self.root_widget = BackgroundWidget(self.background_for_now())
        root = self.root_widget
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
        self.context_label = QLabel('loading status...')
        self.context_label.setObjectName('contextLabel')
        self.context_label.setWordWrap(True)
        left_layout.addWidget(self.context_label)
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
        self.input_box.setPlaceholderText('Type a message for Monika. Ctrl+Enter to send.')
        self.input_box.setMaximumHeight(92)
        self.input_box.installEventFilter(self)
        right_layout.addWidget(self.input_box)

        button_row = QHBoxLayout()
        self.send_button = QPushButton('Send')
        self.spire_button = QPushButton('/spire')
        self.tts_button = QPushButton('TTS: off')
        self.stop_tts_button = QPushButton('Stop voice')
        self.listen_button = QPushButton('Listen')
        self.data_button = QPushButton('Data')
        self.settings_button = QPushButton('Settings')
        self.clear_button = QPushButton('Clear')
        self.send_button.clicked.connect(self.send_chat)
        self.spire_button.clicked.connect(self.send_spire)
        self.tts_button.clicked.connect(self.toggle_tts)
        self.stop_tts_button.clicked.connect(self.stop_tts)
        self.listen_button.clicked.connect(self.listen_once)
        self.data_button.clicked.connect(self.open_data_manager)
        self.settings_button.clicked.connect(self.open_settings)
        self.clear_button.clicked.connect(self.chat_view.clear)
        button_row.addWidget(self.send_button)
        button_row.addWidget(self.spire_button)
        button_row.addWidget(self.tts_button)
        button_row.addWidget(self.stop_tts_button)
        button_row.addWidget(self.listen_button)
        button_row.addWidget(self.data_button)
        button_row.addWidget(self.settings_button)
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
            self.add_system_message('The backend is still shutting down. Please try closing again in a few seconds.')
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

    def background_for_now(self) -> QPixmap:
        mode = str(self.current_config.get('gui_background_mode') or 'auto')
        return self.assets.background_for_mode(mode, dt.datetime.now().hour)

    def refresh_background(self) -> None:
        self.root_widget.set_background(self.background_for_now())

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
        self.add_system_message('/spire is generating a proactive topic...')
        self.set_busy(True)
        self.spire_requested.emit(hint)

    def _handle_ready(self, result: dict[str, Any]) -> None:
        if result.get('ok'):
            self.add_system_message('Backend engine is ready.')
            self.request_data_snapshot()
        else:
            self.add_system_message(f'Backend initialization failed: {result.get("error", "unknown error")}')

    def _handle_config_ready(self, config: dict[str, Any]) -> None:
        self.current_config = dict(config)
        self.refresh_background()
        self.tts = create_tts(config)
        self.stt = create_stt(config)
        self.tts_enabled = bool(config.get('tts_enabled', False))
        self.tts_button.setText('TTS: on' if self.tts_enabled else 'TTS: off')
        provider = str(config.get('tts_provider') or 'windows_sapi')
        self.add_system_message(f'TTS provider: {provider} · {"on" if self.tts_enabled else "off"}')
        if self.settings_dialog is not None:
            self.settings_dialog.render(self.current_config)

    def toggle_tts(self) -> None:
        self.tts_enabled = not self.tts_enabled
        self.tts_button.setText('TTS: on' if self.tts_enabled else 'TTS: off')
        if not self.tts_enabled:
            self.tts.stop()
        self.add_system_message('TTS enabled.' if self.tts_enabled else 'TTS disabled.')

    def stop_tts(self) -> None:
        self.tts.stop()

    def listen_once(self) -> None:
        if self.stt_busy:
            return
        self.stt_busy = True
        self.listen_button.setEnabled(False)
        self.add_system_message('STT listening...')

        def run() -> None:
            result = self.stt.listen()
            self.stt_finished.emit(result)

        threading.Thread(target=run, daemon=True).start()

    def _handle_stt_finished(self, result: dict[str, Any]) -> None:
        self.stt_busy = False
        self.listen_button.setEnabled(True)
        if result.get('ok'):
            text = str(result.get('text') or '').strip()
            self.input_box.setPlainText(text)
            self.add_system_message(f'STT recognized: {text}')
        else:
            self.add_system_message(f'STT failed: {result.get("error", "unknown error")}')

    def open_data_manager(self) -> None:
        if self.data_dialog is None:
            self.data_dialog = DataManagerDialog(self)
        self.data_dialog.show()
        self.data_dialog.raise_()
        self.data_dialog.activateWindow()
        self.request_data_snapshot()

    def open_settings(self) -> None:
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(self)
        self.settings_dialog.render(self.current_config)
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def request_data_snapshot(self) -> None:
        self.data_snapshot_requested.emit()

    def _handle_data_ready(self, payload: dict[str, Any]) -> None:
        if not payload.get('ok', True):
            self.add_system_message(f'Data manager error: {payload.get("error", "unknown error")}')
            QMessageBox.warning(self, 'Data manager error', str(payload.get('error', 'unknown error')))
            return
        notice = str(payload.get('notice') or '').strip()
        if notice:
            self.add_system_message(notice)
        config = payload.get('config')
        if isinstance(config, dict):
            self.current_config.update(config)
        if payload.get('action') == 'save_config':
            self.tts = create_tts(self.current_config)
            self.stt = create_stt(self.current_config)
            self.tts_enabled = bool(self.current_config.get('tts_enabled', False))
            self.tts_button.setText('TTS: on' if self.tts_enabled else 'TTS: off')
            self.refresh_background()
            self.add_system_message('Settings saved. Restart GUI if you changed core model/API options.')
            if self.settings_dialog is not None:
                self.settings_dialog.render(self.current_config)
        if self.data_dialog is not None:
            self.data_dialog.render(payload)
        self.update_context_label(payload)

    def update_context_label(self, payload: dict[str, Any]) -> None:
        status = payload.get('status') if isinstance(payload.get('status'), dict) else {}
        if not status:
            return
        now = dt.datetime.now()
        self.refresh_background()
        events = status.get('today_events') if isinstance(status.get('today_events'), list) else []
        event_names: list[str] = []
        for event in events:
            if isinstance(event, dict):
                name = str(event.get('name') or '').strip()
                if name:
                    event_names.append(name)
        event_text = 'today: ' + ', '.join(event_names) if event_names else 'ordinary day'
        bg_mode = str(self.current_config.get('gui_background_mode') or 'auto')
        self.context_label.setText(
            f"{now.strftime('%Y-%m-%d %H:%M')} | bg {bg_mode} | affection {status.get('affection', '?')} | "
            f"{status.get('relationship_stage', 'relationship')} | {event_text}"
        )

    def _handle_result(self, result: dict[str, Any]) -> None:
        self.set_busy(False)
        if not result.get('ok'):
            self.set_emotion('concerned')
            self.add_system_message(f'Request failed: {result.get("error", "unknown error")}')
            return

        visual_state = self.visual_state_from_result(result)
        reply_text = str(result.get('text') or '')
        emotion = str(visual_state.get('raw_emotion') or 'neutral')
        self.set_emotion(str(visual_state.get('emotion') or emotion))
        self.add_monika_message(reply_text, emotion, result.get('response_time', ''))
        if self.tts_enabled and reply_text:
            self.add_system_message('TTS is synthesizing/playing...')
            self.tts.speak(reply_text)
            QTimer.singleShot(1800, self.report_tts_error_if_any)
            QTimer.singleShot(6000, self.report_tts_error_if_any)
        for notice in result.get('mtrigger_notices') or []:
            self.chat_view.append(f'<div class="notice">{html_escape(str(notice))}</div>')

    def report_tts_error_if_any(self) -> None:
        error = str(getattr(self.tts, 'last_error', '') or '').strip()
        if error and error != self.last_tts_error:
            self.last_tts_error = error
            self.add_system_message(f'TTS error: {error}')


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
QLabel#contextLabel {
    color: #f2e4d7;
    background: rgba(0, 0, 0, 74);
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
    parser = argparse.ArgumentParser(description='MAICA GUI')
    parser.add_argument('--config', default='', help='Optional path to config.json')
    parser.add_argument('--db', default='', help='Optional path to SQLite database')
    parser.add_argument('--safe-test-db', action='store_true', help='Use an isolated GUI test database')
    args, qt_args = parser.parse_known_args()

    config_path = Path(args.config).resolve() if args.config else None
    db_path = Path(args.db).resolve() if args.db else None
    if args.safe_test_db:
        safe_dir = GUI_DIR / '.safe_test'
        safe_dir.mkdir(parents=True, exist_ok=True)
        db_path = safe_dir / 'maica_cli_test.db'

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName('MAICA GUI')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    window = MainWindow(config_path=config_path, db_path=db_path, safe_test_mode=args.safe_test_db)
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
