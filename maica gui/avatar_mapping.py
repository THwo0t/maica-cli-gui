# -*- coding: utf-8 -*-
"""Validated standard emotion/action mappings for avatar backends."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any


GUI_DIR = Path(__file__).resolve().parent
DEFAULT_MAPPING_PATH = GUI_DIR / 'live2d_expression_map.json'
STANDARD_EMOTIONS = {
    'neutral', 'smile', 'happy', 'gentle', 'shy', 'playful',
    'thinking', 'concerned', 'sad', 'surprised',
}
STANDARD_ACTIONS = {'wave', 'nod', 'surprise', 'pout'}
PARAMETER_ID = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,79}$')


class AvatarMappingError(ValueError):
    pass


def load_avatar_mapping(
    configured_path: str | Path | None = None,
    model_entry: str | Path | None = None,
) -> tuple[dict[str, Any], list[str]]:
    mapping = _load_mapping_file(DEFAULT_MAPPING_PATH)
    sources = [str(DEFAULT_MAPPING_PATH)]
    candidates: list[Path] = []
    if configured_path and str(configured_path).strip():
        candidates.append(Path(configured_path).expanduser())
    if model_entry and str(model_entry).strip():
        entry = Path(model_entry).expanduser()
        candidates.append(entry.parent / 'maica_avatar_map.json')
    for candidate in candidates:
        if not candidate.is_file():
            continue
        override = _load_mapping_file(candidate)
        mapping = _merge_mapping(mapping, override)
        sources.append(str(candidate.resolve()))
    return validate_avatar_mapping(mapping), sources


def validate_avatar_mapping(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AvatarMappingError('Avatar mapping must be a JSON object')
    result: dict[str, Any] = {'version': 1, 'emotions': {}, 'actions': {}}
    emotions = payload.get('emotions') if isinstance(payload.get('emotions'), dict) else {}
    for emotion, raw in emotions.items():
        name = str(emotion).strip().lower()
        if name not in STANDARD_EMOTIONS or not isinstance(raw, dict):
            continue
        expressions = _string_list(raw.get('expressions'), 8)
        parameters: dict[str, float] = {}
        for parameter, value in (raw.get('parameters') or {}).items():
            parameter_name = str(parameter).strip()
            if PARAMETER_ID.fullmatch(parameter_name) and isinstance(value, (int, float)):
                parameters[parameter_name] = max(-100.0, min(100.0, float(value)))
        result['emotions'][name] = {'expressions': expressions, 'parameters': parameters}

    actions = payload.get('actions') if isinstance(payload.get('actions'), dict) else {}
    for action, raw in actions.items():
        name = str(action).strip().lower()
        if name not in STANDARD_ACTIONS or not isinstance(raw, dict):
            continue
        fallback = str(raw.get('fallback_emotion') or 'neutral').strip().lower()
        if fallback not in STANDARD_EMOTIONS:
            fallback = 'neutral'
        result['actions'][name] = {
            'motions': _string_list(raw.get('motions'), 8),
            'fallback_emotion': fallback,
        }
    return result


def save_avatar_mapping(path: str | Path, payload: Any) -> Path:
    validated = validate_avatar_mapping(payload)
    target = Path(path).expanduser().resolve(strict=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(validated, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return target


def _load_mapping_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AvatarMappingError(f'Invalid avatar mapping {path}: {exc}') from exc
    return validate_avatar_mapping(payload)


def _merge_mapping(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for section in ('emotions', 'actions'):
        merged.setdefault(section, {}).update(copy.deepcopy(override.get(section) or {}))
    return merged


def _string_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        text = str(item or '').strip()
        if text and len(text) <= 120 and text not in output:
            output.append(text)
        if len(output) >= limit:
            break
    return output
