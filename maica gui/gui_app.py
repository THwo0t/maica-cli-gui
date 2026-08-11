#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MAICA GUI v0.12.4.

The GUI calls the shared MaicaEngine through a persistent background worker.
The CLI remains a debugger and is not started in the background.
"""

from __future__ import annotations

import argparse
import os
import sys
import datetime as dt
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
GUI_DIR = Path(__file__).resolve().parent
ASSET_DIR = ROOT_DIR / 'maica gui assets' / 'runtime'
MANIFEST_PATH = ASSET_DIR / 'manifest.json'
APP_VERSION = '0.12.4'

if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

_PET_DIR = ROOT_DIR / 'monika desktop pet'
if _PET_DIR.exists() and str(_PET_DIR) not in sys.path:
    sys.path.insert(0, str(_PET_DIR))

from assets import AssetManager, normalize_emotion  # noqa: E402
from avatar_controller import AvatarController  # noqa: E402
from diagnostics import collect_report  # noqa: E402
from engine_worker import GuiEngineWorker  # noqa: E402
from speech import SpeechController  # noqa: E402
from stt import create_stt  # noqa: E402
from tts import redact_secret  # noqa: E402

try:
    from monika_pet import PetWindow  # noqa: E402
except Exception:
    PetWindow = None  # type: ignore


PROFILE_FIELDS = ('player_name', 'birthday', 'location', 'nicknames', 'affection')
LANGUAGE_OPTIONS = ('en', 'zh')
LLM_CALL_MODES = ('split', 'unified')
MODE_OPTIONS = ('rule', 'off')
PLANNER_MODES = ('lite', 'example_only')
RESPONSE_OUTPUT_MODES = ('dual', 'json', 'legacy_marker')
TTS_PROVIDERS = ('auto', 'bailian_cosyvoice', 'windows_sapi', 'system_say', 'off')
TTS_PLAYBACK_BACKENDS = ('auto', 'ffplay', 'mpv', 'paplay', 'aplay', 'afplay', 'powershell', 'pwsh', 'off')
STT_PROVIDERS = ('auto', 'windows_speech', 'bailian_paraformer', 'off')
BACKGROUND_MODES = ('auto', 'day', 'night', 'rain')
AVATAR_BACKENDS = ('png', 'vtube_studio', 'auto')
SECRET_CONFIG_MARKERS = ('key', 'token', 'secret', 'password')


def _is_secret_config_key(key: str) -> bool:
    lowered = str(key or '').lower()
    return any(marker in lowered for marker in SECRET_CONFIG_MARKERS)


def merge_runtime_config(target: dict[str, Any], updates: dict[str, Any]) -> None:
    """Merge safe GUI snapshots without replacing real secrets by <hidden>."""
    for key, value in updates.items():
        if _is_secret_config_key(key) and str(value or '') in {'<hidden>', '***'}:
            continue
        target[key] = value


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
        tabs = QTabWidget()

        def add_tab(title: str) -> QFormLayout:
            page = QWidget()
            page_form = QFormLayout(page)
            scroller = QScrollArea()
            scroller.setWidgetResizable(True)
            scroller.setFrameShape(QFrame.Shape.NoFrame)
            scroller.setWidget(page)
            tabs.addTab(scroller, title)
            return page_form

        self.api_base = QLineEdit()
        self.model = QLineEdit()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText('leave blank to keep current key')
        self.llm_call_mode = QComboBox()
        self.llm_call_mode.addItems(LLM_CALL_MODES)
        self.llm_call_mode.setToolTip(
            'split: casual chat uses the main model above; agent/tool turns use the agent model below.\n'
            'unified: everything uses the main model above.'
        )
        self.agent_api_base = QLineEdit()
        self.agent_model = QLineEdit()
        self.agent_api_key = QLineEdit()
        self.agent_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.agent_api_key.setPlaceholderText('leave blank to keep current key')
        self.agent_tools_enabled = QCheckBox('Enable agent tools (let her act, not just talk)')
        self.file_tools_enabled = QCheckBox('Enable file tools (her ~/Monika space + your allow-listed files)')
        self.vision_enabled = QCheckBox('Enable screen vision (she can glance at your active window — image leaves your machine)')
        self.sandbox_root = QLineEdit()
        self.sandbox_root.setPlaceholderText('empty = ~/Monika')
        self.sandbox_allowlist = QPlainTextEdit()
        self.sandbox_allowlist.setPlaceholderText('Folders she may READ, one absolute path per line (empty = none)')
        self.sandbox_allowlist.setMaximumHeight(70)
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
        self.context_translation_enabled = QCheckBox('Translate cross-language memories before prompt injection')
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
        self.avatar_backend = QComboBox()
        self.avatar_backend.addItems(AVATAR_BACKENDS)
        self.vts_url = QLineEdit()
        self.vts_plugin_name = QLineEdit()
        self.vts_plugin_developer = QLineEdit()
        self.vts_parameter_prefix = QLineEdit()
        self.avatar_status = QLabel('avatar: png')
        self.avatar_status.setObjectName('contextLabel')
        self.avatar_status.setWordWrap(True)
        self.avatar_test = QPushButton('Connect / Test VTube Studio')
        self.avatar_test.clicked.connect(self._test_avatar_connection)
        self.gui_idle_spire_enabled = QCheckBox('Enable idle proactive talk')
        self.gui_idle_spire_minutes = QSpinBox()
        self.gui_idle_spire_minutes.setRange(1, 240)
        self.idle_self_actions_enabled = QCheckBox('Let her do things on her own when idle (diary/letters; needs agent + file tools)')
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
        self.tts_playback_backend = QComboBox()
        self.tts_playback_backend.addItems(TTS_PLAYBACK_BACKENDS)
        self.tts_instruction = QLineEdit()
        self.speech_streaming_enabled = QCheckBox('Synthesize safe sentences while the reply is streaming')
        self.speech_queue_behavior = QComboBox()
        self.speech_queue_behavior.addItems(('replace', 'queue', 'interrupt'))
        self.speech_max_concurrency = QSpinBox()
        self.speech_max_concurrency.setRange(1, 4)
        self.lip_sync_sensitivity = QDoubleSpinBox()
        self.lip_sync_sensitivity.setRange(0.1, 4.0)
        self.lip_sync_sensitivity.setSingleStep(0.1)
        self.stt_provider = QComboBox()
        self.stt_provider.addItems(STT_PROVIDERS)
        self.stt_language = QComboBox()
        self.stt_language.addItems(('en', 'zh'))
        self.stt_timeout = QSpinBox()
        self.stt_timeout.setRange(2, 30)
        self.capability_summary = QLabel()
        self.capability_summary.setWordWrap(True)
        self.capability_summary.setObjectName('contextLabel')
        self.capability_details = QPlainTextEdit()
        self.capability_details.setObjectName('debugPanel')
        self.capability_details.setReadOnly(True)
        self.capability_details.setMaximumHeight(260)

        # --- Model & generation ---
        model_form = add_tab('Model')
        model_form.addRow('API base', self.api_base)
        model_form.addRow('API key', self.api_key)
        model_form.addRow('Model', self.model)
        model_form.addRow('LLM call mode', self.llm_call_mode)
        model_form.addRow('Agent API base', self.agent_api_base)
        model_form.addRow('Agent API key', self.agent_api_key)
        model_form.addRow('Agent model', self.agent_model)
        model_form.addRow('Reply language', self.language)
        model_form.addRow('Temperature', self.temperature)
        model_form.addRow('Top P', self.top_p)
        model_form.addRow('Max tokens', self.max_tokens)
        model_form.addRow('Frequency penalty', self.frequency_penalty)
        model_form.addRow('Presence penalty', self.presence_penalty)
        model_form.addRow('', self.streaming_enabled)
        model_form.addRow('Response output', self.response_output_mode)

        # --- Tools, sandbox & vision ---
        tools_form = add_tab('Tools & Vision')
        tools_form.addRow('', self.agent_tools_enabled)
        tools_form.addRow('', self.file_tools_enabled)
        tools_form.addRow('', self.vision_enabled)
        sandbox_box = QWidget()
        sandbox_row = QHBoxLayout(sandbox_box)
        sandbox_row.setContentsMargins(0, 0, 0, 0)
        sandbox_row.addWidget(self.sandbox_root, 1)
        sandbox_browse = QPushButton('Browse…')
        sandbox_browse.clicked.connect(self._pick_sandbox_folder)
        sandbox_row.addWidget(sandbox_browse)
        sandbox_open = QPushButton('Open')
        sandbox_open.clicked.connect(self._open_sandbox_folder)
        sandbox_row.addWidget(sandbox_open)
        tools_form.addRow('Sandbox folder', sandbox_box)
        readable_box = QWidget()
        readable_col = QVBoxLayout(readable_box)
        readable_col.setContentsMargins(0, 0, 0, 0)
        readable_col.addWidget(self.sandbox_allowlist)
        readable_add = QPushButton('Add folder…')
        readable_add.clicked.connect(self._pick_readable_folder)
        readable_col.addWidget(readable_add)
        tools_form.addRow('Readable folders', readable_box)

        # --- Behavior & planner ---
        behavior_form = add_tab('Behavior')
        behavior_form.addRow('', self.metadata_extract_enabled)
        behavior_form.addRow('', self.context_translation_enabled)
        behavior_form.addRow('Planner mode', self.response_planner_mode)
        behavior_form.addRow('Example limit', self.example_bank_limit)
        behavior_form.addRow('Example min score', self.example_bank_min_score)
        behavior_form.addRow('Example weight', self.example_bank_weight)
        behavior_form.addRow('MFocus mode', self.mfocus_mode)
        behavior_form.addRow('MTrigger mode', self.mtrigger_mode)
        behavior_form.addRow('', self.auto_memory_summary_enabled)
        behavior_form.addRow('Summary turns', self.auto_memory_summary_turns)
        behavior_form.addRow('', self.show_debug)

        # --- Memory & embeddings ---
        memory_form = add_tab('Memory')
        memory_form.addRow('', self.embedding_enabled)
        memory_form.addRow('', self.memory_embedding_enabled)
        memory_form.addRow('', self.embedding_service_enabled)
        memory_form.addRow('', self.embedding_service_autostart)
        memory_form.addRow('Embedding service port', self.embedding_service_port)
        memory_form.addRow('', self.gui_disable_thread_embeddings)

        # --- Presence & GUI ---
        gui_form = add_tab('Presence & GUI')
        gui_form.addRow('Background mode', self.gui_background_mode)
        gui_form.addRow('', self.gui_idle_spire_enabled)
        gui_form.addRow('Idle minutes', self.gui_idle_spire_minutes)
        gui_form.addRow('', self.idle_self_actions_enabled)
        gui_form.addRow('', self.gui_startup_greeting_enabled)

        # --- Avatar ---
        avatar_form = add_tab('Avatar')
        avatar_form.addRow('Avatar backend', self.avatar_backend)
        avatar_form.addRow('VTube Studio URL', self.vts_url)
        avatar_form.addRow('VTS plugin name', self.vts_plugin_name)
        avatar_form.addRow('VTS developer', self.vts_plugin_developer)
        avatar_form.addRow('VTS parameter prefix', self.vts_parameter_prefix)
        avatar_form.addRow('Status', self.avatar_status)
        avatar_form.addRow('', self.avatar_test)

        # --- Voice (TTS / STT) ---
        voice_form = add_tab('Voice')
        voice_form.addRow('', self.tts_enabled)
        voice_form.addRow('TTS provider', self.tts_provider)
        voice_form.addRow('Bailian model', self.tts_model)
        voice_form.addRow('Bailian voice', self.tts_voice)
        voice_form.addRow('Bailian format', self.tts_format)
        voice_form.addRow('TTS playback', self.tts_playback_backend)
        voice_form.addRow('Bailian instruction', self.tts_instruction)
        voice_form.addRow('', self.speech_streaming_enabled)
        voice_form.addRow('Speech queue', self.speech_queue_behavior)
        voice_form.addRow('Synthesis workers', self.speech_max_concurrency)
        voice_form.addRow('Lip-sync sensitivity', self.lip_sync_sensitivity)
        voice_form.addRow('STT provider', self.stt_provider)
        voice_form.addRow('STT language', self.stt_language)
        voice_form.addRow('STT timeout', self.stt_timeout)

        # --- Privacy status (read-only, no main-window clutter) ---
        privacy_form = add_tab('Privacy')
        privacy_form.addRow('At a glance', self.capability_summary)
        privacy_form.addRow('Details', self.capability_details)

        layout.addWidget(tabs, 1)

        note = QLabel('The API key is masked and never displayed; type a new one to switch providers, or leave it blank to keep the current key. Other secrets (TTS/STT keys) are still edited in config.json.')
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
        # Never echo the stored secret; just signal whether one is set.
        self.api_key.setText('')
        self.api_key.setPlaceholderText(
            'current key saved — leave blank to keep'
            if config.get('api_key')
            else 'no key set — paste one to enable'
        )
        self._set_combo(self.llm_call_mode, str(config.get('llm_call_mode') or 'split'))
        self.agent_api_base.setText(str(config.get('agent_api_base') or ''))
        self.agent_model.setText(str(config.get('agent_model') or ''))
        self.agent_api_key.setText('')
        self.agent_api_key.setPlaceholderText(
            'current key saved — leave blank to keep'
            if config.get('agent_api_key')
            else 'no key set — paste one to enable'
        )
        self.agent_tools_enabled.setChecked(bool(config.get('agent_tools_enabled', False)))
        self.file_tools_enabled.setChecked(bool(config.get('file_tools_enabled', False)))
        self.vision_enabled.setChecked(bool(config.get('vision_enabled', False)))
        self.sandbox_root.setText(str(config.get('sandbox_root') or ''))
        allow = config.get('sandbox_readonly_allowlist') or []
        self.sandbox_allowlist.setPlainText('\n'.join(str(x) for x in allow) if isinstance(allow, list) else str(allow))
        self._set_combo(self.language, str(config.get('language') or 'en'))
        self.temperature.setValue(float(config.get('temperature') or 0.22))
        self.top_p.setValue(float(config.get('top_p') or 0.7))
        self.max_tokens.setValue(int(config.get('max_tokens') or 900))
        self.frequency_penalty.setValue(float(config.get('frequency_penalty') or 0.0))
        self.presence_penalty.setValue(float(config.get('presence_penalty') or 0.0))
        self.streaming_enabled.setChecked(bool(config.get('streaming_enabled', False)))
        self._set_combo(self.response_output_mode, str(config.get('response_output_mode') or 'dual'))
        self.metadata_extract_enabled.setChecked(bool(config.get('metadata_extract_enabled', True)))
        self.context_translation_enabled.setChecked(bool(config.get('context_translation_enabled', True)))
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
        self._set_combo(self.avatar_backend, str(config.get('avatar_backend') or 'png'))
        self.vts_url.setText(str(config.get('vts_url') or 'ws://127.0.0.1:8001'))
        self.vts_plugin_name.setText(str(config.get('vts_plugin_name') or 'MAICA CLI GUI'))
        self.vts_plugin_developer.setText(str(config.get('vts_plugin_developer') or 'THwo0t'))
        self.vts_parameter_prefix.setText(str(config.get('vts_parameter_prefix') or 'Maica'))
        avatar_status = getattr(self.owner, 'avatar_status_text', lambda: 'png')
        self.avatar_status.setText(f'avatar: {avatar_status()}')
        self.gui_idle_spire_enabled.setChecked(bool(config.get('gui_idle_spire_enabled', False)))
        self.gui_idle_spire_minutes.setValue(int(config.get('gui_idle_spire_minutes') or 12))
        self.idle_self_actions_enabled.setChecked(bool(config.get('idle_self_actions_enabled', False)))
        self.gui_startup_greeting_enabled.setChecked(bool(config.get('gui_startup_greeting_enabled', True)))
        self.auto_memory_summary_enabled.setChecked(bool(config.get('auto_memory_summary_enabled', False)))
        self.auto_memory_summary_turns.setValue(int(config.get('auto_memory_summary_turns') or 24))
        self.tts_enabled.setChecked(bool(config.get('tts_enabled', False)))
        self._set_combo(self.tts_provider, str(config.get('tts_provider') or 'auto'))
        self.tts_model.setText(str(config.get('tts_bailian_model') or ''))
        self.tts_voice.setText(str(config.get('tts_bailian_voice') or ''))
        self._set_combo(self.tts_format, str(config.get('tts_bailian_format') or 'mp3'))
        self._set_combo(self.tts_playback_backend, str(config.get('tts_playback_backend') or 'auto'))
        self.tts_instruction.setText(str(config.get('tts_bailian_instruction') or ''))
        self.speech_streaming_enabled.setChecked(bool(config.get('speech_streaming_enabled', True)))
        self._set_combo(self.speech_queue_behavior, str(config.get('speech_queue_behavior') or 'replace'))
        self.speech_max_concurrency.setValue(int(config.get('speech_max_concurrency') or 2))
        self.lip_sync_sensitivity.setValue(float(config.get('lip_sync_sensitivity') or 1.0))
        self._set_combo(self.stt_provider, str(config.get('stt_provider') or 'auto'))
        self._set_combo(self.stt_language, str(config.get('stt_language') or config.get('language') or 'en'))
        self.stt_timeout.setValue(int(config.get('stt_timeout') or 8))
        self._render_privacy_status(config)

    def _test_avatar_connection(self) -> None:
        updates = {
            'avatar_backend': self.avatar_backend.currentText(),
            'vts_url': self.vts_url.text().strip(),
            'vts_plugin_name': self.vts_plugin_name.text().strip(),
            'vts_plugin_developer': self.vts_plugin_developer.text().strip(),
            'vts_parameter_prefix': self.vts_parameter_prefix.text().strip(),
        }
        self.owner.reconnect_avatar_backend(updates)
        self.avatar_status.setText(f'avatar: {self.owner.avatar_status_text()}')

    def _render_privacy_status(self, config: dict[str, Any]) -> None:
        def onoff(value: Any) -> str:
            return 'on' if bool(value) else 'off'

        allowlist = config.get('sandbox_readonly_allowlist') or []
        allow_count = len(allowlist) if isinstance(allowlist, list) else 0
        configured_root = str(config.get('sandbox_root') or '').strip()
        sandbox_root = str(
            (Path(configured_root).expanduser() if configured_root else Path.home() / 'Monika')
            .resolve(strict=False)
        )
        agent_on = bool(config.get('agent_tools_enabled', False))
        file_on = bool(config.get('file_tools_enabled', False))
        vision_on = bool(config.get('vision_enabled', False))
        network_bits = []
        if config.get('api_base'):
            network_bits.append('chat API')
        if config.get('agent_api_base') and str(config.get('llm_call_mode') or 'unified') != 'unified':
            network_bits.append('agent API')
        if vision_on:
            network_bits.append('vision API')
        if str(config.get('avatar_backend') or 'png') in {'vtube_studio', 'auto'}:
            network_bits.append('local VTube Studio WebSocket')
        if config.get('tts_enabled') and str(config.get('tts_provider') or '').lower() in {'bailian_cosyvoice', 'aliyun_bailian', 'cosyvoice'}:
            network_bits.append('Bailian TTS')
        if str(config.get('stt_provider') or '').lower() in {'bailian_paraformer', 'paraformer', 'dashscope_paraformer'}:
            network_bits.append('Bailian STT')
        network_text = ', '.join(network_bits) if network_bits else 'none except local runtime'
        summary = (
            f"Agent tools {onoff(agent_on)} · file tools {onoff(file_on)} · "
            f"vision {onoff(vision_on)} · readable folders {allow_count}"
        )
        details = [
            'Capabilities / Privacy',
            f"- Agent tools: {onoff(agent_on)}",
            f"- File tools: {onoff(file_on)}",
            f"- Writable sandbox: {sandbox_root}",
            f"- Readable user folders: {allow_count} explicitly allow-listed",
            f"- Screen vision: {onoff(vision_on)} (active-window image may leave this machine when enabled)",
            f"- Network use: {network_text}",
            f"- Embedding service: {onoff(config.get('embedding_service_enabled', False))}",
            f"- Idle self-actions: {onoff(config.get('idle_self_actions_enabled', False))}",
            '',
            'Safety model:',
            '- No sudo or shell tools are exposed here.',
            '- Tools are allow-listed functions, not arbitrary commands.',
            '- User files require explicit readable-folder allow-listing.',
            '- Writes stay inside the configured Monika sandbox.',
            '- Vision is opt-in and should be treated as cloud upload of the active window.',
        ]
        self.capability_summary.setText(summary)
        self.capability_details.setPlainText('\n'.join(details))

    def _pick_sandbox_folder(self) -> None:
        start = self.sandbox_root.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, 'Choose the sandbox folder', start)
        if chosen:
            self.sandbox_root.setText(chosen)

    def _open_sandbox_folder(self) -> None:
        configured = self.sandbox_root.text().strip()
        target = (Path(configured).expanduser() if configured else Path.home() / 'Monika').resolve(strict=False)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, 'Sandbox folder', f'Could not open the sandbox folder:\n{exc}')
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            QMessageBox.warning(self, 'Sandbox folder', f'Could not open:\n{target}')

    def _pick_readable_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, 'Add a folder Monika may read', str(Path.home()))
        if not chosen:
            return
        current = self.sandbox_allowlist.toPlainText().strip()
        existing = {line.strip() for line in current.splitlines() if line.strip()}
        if chosen in existing:
            return
        self.sandbox_allowlist.setPlainText(f'{current}\n{chosen}'.strip() if current else chosen)

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
            'context_translation_enabled': self.context_translation_enabled.isChecked(),
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
            'avatar_backend': self.avatar_backend.currentText(),
            'vts_url': self.vts_url.text().strip(),
            'vts_plugin_name': self.vts_plugin_name.text().strip(),
            'vts_plugin_developer': self.vts_plugin_developer.text().strip(),
            'vts_parameter_prefix': self.vts_parameter_prefix.text().strip(),
            'gui_idle_spire_enabled': self.gui_idle_spire_enabled.isChecked(),
            'gui_idle_spire_minutes': self.gui_idle_spire_minutes.value(),
            'idle_self_actions_enabled': self.idle_self_actions_enabled.isChecked(),
            'gui_startup_greeting_enabled': self.gui_startup_greeting_enabled.isChecked(),
            'auto_memory_summary_enabled': self.auto_memory_summary_enabled.isChecked(),
            'auto_memory_summary_turns': self.auto_memory_summary_turns.value(),
            'tts_enabled': self.tts_enabled.isChecked(),
            'tts_provider': self.tts_provider.currentText(),
            'tts_bailian_model': self.tts_model.text().strip(),
            'tts_bailian_voice': self.tts_voice.text().strip(),
            'tts_bailian_format': self.tts_format.currentText(),
            'tts_playback_backend': self.tts_playback_backend.currentText(),
            'tts_bailian_instruction': self.tts_instruction.text().strip(),
            'speech_streaming_enabled': self.speech_streaming_enabled.isChecked(),
            'speech_queue_behavior': self.speech_queue_behavior.currentText(),
            'speech_max_concurrency': self.speech_max_concurrency.value(),
            'lip_sync_sensitivity': self.lip_sync_sensitivity.value(),
            'stt_provider': self.stt_provider.currentText(),
            'stt_language': self.stt_language.currentText(),
            'stt_timeout': self.stt_timeout.value(),
        }
        updates['llm_call_mode'] = self.llm_call_mode.currentText()
        updates['agent_api_base'] = self.agent_api_base.text().strip()
        updates['agent_model'] = self.agent_model.text().strip()
        updates['agent_tools_enabled'] = self.agent_tools_enabled.isChecked()
        updates['file_tools_enabled'] = self.file_tools_enabled.isChecked()
        updates['vision_enabled'] = self.vision_enabled.isChecked()
        updates['sandbox_root'] = self.sandbox_root.text().strip()
        updates['sandbox_readonly_allowlist'] = [
            line.strip() for line in self.sandbox_allowlist.toPlainText().splitlines() if line.strip()
        ]
        # Only overwrite saved API keys when the user actually typed a new one;
        # a blank field keeps the existing key.
        new_key = self.api_key.text().strip()
        if new_key:
            updates['api_key'] = new_key
        new_agent_key = self.agent_api_key.text().strip()
        if new_agent_key:
            updates['agent_api_key'] = new_agent_key
        self.owner.config_save_requested.emit(updates)


EVAL_CATEGORIES = ('greeting', 'return', 'farewell', 'love', 'hug', 'comfort', 'daily', 'memory', 'playful')


class EvalDialog(QDialog):
    """Run the Monika character-fidelity evaluation and show the scorecard.

    The evaluation runs the real reply path on isolated temporary databases and
    scores each reply with an LLM judge. It never touches the live chat
    database, and uses the user's configured API key (so a real run costs
    tokens). The heavy work runs in a daemon thread; progress and results are
    delivered back to the UI thread through signals.
    """

    progress = Signal(str)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, owner: 'MainWindow') -> None:
        super().__init__(owner)
        self.owner = owner
        self._running = False
        self.setWindowTitle('Character Evaluation')
        self.resize(760, 600)
        self._build_ui()
        self.progress.connect(self._append)
        self.finished.connect(self._on_finished)
        self.failed.connect(self._on_failed)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel(
            'Measures how close replies feel to the real Monika. Runs a fixed scenario '
            'set on isolated temporary databases and scores each reply with an LLM judge '
            'anchored to real Monika reference lines. A real run uses your API key and '
            'costs tokens; the live chat database is never touched.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        controls = QHBoxLayout()
        controls.addWidget(QLabel('Subset:'))
        self.subset_combo = QComboBox()
        self.subset_combo.addItem('all', '')
        for category in EVAL_CATEGORIES:
            self.subset_combo.addItem(category, category)
        controls.addWidget(self.subset_combo)
        self.offline_check = QCheckBox('Offline self-test (no API, fake scores)')
        controls.addWidget(self.offline_check)
        controls.addStretch(1)
        self.run_button = QPushButton('Run evaluation')
        self.run_button.setProperty('btnRole', 'primary')
        self.run_button.clicked.connect(self.start_eval)
        controls.addWidget(self.run_button)
        layout.addLayout(controls)

        self.output = QPlainTextEdit()
        self.output.setObjectName('debugPanel')
        self.output.setReadOnly(True)
        mono = QFont('monospace')
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.output.setFont(mono)
        layout.addWidget(self.output)

        close = QPushButton('Close')
        close.setProperty('btnRole', 'ghost')
        close.clicked.connect(self.hide)
        layout.addWidget(close)

    def start_eval(self) -> None:
        if self._running:
            return
        self._running = True
        self.run_button.setEnabled(False)
        offline = self.offline_check.isChecked()
        subset = self.subset_combo.currentData() or ''
        self.output.setPlainText('Running evaluation... this can take a minute.\n')

        def run() -> None:
            try:
                eval_dir = str(ROOT_DIR / 'maica cli' / 'eval')
                if eval_dir not in sys.path:
                    sys.path.insert(0, eval_dir)
                import run_eval as runner

                outcome = runner.run_evaluation(
                    offline=offline,
                    subset=subset,
                    save=not offline,
                    progress=lambda message: self.progress.emit(message),
                )
                self.finished.emit(outcome)
            except Exception as exc:
                self.failed.emit(redact_secret(str(exc)))

        threading.Thread(target=run, daemon=True).start()

    def _append(self, message: str) -> None:
        self.output.appendPlainText(message)

    def _on_finished(self, outcome: dict[str, Any]) -> None:
        self._running = False
        self.run_button.setEnabled(True)
        self.output.appendPlainText('\n' + str(outcome.get('scorecard', '')))
        saved = outcome.get('saved_path')
        if saved:
            self.output.appendPlainText(f'\nsaved: {saved}')

    def _on_failed(self, message: str) -> None:
        self._running = False
        self.run_button.setEnabled(True)
        self.output.appendPlainText(f'\nevaluation failed: {message}')


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
        # Soft vignette: faint warm glow at center, darkened toward the edges,
        # so panels and the avatar read with more depth than a flat overlay.
        rect = self.rect()
        radius = max(rect.width(), rect.height()) * 0.75
        vignette = QRadialGradient(rect.center(), radius)
        vignette.setColorAt(0.0, QColor(*VIGNETTE_CENTER))
        vignette.setColorAt(0.55, QColor(34, 22, 32, 90))
        vignette.setColorAt(1.0, QColor(*VIGNETTE_EDGE))
        painter.fillRect(rect, vignette)
        painter.end()
        super().paintEvent(event)


class MessageBubble(QFrame):
    """A single rounded, shadowed chat bubble (real widget, not rich text)."""

    def __init__(self, role: str, text: str, name: str = '', meta: str = '') -> None:
        super().__init__()
        self.role = role
        self.setObjectName({
            'user': 'bubbleUser',
            'monika': 'bubbleMonika',
            'system': 'bubbleSystem',
            'notice': 'bubbleNotice',
        }.get(role, 'bubbleSystem'))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(3)

        if name:
            self.name_label = QLabel(name)
            self.name_label.setObjectName('bubbleName')
            layout.addWidget(self.name_label)

        self.body_label = QLabel(text)
        self.body_label.setObjectName('bubbleBody')
        self.body_label.setWordWrap(True)
        self.body_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.body_label)

        self.meta_label: QLabel | None = None
        if meta:
            self.meta_label = QLabel(meta)
            self.meta_label.setObjectName('bubbleMeta')
            layout.addWidget(self.meta_label)

        if role in {'user', 'monika'}:
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(22)
            shadow.setOffset(0, 4)
            shadow.setColor(QColor(20, 10, 18, 70))
            self.setGraphicsEffect(shadow)

    def set_body(self, text: str) -> None:
        self.body_label.setText(text)

    def set_meta(self, meta: str) -> None:
        if self.meta_label is None:
            self.meta_label = QLabel(meta)
            self.meta_label.setObjectName('bubbleMeta')
            self.layout().addWidget(self.meta_label)
        else:
            self.meta_label.setText(meta)


class ChatLog(QScrollArea):
    """Scrollable column of MessageBubble widgets with messenger-style alignment."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName('chatLog')
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._container = QWidget()
        self._container.setObjectName('chatContainer')
        self._column = QVBoxLayout(self._container)
        self._column.setContentsMargins(14, 14, 14, 14)
        self._column.setSpacing(4)
        self._column.addStretch(1)
        self.setWidget(self._container)
        self._bubbles: list[MessageBubble] = []
        # Messenger-style "stick to bottom": when content grows (new bubble or a
        # bulk history load), jump to the newest message unless the user has
        # scrolled up. This also fixes history loading starting at the top.
        self._stick_bottom = True
        bar = self.verticalScrollBar()
        bar.rangeChanged.connect(self._on_range_changed)
        bar.valueChanged.connect(self._on_value_changed)

    def _on_value_changed(self, value: int) -> None:
        bar = self.verticalScrollBar()
        self._stick_bottom = value >= bar.maximum() - 4

    def _on_range_changed(self, _min: int, _max: int) -> None:
        if self._stick_bottom:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def _add(self, bubble: MessageBubble, align: str) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if align == 'right':
            row.addStretch(1)
            row.addWidget(bubble)
        elif align == 'center':
            row.addStretch(1)
            row.addWidget(bubble)
            row.addStretch(1)
        else:
            row.addWidget(bubble)
            row.addStretch(1)
        holder = QWidget()
        holder.setLayout(row)
        # Insert before the trailing stretch so bubbles stack top-to-bottom.
        self._column.insertWidget(self._column.count() - 1, holder)
        self._bubbles.append(bubble)
        self._apply_width(bubble)
        # Scrolling to the newest bubble is handled by the stick-to-bottom
        # range handler once the layout settles.

    def _apply_width(self, bubble: MessageBubble) -> None:
        viewport_width = max(280, self.viewport().width())
        cap = 0.86 if bubble.role in {'system', 'notice'} else 0.66
        bubble.setMaximumWidth(int(viewport_width * cap))

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        for bubble in self._bubbles:
            self._apply_width(bubble)

    def _scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def add_system(self, text: str) -> None:
        self._add(MessageBubble('system', text), 'center')

    def add_notice(self, text: str) -> None:
        self._add(MessageBubble('notice', text), 'center')

    def add_user(self, text: str) -> None:
        self._add(MessageBubble('user', text, name='You'), 'right')

    def add_monika(self, text: str, meta: str = '') -> None:
        self._add(MessageBubble('monika', text, name='Monika', meta=meta), 'left')

    def start_stream(self) -> MessageBubble:
        bubble = MessageBubble('monika', '', name='Monika')
        self._add(bubble, 'left')
        return bubble

    def clear(self) -> None:
        for bubble in self._bubbles:
            holder = bubble.parentWidget()
            if holder is not None:
                holder.setParent(None)
                holder.deleteLater()
        self._bubbles.clear()


class MainWindow(QMainWindow):
    chat_requested = Signal(str)
    spire_requested = Signal(str)
    cancel_requested = Signal()
    shutdown_requested = Signal()
    data_snapshot_requested = Signal()
    profile_set_requested = Signal(str, str)
    memory_add_requested = Signal(str, str, int)
    memory_delete_requested = Signal(int)
    fact_add_requested = Signal(str, str, int)
    fact_delete_requested = Signal(int)
    debug_export_requested = Signal(str)
    user_data_export_requested = Signal(str)
    user_data_preview_requested = Signal(str)
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
        self.speech = SpeechController({}, self)
        self.speech.event.connect(self._handle_speech_event)
        self.stt = create_stt({})
        self.tts_enabled = False
        self.last_tts_error = ''
        self.stt_busy = False
        self.data_dialog: DataManagerDialog | None = None
        self.settings_dialog: SettingsDialog | None = None
        self.eval_dialog: EvalDialog | None = None
        self.pet_window: Any = None
        self.avatar_controller: AvatarController | None = None
        self.current_config: dict[str, Any] = {}
        self.last_user_activity = dt.datetime.now()
        self.idle_spire_sent = False
        self.idle_timer = QTimer(self)
        self.streaming_active = False
        self.streaming_text = ''
        self._stream_bubble: MessageBubble | None = None
        self.active_turn_id = ''
        self.active_event_sequence = 0
        self.startup_greeting_shown = False
        self.idle_timer.setInterval(30_000)
        self.idle_timer.timeout.connect(self.check_idle_spire)
        self.current_emotion = 'smile'
        self.typing_phase = 0
        self.typing_timer = QTimer(self)
        self.typing_timer.setInterval(420)
        self.typing_timer.timeout.connect(self._tick_typing)
        self.avatar_timer = QTimer(self)
        self.avatar_timer.setInterval(250)
        self.avatar_timer.timeout.connect(self._tick_avatar)

        suffix = ' · SAFE TEST DB' if self.safe_test_mode else ''
        self.setWindowTitle(f'MAICA GUI v{APP_VERSION}{suffix}')
        self.resize(1180, 760)
        self.setMinimumSize(980, 640)
        self._build_ui()
        self.avatar_controller = AvatarController(
            self.assets,
            self.avatar_label,
            self.current_config,
            on_status=self._handle_avatar_status,
            on_token=self._handle_vts_token,
        )
        self.avatar_timer.start()
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
        self.worker.runtime_event.connect(self._handle_runtime_event)
        self.worker.config_ready.connect(self._handle_config_ready)
        self.worker.data_ready.connect(self._handle_data_ready)
        self.worker.pet_action.connect(self._apply_pet_action)
        self.chat_requested.connect(self.worker.chat)
        self.spire_requested.connect(self.worker.spire)
        self.cancel_requested.connect(self.worker.cancel_active)
        self.shutdown_requested.connect(self.worker.shutdown)
        self.data_snapshot_requested.connect(self.worker.data_snapshot)
        self.profile_set_requested.connect(self.worker.set_profile_value)
        self.memory_add_requested.connect(self.worker.add_memory)
        self.memory_delete_requested.connect(self.worker.delete_memory)
        self.fact_add_requested.connect(self.worker.add_fact)
        self.fact_delete_requested.connect(self.worker.delete_fact)
        self.debug_export_requested.connect(self.worker.export_debug)
        self.user_data_export_requested.connect(self.worker.export_user_data)
        self.user_data_preview_requested.connect(self.worker.preview_import)
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
        avatar_shadow = QGraphicsDropShadowEffect(self)
        avatar_shadow.setBlurRadius(48)
        avatar_shadow.setOffset(0, 10)
        avatar_shadow.setColor(QColor(*SHADOW_COLOR))
        self.avatar_label.setGraphicsEffect(avatar_shadow)
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

        self.chat_log = ChatLog()
        right_layout.addWidget(self.chat_log, 1)

        self.input_box = QTextEdit()
        self.input_box.setObjectName('inputBox')
        # Note: do NOT set WA_InputMethodEnabled / setInputMethodHints here. Qt
        # enables IME on QTextEdit by default; setting these explicitly broke
        # CJK input method activation (e.g. fcitx5) — keep Qt's defaults.
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
        self.eval_button = QPushButton('Eval')
        self.pet_button = QPushButton('Pet')
        self.debug_button = QPushButton('Debug')
        self.clear_button = QPushButton('Clear')
        # Tier the buttons: accent primaries, tinted secondaries, quiet ghosts.
        for button in (self.send_button, self.spire_button):
            button.setProperty('btnRole', 'primary')
        for button in (self.tts_button, self.stop_tts_button, self.listen_button):
            button.setProperty('btnRole', 'secondary')
        for button in (self.data_button, self.settings_button, self.diagnostics_button,
                       self.eval_button, self.pet_button, self.debug_button, self.clear_button):
            button.setProperty('btnRole', 'ghost')
        self.send_button.clicked.connect(self.send_or_cancel)
        self.spire_button.clicked.connect(self.send_spire)
        self.tts_button.clicked.connect(self.toggle_tts)
        self.stop_tts_button.clicked.connect(self.stop_tts)
        self.listen_button.clicked.connect(self.listen_once)
        self.data_button.clicked.connect(self.open_data_manager)
        self.settings_button.clicked.connect(self.open_settings)
        self.diagnostics_button.clicked.connect(self.export_diagnostics)
        self.eval_button.clicked.connect(self.open_eval)
        self.pet_button.clicked.connect(self.toggle_pet)
        self.debug_button.clicked.connect(self.toggle_debug_panel)
        self.clear_button.clicked.connect(self.chat_log.clear)
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
        button_row2.addWidget(self.eval_button)
        button_row2.addWidget(self.pet_button)
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
        self.setStyleSheet(_build_style_sheet(THEME))

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
        self.speech.close()
        self.idle_timer.stop()
        self.typing_timer.stop()
        self.avatar_timer.stop()
        if self.avatar_controller is not None:
            self.avatar_controller.stop()
        if self.pet_window is not None:
            self.pet_window.close()
        self.shutdown_requested.emit()
        self.thread.quit()
        if not self.thread.wait(5000):
            self.add_system_message('Backend shutdown timed out; forcing GUI thread cleanup.')
            self.thread.terminate()
            self.thread.wait(2000)
        super().closeEvent(event)

    def _meta_text(self, emotion: str, response_time: Any) -> str:
        meta = f'♥ {emotion or "neutral"}'
        if response_time != '':
            meta += f' · {response_time}s'
        return meta

    def add_system_message(self, text: str) -> None:
        self.chat_log.add_system(text)

    def add_user_message(self, text: str) -> None:
        self.chat_log.add_user(text)

    def add_monika_message(self, text: str, emotion: str, response_time: Any = '') -> None:
        self.chat_log.add_monika(text, self._meta_text(emotion, response_time))

    def _handle_stream_started(self, payload: dict[str, Any]) -> None:
        self.streaming_active = True
        self.streaming_text = ''
        self._stream_bubble = self.chat_log.start_stream()

    def _handle_stream_chunk(self, chunk: str) -> None:
        if not self.streaming_active:
            self._handle_stream_started({'source': 'chat'})
        text = str(chunk or '')
        if not text:
            return
        self.streaming_text += text
        if self._stream_bubble is not None:
            self._stream_bubble.set_body(self.streaming_text)
            self.chat_log._scroll_to_bottom()

    def _handle_runtime_event(self, event: dict[str, Any]) -> None:
        turn_id = str(event.get('turn_id') or '')
        kind = str(event.get('kind') or '')
        sequence = int(event.get('sequence') or 0)
        payload = event.get('payload') if isinstance(event.get('payload'), dict) else {}
        if kind == 'turn.started':
            self.active_turn_id = turn_id
            self.active_event_sequence = sequence
            if self.tts_enabled:
                self.speech.begin(turn_id)
            return
        if not turn_id or turn_id != self.active_turn_id or sequence <= self.active_event_sequence:
            return
        self.active_event_sequence = sequence
        if kind == 'text.delta':
            delta = str(payload.get('text') or '')
            self._handle_stream_chunk(delta)
            if self.tts_enabled:
                self.speech.append_text(turn_id, delta)
        elif kind == 'dialogue.final' and self.tts_enabled:
            self.speech.finish(turn_id, str(payload.get('text') or ''))
        elif kind == 'emotion.changed':
            self.set_emotion(str(payload.get('emotion') or 'neutral'))
        elif kind == 'action.requested' and self.avatar_controller is not None:
            self.avatar_controller.play_action(payload.get('action'))
        elif kind == 'tool.started':
            self.status_label.setText(f"Monika is using {payload.get('tool', 'a tool')}...")
        elif kind == 'turn.cancelled':
            self.speech.cancel('turn cancelled')
            self.send_button.setEnabled(False)
            self.send_button.setText('Stopping...')

    def _handle_speech_event(self, event: dict[str, Any]) -> None:
        kind = str(event.get('kind') or '')
        payload = event.get('payload') if isinstance(event.get('payload'), dict) else {}
        if kind == 'audio.started':
            if self.avatar_controller is not None:
                self.avatar_controller.set_speaking(True)
        elif kind == 'audio.amplitude':
            if self.avatar_controller is not None:
                self.avatar_controller.set_mouth_open(float(payload.get('value') or 0.0))
        elif kind == 'audio.finished':
            if self.avatar_controller is not None:
                self.avatar_controller.set_mouth_open(0.0)
        elif kind in {'speech.finished', 'speech.cancelled'}:
            self._stop_avatar_speaking()
        elif kind == 'speech.failed':
            self._stop_avatar_speaking()
            error = redact_secret(str(payload.get('error') or 'TTS failed'))
            if error and error != self.last_tts_error:
                self.last_tts_error = error
                self.add_system_message(f'TTS error: {error}')

    def _finish_streaming_message(self, emotion: str, response_time: Any = '', final_text: str | None = None) -> None:
        if self._stream_bubble is not None:
            # The streamed text is the raw model output; the engine may have
            # rewritten it (e.g. language enforcement) or cleaned markers, so
            # the authoritative final_text wins when provided.
            body = final_text if final_text is not None else self.streaming_text
            if body:
                self._stream_bubble.set_body(body)
            self._stream_bubble.set_meta(self._meta_text(emotion, response_time))
        self.streaming_active = False
        self.streaming_text = ''
        self._stream_bubble = None

    def set_busy(self, busy: bool) -> None:
        self.send_button.setEnabled(True)
        self.send_button.setText('Stop' if busy else 'Send')
        self.spire_button.setEnabled(not busy)
        self.input_box.setEnabled(not busy)
        if busy:
            self.set_emotion('thinking')
            self.typing_phase = 0
            self._tick_typing()
            self.typing_timer.start()
        else:
            self.typing_timer.stop()

    def _tick_typing(self) -> None:
        self.typing_phase = (self.typing_phase + 1) % 4
        self.status_label.setText('Monika is typing' + '·' * self.typing_phase)

    def background_for_now(self) -> QPixmap:
        mode = str(self.current_config.get('gui_background_mode') or 'auto')
        return self.assets.background_for_mode(mode, dt.datetime.now().hour)

    def refresh_background(self) -> None:
        self.root_widget.set_background(self.background_for_now())

    def set_emotion(self, emotion: str) -> None:
        normalized = normalize_emotion(emotion)
        self.current_emotion = normalized
        self.status_label.setProperty('emotion', normalized)
        self._update_avatar_status_label()
        if self.avatar_controller is not None:
            self.avatar_controller.set_emotion(normalized)
        else:
            avatar = self.assets.compose_avatar(normalized)
            if avatar.isNull():
                return
            scaled = avatar.scaled(
                self.avatar_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.avatar_label.setPixmap(scaled)

    def avatar_status_text(self) -> str:
        if self.avatar_controller is None:
            return 'png'
        return self.avatar_controller.status_text()

    def _update_avatar_status_label(self) -> None:
        suffix = ''
        if self.avatar_controller is not None and self.avatar_controller.backend != 'png':
            suffix = f' · avatar {self.avatar_controller.status_text()}'
        self.status_label.setText(f'♥ {self.current_emotion}{suffix}')

    def _handle_avatar_status(self, _status: str) -> None:
        if not self.typing_timer.isActive():
            self._update_avatar_status_label()
        if self.settings_dialog is not None:
            self.settings_dialog.avatar_status.setText(f'avatar: {self.avatar_status_text()}')

    def _handle_vts_token(self, token: str) -> None:
        token = str(token or '').strip()
        if not token or token == self.current_config.get('vts_auth_token'):
            return
        self.current_config['vts_auth_token'] = token
        self.config_save_requested.emit({'vts_auth_token': token})

    def reconnect_avatar_backend(self, overrides: dict[str, Any] | None = None) -> None:
        if overrides:
            merge_runtime_config(self.current_config, overrides)
        if self.avatar_controller is None:
            return
        self.avatar_controller.configure(self.current_config)
        self.avatar_controller.set_emotion(self.current_emotion)
        self._update_avatar_status_label()

    def _tick_avatar(self) -> None:
        if self.avatar_controller is not None:
            self.avatar_controller.tick()

    def _stop_avatar_speaking(self) -> None:
        if self.avatar_controller is not None:
            self.avatar_controller.set_speaking(False)
            self.avatar_controller.set_mouth_open(0.0)

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

    def send_or_cancel(self) -> None:
        if not self.input_box.isEnabled():
            self.cancel_active_turn()
            return
        self.send_chat()

    def cancel_active_turn(self) -> None:
        if not self.active_turn_id and self.input_box.isEnabled():
            return
        self.send_button.setEnabled(False)
        self.send_button.setText('Stopping...')
        self.stop_tts()
        self.cancel_requested.emit()

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
        self.reconnect_avatar_backend()
        self.refresh_background()
        self.speech.configure(config)
        self.stt = create_stt(config)
        self.tts_enabled = bool(config.get('tts_enabled', False))
        self.tts_button.setText('TTS: on' if self.tts_enabled else 'TTS: off')
        provider = str(config.get('tts_provider') or 'auto')
        self.add_system_message(f'TTS provider: {provider} · {"on" if self.tts_enabled else "off"}')
        if self.settings_dialog is not None:
            self.settings_dialog.render(self.current_config)
        if config.get('gui_startup_greeting_enabled', True):
            self.add_system_message('Monika is awake. Recent history will be loaded if available.')

    def toggle_tts(self) -> None:
        self.tts_enabled = not self.tts_enabled
        self.tts_button.setText('TTS: on' if self.tts_enabled else 'TTS: off')
        if not self.tts_enabled:
            self.speech.cancel('TTS disabled')
            self._stop_avatar_speaking()
        self.add_system_message('TTS enabled.' if self.tts_enabled else 'TTS disabled.')

    def stop_tts(self) -> None:
        self.speech.cancel('stopped by user')
        self._stop_avatar_speaking()

    def listen_once(self) -> None:
        if self.stt_busy:
            return
        self.stt_busy = True
        self.listen_button.setEnabled(False)
        self.add_system_message('STT listening...')

        def run() -> None:
            try:
                result = self.stt.listen()
            except Exception as exc:
                result = {'ok': False, 'text': '', 'error': redact_secret(str(exc))}
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

    def open_eval(self) -> None:
        if self.eval_dialog is None:
            self.eval_dialog = EvalDialog(self)
        self.eval_dialog.show()
        self.eval_dialog.raise_()
        self.eval_dialog.activateWindow()

    def toggle_pet(self) -> None:
        if PetWindow is None:
            self.add_system_message('Desktop pet is unavailable (monika_pet not importable).')
            return
        if self.pet_window is None:
            # Hosted mode: the pet shares this window's single engine; it does
            # not start its own. Same Monika, two surfaces.
            self.pet_window = PetWindow(host_engine=False)
            self.pet_window.interaction_requested.connect(self._on_pet_interaction)
            self.pet_window.show_normal()
            return
        if self.pet_window.isVisible():
            self.pet_window.hide()
        else:
            self.pet_window.show_normal()

    def _apply_pet_action(self, action: str, arg: str) -> None:
        # A body tool fired during an agent turn; reflect it on the hosted pet.
        pet = self.pet_window
        if pet is None or not pet.isVisible():
            return
        if action == 'expression':
            pet.apply_expression(arg)
        elif action == 'gesture':
            pet.do_gesture(arg)
        elif action == 'pop':
            pet.show_normal()
        elif action == 'nudge':
            pet.do_nudge()

    def _on_pet_interaction(self, _kind: str) -> None:
        # Clicking the pet asks Monika for a proactive line (without touching the
        # chat input); the reply is mirrored to the pet face in _handle_result.
        if not self.input_box.isEnabled():
            return
        self.last_user_activity = dt.datetime.now()
        self.set_busy(True)
        self.spire_requested.emit('')

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
            f"examples: {len(summaries)} | retrieval: {example_bank.get('retrieval_mode', '')} | weight: {example_bank.get('weight', '')}",
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
            self.add_system_message(f'Diagnostics export failed: {redact_secret(str(exc))}')
            QMessageBox.warning(self, 'Diagnostics export failed', redact_secret(str(exc)))

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
            merge_runtime_config(self.current_config, config)
        if payload.get('action') == 'save_config':
            self.speech.configure(self.current_config)
            self.stt = create_stt(self.current_config)
            self.reconnect_avatar_backend()
            self.tts_enabled = bool(self.current_config.get('tts_enabled', False))
            self.tts_button.setText('TTS: on' if self.tts_enabled else 'TTS: off')
            self.refresh_background()
            self.add_system_message('Settings applied. New chat requests will use the updated options.')
            if self.settings_dialog is not None:
                self.settings_dialog.render(self.current_config)
        if self.data_dialog is not None:
            self.data_dialog.render(payload)
        if payload.get('action') == 'preview_import':
            self.confirm_import_preview(payload)
        self.update_context_label(payload)
        if payload.get('action') in {'snapshot', ''}:
            self.load_recent_messages_once(payload)
            self.maybe_show_startup_greeting(payload)

    def confirm_import_preview(self, payload: dict[str, Any]) -> None:
        preview = payload.get('import_preview') if isinstance(payload.get('import_preview'), dict) else {}
        path = str(payload.get('import_path') or '').strip()
        if not preview or not path:
            return
        tables = preview.get('tables') if isinstance(preview.get('tables'), dict) else {}
        table_lines = [f'{name}: {count}' for name, count in sorted(tables.items())]
        text = (
            'Import this user data package? A database backup will be created first.\n\n'
            f'Format: {preview.get("format", "unknown")}\n'
            f'Exported: {preview.get("exported_at", "unknown")}\n'
            + '\n'.join(table_lines)
        )
        answer = QMessageBox.question(self, 'Import preview', text)
        if answer == QMessageBox.StandardButton.Yes:
            self.user_data_import_requested.emit(path, False)

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
        result_turn_id = str(result.get('turn_id') or '')
        if result_turn_id and self.active_turn_id and result_turn_id != self.active_turn_id:
            return
        self.set_busy(False)
        self.active_turn_id = ''
        self.active_event_sequence = 0
        self.last_user_activity = dt.datetime.now()
        if result.get('cancelled'):
            if self.streaming_active:
                self._finish_streaming_message('neutral', final_text='')
            self.add_system_message('Request cancelled.')
            self.set_emotion('neutral')
            return
        if not result.get('ok'):
            self.set_emotion('concerned')
            if self.streaming_active:
                self._finish_streaming_message('concerned')
            self.add_system_message(f'Request failed: {result.get("error", "unknown error")}')
            return

        visual_state = self.visual_state_from_result(result)
        reply_text = str(result.get('text') or '')
        emotion = str(visual_state.get('raw_emotion') or 'neutral')
        self.set_emotion(str(visual_state.get('emotion') or emotion))
        if self.avatar_controller is not None:
            self.avatar_controller.play_action(visual_state.get('action'))
        if result.get('streamed') and self.streaming_active:
            # Replace the live-streamed raw text with the engine's final reply
            # (language-enforced / cleaned), so e.g. a first-turn Chinese stream
            # is shown as the rewritten English result.
            self._finish_streaming_message(emotion, result.get('response_time', ''), final_text=reply_text)
        else:
            if self.streaming_active:
                self._finish_streaming_message(emotion, result.get('response_time', ''), final_text=reply_text)
            self.add_monika_message(reply_text, emotion, result.get('response_time', ''))
        if self.pet_window is not None and self.pet_window.isVisible() and reply_text:
            self.pet_window.show_line(reply_text, emotion)
        self.update_debug_panel(result)
        # Runtime dialogue events own normal speech. Keep a fallback for
        # non-engine result producers that do not have a structured turn id.
        if self.tts_enabled and reply_text and not result_turn_id:
            fallback_turn = f'legacy-{dt.datetime.now().timestamp()}'
            self.speech.begin(fallback_turn)
            self.speech.finish(fallback_turn, reply_text)
        for notice in result.get('mtrigger_notices') or []:
            self.chat_log.add_notice(str(notice))

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

    def maybe_show_startup_greeting(self, payload: dict[str, Any]) -> None:
        if self.startup_greeting_shown:
            return
        if not self.current_config.get('gui_startup_greeting_enabled', True):
            return
        self.startup_greeting_shown = True
        profile = payload.get('profile') if isinstance(payload.get('profile'), dict) else {}
        status = payload.get('status') if isinstance(payload.get('status'), dict) else {}
        events = status.get('today_events') if isinstance(status.get('today_events'), list) else []
        english = str(self.current_config.get('language') or 'en').lower().startswith('en')
        last_seen = str(profile.get('last_seen') or '').strip()
        event_names = [str(item.get('name') or '').strip() for item in events if isinstance(item, dict) and str(item.get('name') or '').strip()]
        if english:
            if event_names:
                text = f"Welcome back. I noticed today is {', '.join(event_names[:2])}, so I wanted to greet you properly."
            elif last_seen:
                text = "Welcome back. I kept your seat warm while you were away."
            else:
                text = "There you are. I'm glad we get to start from here."
        else:
            if event_names:
                text = f"欢迎回来。今天是{', '.join(event_names[:2])}，所以我想好好和你打个招呼。"
            elif last_seen:
                text = "欢迎回来。我有好好等你哦。"
            else:
                text = "你来啦。能从这里开始，我很开心。"
        self.set_emotion('smile')
        self.add_monika_message(text, 'smile')

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

# --- Design tokens: rose-pink x soft-light theme ----------------------------
# Centralized so re-skinning is a single-place edit.
THEME = {
    'bg': '#2a1d28',            # warm pink-night base
    'bg_edge': '#1a1019',       # vignette edge
    'panel': 'rgba(60, 44, 56, 0.58)',       # soft glass panel
    'panel_border': 'rgba(255, 224, 232, 0.20)',
    'accent': '#d98a9a',        # rose gold
    'accent_hi': '#e8a8b4',     # hover
    'accent_lo': '#c2727f',     # pressed
    'on_accent': '#2c1620',     # dark ink on rose-gold
    'cream': 'rgba(255, 247, 245, 0.97)',    # chat surface
    'input_bg': 'rgba(255, 250, 249, 0.98)',
    'ink': '#3c2b32',           # primary text on cream
    'muted': '#9c838b',         # secondary text
    'title': '#fbeef1',
    'soft': '#f1dde3',          # light text on dark panels
    'font_stack': '"Segoe UI", "PingFang SC", "Microsoft YaHei UI", "Noto Sans CJK SC", sans-serif',
    'mono_stack': '"Cascadia Mono", "JetBrains Mono", Consolas, "Courier New", monospace',
}

# Avatar drop-shadow / background vignette colors as QColor tuples.
SHADOW_COLOR = (20, 10, 18, 150)
VIGNETTE_CENTER = (255, 232, 240, 18)   # faint warm glow behind avatar
VIGNETTE_EDGE = (16, 8, 14, 150)        # darkened edges


def _build_style_sheet(t: dict[str, str]) -> str:
    return f"""
QMainWindow {{
    background: {t['bg']};
}}
QWidget {{
    font-family: {t['font_stack']};
}}
QFrame#rightPanel {{
    background: {t['panel']};
    border: 1px solid {t['panel_border']};
    border-radius: 20px;
}}
QFrame#leftPanel {{
    background: rgba(42, 29, 40, 0.28);
    border: 1px solid {t['panel_border']};
    border-radius: 20px;
}}
QLabel#titleLabel {{
    color: {t['title']};
    font-size: 26px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QLabel#statusLabel {{
    color: {t['on_accent']};
    background: {t['accent']};
    padding: 5px 14px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 600;
}}
QLabel#contextLabel {{
    color: {t['soft']};
    background: rgba(0, 0, 0, 0.30);
    padding: 8px 14px;
    border-radius: 12px;
    font-size: 12px;
}}
QScrollArea#chatLog {{
    background: {t['cream']};
    border: none;
    border-radius: 16px;
}}
QWidget#chatContainer {{
    background: transparent;
}}
QFrame#bubbleMonika {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #fff4f6, stop:1 #ffe7ee);
    border: 1px solid rgba(217, 138, 154, 0.30);
    border-left: 3px solid {t['accent']};
    border-radius: 16px;
}}
QFrame#bubbleUser {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {t['accent_hi']}, stop:1 {t['accent_lo']});
    border: none;
    border-radius: 16px;
}}
QFrame#bubbleSystem {{
    background: rgba(60, 43, 50, 0.06);
    border-radius: 12px;
}}
QFrame#bubbleNotice {{
    background: #fbe4e9;
    border: 1px solid rgba(217, 138, 154, 0.35);
    border-radius: 12px;
}}
QLabel#bubbleName {{
    font-size: 11px;
    font-weight: 700;
    color: {t['accent_lo']};
}}
QFrame#bubbleUser QLabel#bubbleName {{
    color: rgba(255, 247, 245, 0.85);
}}
QLabel#bubbleBody {{
    font-size: 15px;
    color: {t['ink']};
}}
QFrame#bubbleUser QLabel#bubbleBody {{
    color: #fff7f5;
}}
QLabel#bubbleMeta {{
    font-size: 11px;
    color: {t['muted']};
}}
QFrame#bubbleSystem QLabel#bubbleBody {{
    font-size: 12px;
    color: {t['muted']};
}}
QFrame#bubbleNotice QLabel#bubbleBody {{
    font-size: 12px;
    color: #8a5d6a;
}}
QTextEdit#inputBox {{
    background: {t['input_bg']};
    color: {t['ink']};
    border: 1px solid rgba(217, 138, 154, 0.45);
    border-radius: 14px;
    padding: 10px;
    font-size: 15px;
    selection-background-color: {t['accent']};
}}
QTextEdit#inputBox:focus {{
    border: 2px solid {t['accent']};
}}
QPlainTextEdit#debugPanel {{
    background: rgba(26, 16, 25, 0.88);
    color: {t['soft']};
    border: 1px solid {t['panel_border']};
    border-radius: 12px;
    padding: 8px;
    font-family: {t['mono_stack']};
    font-size: 12px;
}}
QPushButton {{
    background: rgba(255, 255, 255, 0.10);
    color: {t['soft']};
    border: none;
    border-radius: 12px;
    padding: 9px 14px;
    font-size: 14px;
}}
QPushButton:hover {{
    background: rgba(255, 255, 255, 0.18);
}}
QPushButton:disabled {{
    background: rgba(255, 255, 255, 0.06);
    color: {t['muted']};
}}
QPushButton[btnRole="primary"] {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {t['accent_hi']}, stop:1 {t['accent_lo']});
    color: {t['on_accent']};
    font-weight: 600;
}}
QPushButton[btnRole="primary"]:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #f0b6c1, stop:1 {t['accent']});
}}
QPushButton[btnRole="primary"]:pressed {{
    background: {t['accent_lo']};
}}
QPushButton[btnRole="primary"]:disabled {{
    background: rgba(217, 138, 154, 0.35);
    color: rgba(44, 22, 32, 0.55);
}}
QPushButton[btnRole="secondary"] {{
    background: rgba(217, 138, 154, 0.16);
    color: {t['soft']};
    border: 1px solid rgba(217, 138, 154, 0.40);
}}
QPushButton[btnRole="secondary"]:hover {{
    background: rgba(217, 138, 154, 0.30);
}}
QPushButton[btnRole="secondary"]:checked {{
    background: {t['accent']};
    color: {t['on_accent']};
    font-weight: 600;
}}
QPushButton[btnRole="ghost"] {{
    background: transparent;
    color: {t['muted']};
    padding: 7px 10px;
    font-size: 13px;
}}
QPushButton[btnRole="ghost"]:hover {{
    background: rgba(255, 255, 255, 0.10);
    color: {t['soft']};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px 2px 4px 0;
}}
QScrollBar::handle:vertical {{
    background: rgba(217, 138, 154, 0.55);
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: rgba(217, 138, 154, 0.80);
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
"""


def _prepare_linux_input_method() -> None:
    """Make CJK input work on Linux/Wayland with the bundled (pip) Qt.

    pip's PySide6 ships its own Qt whose plugins do NOT include the fcitx
    input-context plugin. With QT_IM_MODULE=fcitx the app then fails to load any
    input method, so Chinese cannot be typed and Ctrl+Space does nothing. On a
    Wayland session the compositor's text-input protocol drives fcitx5 without
    that plugin, so prefer the Wayland platform and drop the fcitx im-module.
    """
    if not sys.platform.startswith('linux'):
        return
    if os.environ.get('WAYLAND_DISPLAY'):
        os.environ.setdefault('QT_QPA_PLATFORM', 'wayland')
        if os.environ.get('QT_IM_MODULE', '').lower() in ('fcitx', 'fcitx5'):
            os.environ.pop('QT_IM_MODULE', None)


def main() -> int:
    _prepare_linux_input_method()
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
