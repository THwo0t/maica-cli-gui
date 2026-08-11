# -*- coding: utf-8 -*-
"""Safe runtime configuration storage and controller update routing."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal


SECRET_CONFIG_MARKERS = ('key', 'token', 'secret', 'password')
HIDDEN_SECRET_VALUES = {'<hidden>', '***'}

SPEECH_KEYS = {
    'language', 'tts_enabled', 'tts_provider', 'tts_bailian_api_key',
    'tts_bailian_model', 'tts_bailian_voice', 'tts_bailian_format',
    'tts_bailian_instruction', 'speech_streaming_enabled',
    'speech_queue_behavior', 'speech_max_concurrency',
    'lip_sync_sensitivity', 'audio_output_device',
}
AVATAR_KEYS = {
    'avatar_backend', 'vts_url', 'vts_plugin_name', 'vts_plugin_developer',
    'vts_auth_token', 'vts_parameter_prefix', 'live2d_model_path',
    'live2d_core_path', 'live2d_render_fps', 'live2d_eye_tracking',
    'live2d_transparent_background', 'live2d_expression_map_path',
    'live2d_mouth_attack_ms', 'live2d_mouth_release_ms',
}
STT_KEYS = {'language', 'stt_provider', 'stt_language', 'stt_timeout', 'stt_bailian_api_key'}
APPEARANCE_KEYS = {'gui_background_mode'}


def is_secret_config_key(key: str) -> bool:
    lowered = str(key or '').lower()
    return any(marker in lowered for marker in SECRET_CONFIG_MARKERS)


def merge_runtime_config(target: dict[str, Any], updates: dict[str, Any]) -> set[str]:
    """Merge snapshots without replacing real secrets with redaction markers."""
    changed: set[str] = set()
    for key, value in updates.items():
        if is_secret_config_key(key) and str(value or '') in HIDDEN_SECRET_VALUES:
            continue
        if key not in target or target.get(key) != value:
            target[key] = value
            changed.add(key)
    return changed


class RuntimeSettingsController(QObject):
    """Keep one mutable config and notify only the affected subsystems."""

    speech_changed = Signal(dict)
    avatar_changed = Signal(dict, bool)
    stt_changed = Signal(dict)
    appearance_changed = Signal(dict)
    config_changed = Signal(dict, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config: dict[str, Any] = {}

    def apply(self, updates: dict[str, Any], force_avatar: bool = False, force_all: bool = False) -> set[str]:
        changed = merge_runtime_config(self.config, updates)
        effective = set(updates) if force_all else changed
        snapshot = dict(self.config)
        if effective & SPEECH_KEYS:
            self.speech_changed.emit(snapshot)
        if effective & AVATAR_KEYS or force_avatar:
            self.avatar_changed.emit(snapshot, bool(force_avatar))
        if effective & STT_KEYS:
            self.stt_changed.emit(snapshot)
        if effective & APPEARANCE_KEYS:
            self.appearance_changed.emit(snapshot)
        if changed or force_all or force_avatar:
            self.config_changed.emit(snapshot, changed)
        return changed
