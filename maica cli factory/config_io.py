# -*- coding: utf-8 -*-
"""Small JSON config helpers shared by CLI, engine, and GUI tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if default is None:
        return data
    merged = default.copy()
    merged.update(data)
    return merged


def save_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    tmp.replace(path)
