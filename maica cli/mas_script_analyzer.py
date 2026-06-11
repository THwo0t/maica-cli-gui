#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Analyze decompiled MAS Ren'Py scripts for style and reflection patterns.

This tool intentionally does not decompile .rpyc files. Point it at a folder
containing decompiled .rpy files, such as MAS_decompiled/, and it will produce
JSON/JSONL analysis files that can guide MAICA CLI style tuning.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DIALOGUE_RE = re.compile(
    r'^\s*(?P<speaker>m|monika|extend)\b(?:\s+(?P<expr>[A-Za-z0-9_]+))?\s+"(?P<text>(?:\\.|[^"\\])*)"',
    re.I,
)
LABEL_RE = re.compile(r"^\s*label\s+(?P<label>[A-Za-z0-9_]+)\s*:")
IF_RE = re.compile(r"^\s*(if|elif)\s+(?P<condition>.+):")


CATEGORY_KEYWORDS = {
    "greeting": ["greeting", "greetings", "hello", "back"],
    "farewell": ["farewell", "goodbye", "bye"],
    "brb": ["brb", "be_right_back", "bathroom", "going"],
    "mood": ["mood", "upset", "sad", "happy", "tired"],
    "compliment": ["compliment", "compliments"],
    "apology": ["apology", "apologies", "sorry"],
    "holiday": ["holiday", "christmas", "halloween", "birthday", "valentine", "new_year"],
    "anniversary": ["anniversary"],
    "story": ["story", "stories"],
    "song": ["song", "songs", "music"],
    "reaction": ["reaction", "windowreact"],
    "topic": ["topic", "topics"],
}


REFLECTIVE_TOPICS = {
    "time": ["时间", "今天", "明天", "昨天", "未来", "过去", "季节", "year", "future", "past", "time"],
    "memory": ["记忆", "回忆", "记得", "忘记", "remember", "memory"],
    "rain": ["雨", "下雨", "天气", "rain", "weather"],
    "music": ["音乐", "钢琴", "歌", "旋律", "piano", "music", "song"],
    "literature": ["文学", "诗", "书", "阅读", "poem", "poetry", "book", "literature"],
    "loneliness": ["孤独", "寂寞", "alone", "lonely"],
    "self_improvement": ["努力", "习惯", "改变", "成长", "更好", "improve", "habit", "change"],
    "work_study": ["学习", "工作", "考试", "作业", "study", "work", "exam"],
    "stars": ["星空", "星星", "夜空", "star", "sky"],
}


AVOID_REFLECTION = ["代码", "程序", "AI", "模型", "虚拟", "现实边界", "永恒", "命运"]


def decode_renpy_string(text: str) -> str:
    return (
        text.replace(r"\"", '"')
        .replace(r"\n", "\n")
        .replace(r"\t", "\t")
        .replace(r"\\", "\\")
    )


def categorize(path: Path, label: str) -> str:
    haystack = f"{path.name} {label}".lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            return category
    return "other"


def detect_reflective_topics(text: str) -> list[str]:
    lowered = text.lower()
    topics = []
    for topic, keywords in REFLECTIVE_TOPICS.items():
        if any(keyword.lower() in lowered for keyword in keywords):
            topics.append(topic)
    return topics


def is_grandiose(text: str) -> bool:
    return any(word.lower() in text.lower() for word in AVOID_REFLECTION)


def split_sentences(text: str) -> list[str]:
    parts = re.findall(r"[^。！？.!?]+[。！？.!?]?", text)
    return [part.strip() for part in parts if part.strip()]


def parse_rpy(path: Path) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    current_label = ""
    current_conditions: list[str] = []
    buffer: list[str] = []
    expressions: list[str] = []
    start_line = 0

    def flush(end_line: int) -> None:
        nonlocal buffer, expressions, start_line
        if not buffer:
            return
        clean_text = "\n".join(buffer).strip()
        if not clean_text:
            buffer = []
            expressions = []
            return
        sentences = split_sentences(clean_text)
        reflective_topics = detect_reflective_topics(clean_text)
        units.append(
            {
                "source_file": path.name,
                "label": current_label,
                "category": categorize(path, current_label),
                "conditions": current_conditions[-5:],
                "expressions": expressions,
                "text": clean_text,
                "line_count": len(buffer),
                "sentence_count": len(sentences),
                "char_count": len(clean_text),
                "has_player_placeholder": "[player]" in clean_text,
                "reflective_topics": reflective_topics,
                "is_reflective_candidate": bool(reflective_topics) and not is_grandiose(clean_text),
                "start_line": start_line,
                "end_line": end_line,
            }
        )
        buffer = []
        expressions = []
        start_line = 0

    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.lstrip("\ufeff")
            label_match = LABEL_RE.match(line)
            if label_match:
                flush(line_no - 1)
                current_label = label_match.group("label")
                current_conditions = []
                continue

            condition_match = IF_RE.match(line)
            if condition_match:
                condition = condition_match.group("condition").strip()
                if condition:
                    current_conditions.append(condition)

            dialogue_match = DIALOGUE_RE.match(line)
            if dialogue_match:
                speaker = dialogue_match.group("speaker").lower()
                expr = dialogue_match.group("expr") or ""
                text = decode_renpy_string(dialogue_match.group("text"))
                if speaker != "extend" and buffer:
                    flush(line_no - 1)
                if not start_line:
                    start_line = line_no
                buffer.append(text)
                if expr:
                    expressions.append(expr)
                continue

            stripped = line.strip()
            if not stripped or stripped.startswith(("return", "jump ", "label ", "menu:")):
                flush(line_no - 1)

    flush(line_no if "line_no" in locals() else 0)
    return units


def summarize_units(units: list[dict[str, Any]]) -> dict[str, Any]:
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reflective_counter: Counter[str] = Counter()
    for unit in units:
        by_category[unit["category"]].append(unit)
        reflective_counter.update(unit["reflective_topics"])

    category_stats = {}
    for category, rows in by_category.items():
        char_counts = [row["char_count"] for row in rows]
        sentence_counts = [row["sentence_count"] for row in rows]
        category_stats[category] = {
            "count": len(rows),
            "avg_chars": round(sum(char_counts) / len(char_counts), 2),
            "avg_sentences": round(sum(sentence_counts) / len(sentence_counts), 2),
            "short_ratio": round(sum(1 for value in sentence_counts if value <= 2) / len(rows), 3),
            "reflective_candidates": sum(1 for row in rows if row["is_reflective_candidate"]),
        }

    return {
        "total_units": len(units),
        "category_stats": category_stats,
        "reflective_topics": dict(reflective_counter.most_common()),
    }


def build_reflective_profile(units: list[dict[str, Any]]) -> dict[str, Any]:
    reflective_units = [unit for unit in units if unit["is_reflective_candidate"]]
    topic_counter: Counter[str] = Counter()
    category_counter: Counter[str] = Counter()
    for unit in reflective_units:
        topic_counter.update(unit["reflective_topics"])
        category_counter[unit["category"]] += 1
    return {
        "daily_reflection": {
            "allowed": True,
            "max_sentences": 1,
            "topics": list(topic_counter.keys()),
            "best_categories": list(category_counter.keys()),
            "avoid": AVOID_REFLECTION,
            "style": "gentle observation grounded in the immediate topic; invite discussion without lecturing",
            "candidate_count": len(reflective_units),
        }
    }


def write_outputs(units: list[dict[str, Any]], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    units_path = output_dir / "mas_dialogue_units.jsonl"
    stats_path = output_dir / "mas_script_stats.json"
    reflective_path = output_dir / "mas_reflective_profile.json"

    with units_path.open("w", encoding="utf-8") as handle:
        for unit in units:
            handle.write(json.dumps(unit, ensure_ascii=False) + "\n")
    stats_path.write_text(json.dumps(summarize_units(units), ensure_ascii=False, indent=2), encoding="utf-8")
    reflective_path.write_text(json.dumps(build_reflective_profile(units), ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "units": str(units_path),
        "stats": str(stats_path),
        "reflective_profile": str(reflective_path),
    }


def analyze(input_dir: Path, output_dir: Path) -> dict[str, Any]:
    paths = sorted(input_dir.rglob("*.rpy"))
    units: list[dict[str, Any]] = []
    for path in paths:
        # Skip Ren'Py engine common files if a whole game folder is provided.
        if "renpy" in [part.lower() for part in path.parts]:
            continue
        units.extend(parse_rpy(path))
    outputs = write_outputs(units, output_dir)
    return {"input_dir": str(input_dir), "files": len(paths), "units": len(units), "outputs": outputs}


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze decompiled MAS .rpy scripts")
    parser.add_argument("--input", default="../MAS_decompiled", help="Folder containing decompiled .rpy files")
    parser.add_argument("--output", default="analysis", help="Output folder for JSON/JSONL analysis")
    args = parser.parse_args()

    base = Path(__file__).resolve().parent
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    if not input_dir.is_absolute():
        input_dir = (base / input_dir).resolve()
    if not output_dir.is_absolute():
        output_dir = (base / output_dir).resolve()
    if not input_dir.exists():
        raise SystemExit(f"Input folder not found: {input_dir}")
    print(json.dumps(analyze(input_dir, output_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
