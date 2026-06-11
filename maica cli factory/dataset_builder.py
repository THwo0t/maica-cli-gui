# -*- coding: utf-8 -*-
"""Export chat logs into reviewable dialogue dataset files.

This module is privacy-first: it reads JSONL logs and writes cleaned/labeled
pairs, but it does not copy raw logs unless another tool does so explicitly.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any, Iterable


APP_DIR = Path(__file__).resolve().parent


def resolve_app_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (APP_DIR / path).resolve()


def iter_log_records(log_root: str | Path) -> Iterable[tuple[Path, dict[str, Any]]]:
    root = resolve_app_path(log_root)
    if not root.exists():
        return
    for path in sorted(root.rglob("*.jsonl")):
        with path.open("r", encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    record["_source_file"] = str(path.relative_to(root))
                    record["_source_line"] = line_number
                    yield path, record


def _response_plan(record: dict[str, Any]) -> dict[str, Any]:
    mfocus_plan = record.get("mfocus_plan")
    if not isinstance(mfocus_plan, dict):
        return {}
    response_plan = mfocus_plan.get("response_plan")
    return response_plan if isinstance(response_plan, dict) else {}


def _style_plan(record: dict[str, Any]) -> dict[str, Any]:
    mfocus_plan = record.get("mfocus_plan")
    if not isinstance(mfocus_plan, dict):
        return {}
    style = mfocus_plan.get("style")
    return style if isinstance(style, dict) else {}


def _clean_pair(record: dict[str, Any]) -> dict[str, Any] | None:
    user = str(record.get("user") or "").strip()
    assistant = str(record.get("assistant_text") or "").strip()
    if not assistant:
        return None
    return {
        "time": record.get("time", ""),
        "source": record.get("source", ""),
        "user_input": user,
        "assistant_reply": assistant,
        "source_file": record.get("_source_file", ""),
        "source_line": record.get("_source_line", 0),
    }


def _labeled_pair(record: dict[str, Any]) -> dict[str, Any] | None:
    clean = _clean_pair(record)
    if not clean:
        return None
    response_plan = _response_plan(record)
    style = _style_plan(record)
    category = str(response_plan.get("category") or style.get("category") or "daily")
    strategy = str(response_plan.get("mode") or "")
    emotion = str(record.get("emotion") or response_plan.get("emotion") or "neutral")
    return {
        **clean,
        "scene_category": category,
        "emotion": emotion,
        "reply_strategy": strategy,
        "length": str(response_plan.get("length") or ""),
        "quality_score": None,
        "notes": "",
    }


def _style_example(record: dict[str, Any]) -> dict[str, Any] | None:
    labeled = _labeled_pair(record)
    if not labeled:
        return None
    if not labeled["user_input"] or not labeled["assistant_reply"]:
        return None
    return {
        "user_text": labeled["user_input"],
        "assistant_text": labeled["assistant_reply"],
        "category": labeled["scene_category"],
        "emotion": labeled["emotion"],
        "strategy": labeled["reply_strategy"],
        "source_file": labeled["source_file"],
        "source_line": labeled["source_line"],
    }


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def ensure_dataset_skeleton(output_dir: str | Path) -> Path:
    output = resolve_app_path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "raw_logs").mkdir(parents=True, exist_ok=True)
    for name in ("bad_outputs.jsonl", "preference_pairs.jsonl"):
        path = output / name
        if not path.exists():
            path.touch()
    return output


def export_dialogue_dataset(
    log_root: str | Path = "logs",
    output_dir: str | Path = "dialogue_dataset",
) -> dict[str, Any]:
    output = ensure_dataset_skeleton(output_dir)
    records = [record for _, record in iter_log_records(log_root)]

    cleaned = [row for record in records if (row := _clean_pair(record))]
    labeled = [row for record in records if (row := _labeled_pair(record))]
    style_examples = [row for record in records if (row := _style_example(record))]

    result = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "log_root": str(resolve_app_path(log_root)),
        "output_dir": str(output),
        "records_read": len(records),
        "cleaned_pairs": _write_jsonl(output / "cleaned_pairs.jsonl", cleaned),
        "labeled_pairs": _write_jsonl(output / "labeled_pairs.jsonl", labeled),
        "style_examples": _write_jsonl(output / "style_examples.jsonl", style_examples),
        "bad_outputs": str(output / "bad_outputs.jsonl"),
        "preference_pairs": str(output / "preference_pairs.jsonl"),
    }
    with (output / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Export MAICA CLI logs into dialogue dataset JSONL files.")
    parser.add_argument("--log-root", default="logs", help="JSONL log root, default: logs")
    parser.add_argument("--output", default="dialogue_dataset", help="Output dataset directory")
    args = parser.parse_args()
    result = export_dialogue_dataset(args.log_root, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
