#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import MAICA_ds_basis JSONL files into the local style database."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from maica_cli import DEFAULT_CONFIG, load_json
from style import DEFAULT_STYLE_SOURCES, import_default_style_sources


APP_DIR = Path(__file__).resolve().parent


def load_config(path: Path) -> dict[str, Any]:
    config = DEFAULT_CONFIG.copy()
    config.update(load_json(path, {}))
    if not config.get("style_sources"):
        config["style_sources"] = DEFAULT_STYLE_SOURCES
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description="Import MAICA basis dataset into style.db")
    parser.add_argument("--config", default=str(APP_DIR / "config.json"), help="Path to config.json")
    parser.add_argument("--source-root", default="", help="Optional folder containing dataset jsonl files")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    source_root = args.source_root or None
    result = import_default_style_sources(config, source_root=source_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
