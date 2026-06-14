# -*- coding: utf-8 -*-
"""GUI asset loading and layered Monika sprite composition."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap


def read_json(path: Path) -> dict[str, Any]:
    with path.open('r', encoding='utf-8-sig') as handle:
        return json.load(handle)


EXPRESSIONS = (
    'neutral', 'smile', 'happy', 'gentle', 'shy',
    'playful', 'thinking', 'concerned', 'sad', 'surprised',
)

# Aliases collapse only synonyms; the ten distinct expressions above are kept
# so the engine's full emotion range maps to distinct faces.
EMOTION_ALIASES = {
    'blush': 'shy',
    'embarrassed': 'shy',
    'deep_affection': 'shy',
    'worried': 'concerned',
    'anxious': 'concerned',
    'attentive': 'concerned',
    'soft': 'gentle',
    'calm': 'gentle',
    'tender': 'gentle',
    'smug': 'playful',
    'teasing': 'playful',
    'grin': 'happy',
    'joy': 'happy',
    'excited': 'happy',
    'focused': 'thinking',
    'curious': 'thinking',
    'curious_warm': 'thinking',
    'sorrow': 'sad',
    'upset': 'sad',
    'shock': 'surprised',
    'surprise': 'surprised',
}


def normalize_emotion(emotion: str) -> str:
    value = (emotion or '').lower().strip()
    value = EMOTION_ALIASES.get(value, value)
    return value if value in EXPRESSIONS else 'neutral'


class AssetManager:
    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = manifest_path
        self.root = manifest_path.parent
        self.manifest = read_json(manifest_path)
        self.assets = self.manifest.get('assets', {})
        self._pixmap_cache: dict[str, QPixmap] = {}
        self._avatar_cache: dict[str, QPixmap] = {}

    def path_for(self, rel_path: str) -> Path:
        return self.root / rel_path

    def pixmap(self, rel_path: str) -> QPixmap:
        if rel_path not in self._pixmap_cache:
            self._pixmap_cache[rel_path] = QPixmap(str(self.path_for(rel_path)))
        return self._pixmap_cache[rel_path]

    def background(self) -> QPixmap:
        rel_path = str(self.assets.get('background_default') or '')
        return self.pixmap(rel_path) if rel_path else QPixmap()

    def background_for_hour(self, hour: int) -> QPixmap:
        key = 'background_night' if hour >= 18 or hour < 6 else 'background_default'
        rel_path = str(self.assets.get(key) or self.assets.get('background_default') or '')
        return self.pixmap(rel_path) if rel_path else QPixmap()

    def background_for_mode(self, mode: str, hour: int) -> QPixmap:
        value = (mode or 'auto').lower().strip()
        key_map = {
            'day': 'background_default',
            'night': 'background_night',
            'rain': 'background_rain',
        }
        if value == 'auto':
            return self.background_for_hour(hour)
        key = key_map.get(value, 'background_default')
        rel_path = str(self.assets.get(key) or self.assets.get('background_default') or '')
        return self.pixmap(rel_path) if rel_path else QPixmap()

    def compose_avatar(self, emotion: str = 'neutral') -> QPixmap:
        normalized = normalize_emotion(emotion)
        if normalized in self._avatar_cache:
            return self._avatar_cache[normalized]

        layers: list[str] = []
        layers.extend(self.assets.get('monika_layers', []))
        expressions = self.assets.get('expressions', {})
        layers.extend(expressions.get(normalized) or expressions.get('neutral') or [])
        layers.extend(self.assets.get('monika_front_layers', []))

        canvas = QPixmap(1280, 850)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        for rel_path in layers:
            layer = self.pixmap(str(rel_path))
            if not layer.isNull():
                painter.drawPixmap(0, 0, layer)
        painter.end()

        self._avatar_cache[normalized] = canvas
        return canvas
