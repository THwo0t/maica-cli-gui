# -*- coding: utf-8 -*-
"""Small JSON config helpers shared by CLI, engine, and GUI tools."""

from __future__ import annotations

import json
import datetime as dt
from pathlib import Path
from typing import Any


def load_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except json.JSONDecodeError:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        broken = path.with_suffix(path.suffix + f".broken-{stamp}")
        try:
            path.replace(broken)
        except OSError:
            pass
        return dict(default or {})
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
