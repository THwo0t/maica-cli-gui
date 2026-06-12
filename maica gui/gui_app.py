#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MAICA GUI v0.10.4.1.

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
    QScrollArea,
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
APP_VERSION = '0.10.4.1'

if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

from assets import AssetManager, normalize_emotion  # noqa: E402
from diagnostics import collect_report  # noqa: E402
from engine_worker import GuiEngineWorker  # noqa: E402
from stt import create_stt  # noqa: E402
from tts import create_tts  # noqa: E402


PROFILE_FIELDS = ('player_name', 'birthday', 'location', 'nicknames', 'affection')
LANGUAGE_OPTIONS = ('en', 'zh')
MODE_OPTIONS = ('rule', 'off')
PLANNER_MODES = ('lite', 'example_only')
RESPONSE_OUTPUT_MODES = ('dual', 'json', 'legacy_marker')
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
        summarize = QPushButton('Summarize recent memory')
        summarize.clicked.connect(self.owner.summarize_memory_requested.emit)
        layout.addWidget(summarize)
        data_row = QHBoxLayout()
        export_data = QPushButton('Export user data')
        import_data = QPushButton('Import user data')
        export_data.clicked.connect(self.export_user_data)
        import_data.clicked.connect(self.import_user_data)
        data_row.addWidget(export_data)
        data_row.addWidget(import_data)
        layout.addLayout(data_row)
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
                'summaries': len(snapshot.get('summaries') or []),
                'events': len(snapshot.get('events') or []),
            },
            'token_usage': snapshot.get('token_usage', {}),
            'recent_events': snapshot.get('events', [])[:8],
            'recent_summaries': snapshot.get('summaries', [])[:5],
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

    def export_user_data(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, 'Export user data', 'maica_user_data.zip', 'ZIP (*.zip)')
        if path:
            self.owner.user_data_export_requested.emit(path)

    def import_user_data(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, 'Import user data', '', 'ZIP (*.zip)')
        if not path:
            return
        if QMessageBox.question(self, 'Import user data', 'Import and append this user data? A backup will be created first.') == QMessageBox.StandardButton.Yes:
            self.owner.user_data_import_requested.emit(path, False)


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
        self.frequency_penalty = QDoubleSpinBox()
        self.frequency_penalty.setRange(-2.0, 2.0)
        self.frequency_penalty.setSingleStep(0.01)
        self.frequency_penalty.setDecimals(2)
        self.presence_penalty = QDoubleSpinBox()
        self.presence_penalty.setRange(-2.0, 2.0)
        self.presence_penalty.setSingleStep(0.01)
        self.presence_penalty.setDecimals(2)
        self.streaming_enabled = QCheckBox('Enable streaming when supported')
        self.response_output_mode = QComboBox()
        self.response_output_mode.addItems(RESPONSE_OUTPUT_MODES)
        self.metadata_extract_enabled = QCheckBox('Extract emotion/action metadata with a second light call')
        self.response_planner_mode = QComboBox()
        self.response_planner_mode.addItems(PLANNER_MODES)
        self.example_bank_limit = QSpinBox()
        self.example_bank_limit.setRange(0, 5)
        self.example_bank_min_score = QDoubleSpinBox()
        self.example_bank_min_score.setRange(0, 500)
        self.example_bank_min_score.setSingleStep(5)
        self.example_bank_weight = QDoubleSpinBox()
        self.example_bank_weight.setRange(0.0, 2.0)
        self.example_bank_weight.setSingleStep(0.05)
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
        self.gui_idle_spire_enabled = QCheckBox('Enable idle proactive talk')
        self.gui_idle_spire_minutes = QSpinBox()
        self.gui_idle_spire_minutes.setRange(1, 240)
        self.gui_startup_greeting_enabled = QCheckBox('Show startup greeting')
        self.auto_memory_summary_enabled = QCheckBox('Enable automatic memory summaries')
        self.auto_memory_summary_turns = QSpinBox()
        self.auto_memory_summary_turns.setRange(4, 200)

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
        form.addRow('Frequency penalty', self.frequency_penalty)
        form.addRow('Presence penalty', self.presence_penalty)
        form.addRow('', self.streaming_enabled)
        form.addRow('Response output', self.response_output_mode)
        form.addRow('', self.metadata_extract_enabled)
        form.addRow('Planner mode', self.response_planner_mode)
        form.addRow('Example limit', self.example_bank_limit)
        form.addRow('Example min score', self.example_bank_min_score)
        form.addRow('Example weight', self.example_bank_weight)
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
        form.addRow('', self.gui_idle_spire_enabled)
        form.addRow('Idle minutes', self.gui_idle_spire_minutes)
        form.addRow('', self.gui_startup_greeting_enabled)
        form.addRow('', self.auto_memory_summary_enabled)
        form.addRow('Summary turns', self.auto_memory_summary_turns)
        form.addRow('', self.tts_enabled)
        form.addRow('TTS provider', self.tts_provider)
        form.addRow('Bailian model', self.tts_model)
        form.addRow('Bailian voice', self.tts_voice)
        form.addRow('Bailian format', self.tts_format)
        form.addRow('Bailian instruction', self.tts_instruction)
        form.addRow('STT provider', self.stt_provider)
        form.addRow('STT language', self.stt_language)
        form.addRow('STT timeout', self.stt_timeout)

        form_host = QWidget()
        form_host.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(form_host)
        layout.addWidget(scroll, 1)

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
        self.frequency_penalty.setValue(float(config.get('frequency_penalty') or 0.0))
        self.presence_penalty.setValue(float(config.get('presence_penalty') or 0.0))
        self.streaming_enabled.setChecked(bool(config.get('streaming_enabled', False)))
        self._set_combo(self.response_output_mode, str(config.get('response_output_mode') or 'dual'))
        self.metadata_extract_enabled.setChecked(bool(config.get('metadata_extract_enabled', True)))
        self._set_combo(self.response_planner_mode, str(config.get('response_planner_mode') or 'lite'))
        self.example_bank_limit.setValue(int(config.get('example_bank_limit') or 3))
        self.example_bank_min_score.setValue(float(config.get('example_bank_min_score') or 150))
        self.example_bank_weight.setValue(float(config.get('example_bank_weight') or 0.65))
        self._set_combo(self.mfocus_mode, str(config.get('mfocus_mode') or 'rule'))
        self._set_combo(self.mtrigger_mode, str(config.get('mtrigger_mode') or 'rule'))
        self.show_debug.setChecked(bool(config.get('show_debug', True)))
        self.embedding_enabled.setChecked(bool(config.get('embedding_enabled', False)))
        self.memory_embedding_enabled.setChecked(bool(config.get('memory_embedding_enabled', False)))
        self.embedding_service_enabled.setChecked(bool(config.get('embedding_service_enabled', False)))
        self.embedding_service_autostart.setChecked(bool(config.get('embedding_service_autostart', True)))
        self.embedding_service_port.setValue(int(config.get('embedding_service_port') or 8766))
        self.gui_disable_thread_embeddings.setChecked(bool(config.get('gui_disable_thread_embeddings', True)))
        self._set_combo(self.gui_background_mode, str(config.get('gui_background_mode') or 'auto'))
        self.gui_idle_spire_enabled.setChecked(bool(config.get('gui_idle_spire_enabled', False)))
        self.gui_idle_spire_minutes.setValue(int(config.get('gui_idle_spire_minutes') or 12))
        self.gui_startup_greeting_enabled.setChecked(bool(config.get('gui_startup_greeting_enabled', True)))
        self.auto_memory_summary_enabled.setChecked(bool(config.get('auto_memory_summary_enabled', False)))
        self.auto_memory_summary_turns.setValue(int(config.get('auto_memory_summary_turns') or 24))
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
            'frequency_penalty': self.frequency_penalty.value(),
            'presence_penalty': self.presence_penalty.value(),
            'streaming_enabled': self.streaming_enabled.isChecked(),
            'response_output_mode': self.response_output_mode.currentText(),
            'metadata_extract_enabled': self.metadata_extract_enabled.isChecked(),
            'response_planner_mode': self.response_planner_mode.currentText(),
            'example_bank_limit': self.example_bank_limit.value(),
            'example_bank_min_score': self.example_bank_min_score.value(),
            'example_bank_weight': self.example_bank_weight.value(),
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
            'gui_idle_spire_enabled': self.gui_idle_spire_enabled.isChecked(),
            'gui_idle_spire_minutes': self.gui_idle_spire_minutes.value(),
            'gui_startup_greeting_enabled': self.gui_startup_greeting_enabled.isChecked(),
            'auto_memory_summary_enabled': self.auto_memory_summary_enabled.isChecked(),
            'auto_memory_summary_turns': self.auto_memory_summary_turns.value(),
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
    user_data_export_requested = Signal(str)
    user_data_import_requested = Signal(str, bool)
    summarize_memory_requested = Signal()
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
        self.last_user_activity = dt.datetime.now()
        self.idle_spire_sent = False
        self.idle_timer = QTimer(self)
        self.idle_timer.setInterval(30_000)
        self.idle_timer.timeout.connect(self.check_idle_spire)

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
        self.idle_timer.start()

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
        self.user_data_export_requested.connect(self.worker.export_user_data)
        self.user_data_import_requested.connect(self.worker.import_user_data)
        self.summarize_memory_requested.connect(self.worker.summarize_memory)
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
        self.chat_view.document().setDefaultStyleSheet(CHAT_HTML_STYLE)
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
        self.diagnostics_button = QPushButton('Diagnostics')
        self.debug_button = QPushButton('Debug')
        self.clear_button = QPushButton('Clear')
        self.send_button.clicked.connect(self.send_chat)
        self.spire_button.clicked.connect(self.send_spire)
        self.tts_button.clicked.connect(self.toggle_tts)
        self.stop_tts_button.clicked.connect(self.stop_tts)
        self.listen_button.clicked.connect(self.listen_once)
        self.data_button.clicked.connect(self.open_data_manager)
        self.settings_button.clicked.connect(self.open_settings)
        self.diagnostics_button.clicked.connect(self.export_diagnostics)
        self.debug_button.clicked.connect(self.toggle_debug_panel)
        self.clear_button.clicked.connect(self.chat_view.clear)
        button_row.addWidget(self.send_button)
        button_row.addWidget(self.spire_button)
        button_row.addWidget(self.tts_button)
        button_row.addWidget(self.stop_tts_button)
        button_row.addWidget(self.listen_button)
        right_layout.addLayout(button_row)

        button_row2 = QHBoxLayout()
        button_row2.addWidget(self.data_button)
        button_row2.addWidget(self.settings_button)
        button_row2.addWidget(self.diagnostics_button)
        button_row2.addWidget(self.debug_button)
        button_row2.addWidget(self.clear_button)
        right_layout.addLayout(button_row2)

        self.debug_panel = QPlainTextEdit()
        self.debug_panel.setObjectName('debugPanel')
        self.debug_panel.setReadOnly(True)
        self.debug_panel.setMaximumHeight(150)
        self.debug_panel.setVisible(False)
        right_layout.addWidget(self.debug_panel)

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
        self.idle_timer.stop()
        self.shutdown_requested.emit()
        self.thread.quit()
        if not self.thread.wait(5000):
            self.add_system_message('Backend shutdown timed out; forcing GUI thread cleanup.')
            self.thread.terminate()
            self.thread.wait(2000)
        super().closeEvent(event)

    def add_system_message(self, text: str) -> None:
        self.chat_view.append(f'<div class="system">{html_escape(text)}</div>')

    def add_user_message(self, text: str) -> None:
        self.chat_view.append(f'<div class="user"><b>you</b><br>{html_escape(text)}</div>')

    def add_monika_message(self, text: str, emotion: str, response_time: Any = '') -> None:
        lines = '<br>'.join(html_escape(line) for line in text.splitlines() if line.strip())
        meta = f'[emotion: {html_escape(emotion or "neutral")}]'
        if response_time != '':
            meta += f' [time: {response_time}s]'
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
        self.last_user_activity = dt.datetime.now()
        self.idle_spire_sent = False
        self.input_box.clear()
        self.add_user_message(text)
        self.set_busy(True)
        self.chat_requested.emit(text)

    def send_spire(self) -> None:
        hint = self.input_box.toPlainText().strip()
        self.last_user_activity = dt.datetime.now()
        self.idle_spire_sent = False
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
        if config.get('gui_startup_greeting_enabled', True):
            self.add_system_message('Monika is awake. Recent history will be loaded if available.')

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

    def toggle_debug_panel(self) -> None:
        self.debug_panel.setVisible(not self.debug_panel.isVisible())

    def update_debug_panel(self, result: dict[str, Any]) -> None:
        debug = result.get('debug') if isinstance(result.get('debug'), dict) else {}
        plan = debug.get('mfocus_plan') if isinstance(debug.get('mfocus_plan'), dict) else {}
        response_plan = plan.get('response_plan') if isinstance(plan.get('response_plan'), dict) else {}
        style = plan.get('style') if isinstance(plan.get('style'), dict) else {}
        example_bank = response_plan.get('example_bank') if isinstance(response_plan.get('example_bank'), dict) else {}
        summaries = response_plan.get('example_summaries') if isinstance(response_plan.get('example_summaries'), list) else []
        lines = [
            f"source: {result.get('source', 'chat')} | ok: {result.get('ok')}",
            f"emotion: {result.get('emotion', '')} | response_time: {result.get('response_time', '')}s",
            f"category: {response_plan.get('category', '')} | intent: {response_plan.get('intent', '')}",
            f"mode: {response_plan.get('mode', '')} | length: {response_plan.get('length', '')}",
            f"style: {style.get('category', '')} | max_sentences: {style.get('max_sentences', '')}",
            f"examples: {len(summaries)} | retrieval: {example_bank.get('retrieval_mode', '')}",
        ]
        for index, item in enumerate(summaries[:3], start=1):
            if isinstance(item, dict):
                lines.append(
                    f"example {index}: {item.get('source', '')} | {item.get('intent', '')} | "
                    f"score={item.get('score', '')} | vector={item.get('vector_similarity', '')}"
                )
        self.debug_panel.setPlainText('\n'.join(lines))

    def export_diagnostics(self) -> None:
        default_name = f"maica_diagnostics_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        target, _selected = QFileDialog.getSaveFileName(
            self,
            'Export diagnostics',
            str(Path.home() / default_name),
            'JSON files (*.json);;All files (*.*)',
        )
        if not target:
            return
        try:
            import json

            report = collect_report()
            Path(target).write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding='utf-8')
            self.add_system_message(f'Diagnostics exported: {target}')
        except Exception as exc:
            self.add_system_message(f'Diagnostics export failed: {exc}')
            QMessageBox.warning(self, 'Diagnostics export failed', str(exc))

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
            self.add_system_message('Settings applied. New chat requests will use the updated options.')
            if self.settings_dialog is not None:
                self.settings_dialog.render(self.current_config)
        if self.data_dialog is not None:
            self.data_dialog.render(payload)
        self.update_context_label(payload)
        if payload.get('action') in {'snapshot', ''}:
            self.load_recent_messages_once(payload)

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
        self.last_user_activity = dt.datetime.now()
        if not result.get('ok'):
            self.set_emotion('concerned')
            self.add_system_message(f'Request failed: {result.get("error", "unknown error")}')
            return

        visual_state = self.visual_state_from_result(result)
        reply_text = str(result.get('text') or '')
        emotion = str(visual_state.get('raw_emotion') or 'neutral')
        self.set_emotion(str(visual_state.get('emotion') or emotion))
        self.add_monika_message(reply_text, emotion, result.get('response_time', ''))
        self.update_debug_panel(result)
        if self.tts_enabled and reply_text:
            self.add_system_message('TTS is synthesizing/playing...')
            self.tts.speak(reply_text)
            QTimer.singleShot(1800, self.report_tts_error_if_any)
            QTimer.singleShot(6000, self.report_tts_error_if_any)
        for notice in result.get('mtrigger_notices') or []:
            self.chat_view.append(f'<div class="notice">{html_escape(str(notice))}</div>')

    def load_recent_messages_once(self, payload: dict[str, Any]) -> None:
        if getattr(self, '_recent_loaded', False):
            return
        self._recent_loaded = True
        messages = payload.get('recent_messages') if isinstance(payload.get('recent_messages'), list) else []
        if not messages:
            return
        self.add_system_message('Loaded recent conversation history.')
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get('role') or '')
            content = str(message.get('content') or '')
            if role == 'user':
                self.add_user_message(content)
            elif role == 'assistant':
                self.add_monika_message(content, 'neutral')

    def check_idle_spire(self) -> None:
        if not self.current_config.get('gui_idle_spire_enabled', False):
            return
        if self.idle_spire_sent or not self.input_box.isEnabled() or self.input_box.toPlainText().strip():
            return
        minutes = int(self.current_config.get('gui_idle_spire_minutes') or 12)
        if (dt.datetime.now() - self.last_user_activity).total_seconds() >= minutes * 60:
            self.idle_spire_sent = True
            self.add_system_message('Idle proactive talk is starting...')
            self.set_busy(True)
            self.spire_requested.emit('')

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
QPlainTextEdit#debugPanel {
    background: rgba(22, 26, 31, 210);
    color: #d8e8db;
    border: 1px solid rgba(255, 255, 255, 54);
    border-radius: 12px;
    padding: 8px;
    font-family: Consolas, "Courier New", monospace;
    font-size: 12px;
}
QPushButton {
    background: #8d4d5a;
    color: #fff8f4;
    border: none;
    border-radius: 12px;
    padding: 8px 10px;
    font-size: 14px;
}
QPushButton:hover {
    background: #a65f6d;
}
QPushButton:disabled {
    background: #6a6267;
    color: #d1c5c1;
}
"""

CHAT_HTML_STYLE = """
.system {
    color: #5b6670;
    margin: 8px 0;
    font-size: 12px;
}
.user {
    background: #eef4ff;
    border-radius: 10px;
    margin: 8px 0 8px 36px;
    padding: 8px 10px;
}
.monika {
    background: #fff6f0;
    border-radius: 10px;
    margin: 8px 36px 8px 0;
    padding: 8px 10px;
}
.monika span {
    color: #8f7a70;
    font-size: 11px;
}
.notice {
    color: #7a5c2d;
    background: #fff5d8;
    border-radius: 8px;
    margin: 6px 0;
    padding: 5px 8px;
    font-size: 12px;
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
