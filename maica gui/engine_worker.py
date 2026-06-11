# -*- coding: utf-8 -*-
"""Persistent GUI worker that owns MaicaEngine on a background thread."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot


ROOT_DIR = Path(__file__).resolve().parents[1]
CLI_DIR = ROOT_DIR / 'maica cli'

if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from embedding_index import prewarm_embedding_model  # noqa: E402
from engine import MaicaEngine  # noqa: E402


class GuiEngineWorker(QObject):
    ready = Signal(dict)
    config_ready = Signal(dict)
    status = Signal(str)
    finished = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.engine: MaicaEngine | None = None

    @Slot()
    def initialize(self) -> None:
        try:
            self.engine = MaicaEngine()
            self._apply_gui_safety_overrides()
            self.config_ready.emit(dict(self.engine.config))
            self.ready.emit({'ok': True, 'error': ''})
            self._prewarm_embeddings_if_needed()
        except Exception as exc:
            self.ready.emit({'ok': False, 'error': f'{exc}\n{traceback.format_exc()}'})

    def _apply_gui_safety_overrides(self) -> None:
        if self.engine is None:
            return
        config = self.engine.config
        if not config.get('gui_disable_thread_embeddings', True):
            return
        disabled = []
        for key in ('embedding_enabled', 'memory_embedding_enabled'):
            if config.get(key):
                config[key] = False
                disabled.append(key)
        config['gui_prewarm_embeddings'] = False
        if disabled:
            self.status.emit(
                'GUI 已禁用线程内向量检索以避免 Qt worker 闪退；CLI Debugger 仍可使用向量检索。'
            )

    def _prewarm_embeddings_if_needed(self) -> None:
        if self.engine is None:
            return
        config = self.engine.config
        if not config.get('gui_prewarm_embeddings', False):
            return
        if not (config.get('embedding_enabled') or config.get('memory_embedding_enabled')):
            return
        self.status.emit('正在后台加载向量模型...')
        report = prewarm_embedding_model(
            config,
            quiet=bool(config.get('gui_quiet_embedding_load', True)),
        )
        if report.get('ok'):
            dim = report.get('dimension') or '?'
            self.status.emit(f'向量模型已就绪：{dim}d')
        else:
            self.status.emit(f'向量模型预热失败：{report.get("error")}')

    @Slot(str)
    def chat(self, text: str) -> None:
        self._run_request('chat', text)

    @Slot(str)
    def spire(self, hint: str) -> None:
        self._run_request('spire', hint)

    @Slot()
    def shutdown(self) -> None:
        if self.engine is not None:
            self.engine.close()
            self.engine = None

    def _run_request(self, mode: str, text: str) -> None:
        try:
            if self.engine is None:
                self.engine = MaicaEngine()
            if mode == 'spire':
                result = self.engine.spire(text)
            else:
                result = self.engine.chat(text)
            self.finished.emit(result)
        except Exception as exc:
            self.finished.emit(
                {
                    'ok': False,
                    'source': mode,
                    'text': '',
                    'emotion': 'concerned',
                    'action': {},
                    'mtrigger_notices': [],
                    'debug': {},
                    'error': f'{exc}\n{traceback.format_exc()}',
                }
            )
