# -*- coding: utf-8 -*-
"""Advanced editor for per-model Live2D expression and motion mappings."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from avatar_mapping import AvatarMappingError, load_avatar_mapping, save_avatar_mapping, validate_avatar_mapping


class AvatarMappingDialog(QDialog):
    saved = Signal(str)

    def __init__(self, configured_path: str, model_entry: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.configured_path = str(configured_path or '')
        self.model_entry = str(model_entry or '')
        self.setWindowTitle('Live2D Expression / Motion Mapping')
        self.resize(720, 620)
        layout = QVBoxLayout(self)
        note = QLabel(
            'Only standard MAICA emotions and whitelisted actions are accepted. '
            'Expression and motion names below must match the selected model.'
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.sources = QLabel()
        self.sources.setWordWrap(True)
        layout.addWidget(self.sources)
        self.editor = QPlainTextEdit()
        layout.addWidget(self.editor, 1)
        buttons = QHBoxLayout()
        validate_button = QPushButton('Validate')
        save_button = QPushButton('Save As')
        close_button = QPushButton('Close')
        validate_button.clicked.connect(self.validate_text)
        save_button.clicked.connect(self.save_as)
        close_button.clicked.connect(self.close)
        buttons.addStretch(1)
        buttons.addWidget(validate_button)
        buttons.addWidget(save_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        self._load()

    def _load(self) -> None:
        try:
            mapping, sources = load_avatar_mapping(self.configured_path, self.model_entry)
        except AvatarMappingError as exc:
            QMessageBox.warning(self, 'Mapping load failed', str(exc))
            mapping, sources = load_avatar_mapping()
        self.sources.setText('Loaded: ' + ', '.join(Path(path).name for path in sources))
        self.editor.setPlainText(json.dumps(mapping, ensure_ascii=False, indent=2))

    def validate_text(self) -> dict:
        try:
            payload = json.loads(self.editor.toPlainText())
            validated = validate_avatar_mapping(payload)
        except (json.JSONDecodeError, AvatarMappingError) as exc:
            QMessageBox.warning(self, 'Invalid mapping', str(exc))
            return {}
        QMessageBox.information(self, 'Mapping valid', 'The mapping is valid and contains only allowed keys.')
        return validated

    def save_as(self) -> None:
        try:
            payload = validate_avatar_mapping(json.loads(self.editor.toPlainText()))
        except (json.JSONDecodeError, AvatarMappingError) as exc:
            QMessageBox.warning(self, 'Invalid mapping', str(exc))
            return
        if self.configured_path:
            suggested = Path(self.configured_path).expanduser()
        elif self.model_entry:
            suggested = Path(self.model_entry).expanduser().parent / 'maica_avatar_map.json'
        else:
            suggested = Path.home() / 'maica_avatar_map.json'
        target, _filter = QFileDialog.getSaveFileName(
            self,
            'Save Live2D mapping',
            str(suggested),
            'JSON files (*.json)',
        )
        if not target:
            return
        try:
            saved = save_avatar_mapping(target, payload)
        except OSError as exc:
            QMessageBox.warning(self, 'Mapping save failed', str(exc))
            return
        self.configured_path = str(saved)
        self.saved.emit(str(saved))
        self.sources.setText('Loaded: ' + saved.name)
        QMessageBox.information(self, 'Mapping saved', 'The mapping will apply after Settings is saved.')
