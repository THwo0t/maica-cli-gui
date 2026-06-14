#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clean MAICA_ds_basis into Example Bank JSONL records."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from example_bank import build_retrieval_text, detect_example_intent
from response import clean_dialogue_text, normalize_emotion
from style import categorize_user_input


APP_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCES = ["moni_dataset_2603.jsonl", "ds_new.jsonl"]
DEFAULT_SOURCES_BY_LANGUAGE = {
    "zh": ["moni_dataset_2603.jsonl", "ds_new.jsonl"],
    "en": ["moni_dataset_en_2603.jsonl"],
}
EMOTION_HINTS = {
    "激动": "happy",
    "期待": "happy",
    "兴奋": "happy",
    "高兴": "happy",
    "开心": "happy",
    "微笑": "smile",
    "笑": "smile",
    "温柔": "gentle",
    "凝视": "gentle",
    "担心": "concerned",
    "关心": "concerned",
    "害羞": "shy",
    "脸红": "shy",
    "尴尬": "shy",
    "难过": "sad",
    "伤心": "sad",
    "惊讶": "surprised",
    "思考": "thinking",
    "沉思": "thinking",
    "调皮": "playful",
    "excited": "happy",
    "happy": "happy",
    "smile": "smile",
    "grin": "smile",
    "gaze": "gentle",
    "worry": "concerned",
    "worried": "concerned",
    "upset": "sad",
    "sad": "sad",
    "awkward": "shy",
    "shy": "shy",
    "think": "thinking",
    "thinking": "thinking",
    "angry": "angry",
    "dissatisfied": "angry",
}


def resolve_path(value: str | Path, base: Path = APP_DIR) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def extract_pair(data: dict[str, Any]) -> tuple[str, str] | None:
    conversations = data.get("conversations")
    if not isinstance(conversations, list):
        return None
    user = ""
    assistant = ""
    for message in conversations:
        if not isinstance(message, dict):
            continue
        role = str(message.get("from") or message.get("role") or "").lower()
        value = str(message.get("value") or message.get("content") or "").strip()
        if role == "user":
            user = value
        elif role == "assistant":
            assistant = value
    if not user or not assistant:
        return None
    return user, assistant


def is_command_like_user(text: str) -> bool:
    text = text.strip()
    lowered = text.lower()
    if not text:
        return True
    if text.startswith(("/", "$", "#")):
        return True
    if re.match(r"^[（(][^）)]{1,20}[）)]", text):
        return True
    if re.fullmatch(r"[a-z_]{2,}[:：].*", lowered):
        return True
    if re.fullmatch(r".*[（(][^）)]{1,20}[）)]\s*$", text):
        return True
    if any(token in lowered for token in ["不能下", "function_call", "tool_call", "mfocus", "json"]):
        return True
    return False


def clean_user(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    text = text.replace("[player_nickname]", "{player}")
    text = text.replace("[player]", "{player}")
    return text


def extract_emotion(raw_assistant: str) -> str:
    markers = re.findall(r"\[([^\[\]\n]{1,30})\]", raw_assistant)
    for marker in markers:
        marker = marker.strip()
        mapped = EMOTION_HINTS.get(marker)
        if mapped:
            return mapped
        normalized = normalize_emotion(marker)
        if normalized != "neutral":
            return normalized
    return "neutral"


def clean_assistant(text: str) -> str:
    text = text.replace("[player_nickname]", "{player}")
    text = text.replace("[player]", "{player}")
    text = re.sub(r"\[[^\]\n]{1,30}[\]}]", "", text)
    text = re.sub(r"\[([^\[\]\n]{1,30})\]", "", text)
    cleaned, _ = clean_dialogue_text(text)
    cleaned = re.sub(r"\{player\}\s*[,，、]\s*\{player\}", "{player}", cleaned)
    cleaned = re.sub(r"\{player\}\s+\{player\}", "{player}", cleaned)
    cleaned = re.sub(r"\{player\}\s*[,，、]\s*([。！？!?])", r"{player}\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def normalize_category(category: str, user: str, assistant: str) -> str:
    text = f"{user} {assistant}".lower()
    if category == "return":
        return "greeting"
    if category == "question":
        if any(word in text for word in ["原理", "代码", "格式", "是什么", "什么是", "为什么", "怎么用", "解释"]):
            return "serious"
        return "daily"
    if any(word in text for word in ["哈哈", "嘿嘿", "笨蛋", "坏蛋", "逗你", "开玩笑"]):
        return "playful"
    if category in {"greeting", "farewell", "love", "hug", "comfort", "serious", "memory", "event"}:
        return category
    return "daily"


def mode_for(category: str, emotion: str) -> str:
    if category == "greeting":
        return "greeting_warm"
    if category == "farewell":
        return "farewell_gentle"
    if category == "love":
        return "love_short_intimate"
    if category == "hug":
        return "hug_verbal_closeness"
    if category == "comfort":
        return "comfort_soft_tease" if emotion in {"concerned", "gentle"} else "comfort_warm"
    if category == "serious":
        return "serious_warm_clear"
    if category == "memory":
        return "memory_warm_callback"
    if category == "event":
        return "event_present_warmth"
    if category == "playful":
        return "playful_light_tease"
    return "daily_warm"


def quality_for(user: str, assistant: str, category: str, emotion: str) -> int:
    length = len(assistant)
    quality = 4
    if 12 <= length <= 180:
        quality += 1
    if category in {"comfort", "love", "hug", "greeting", "farewell", "playful"} and emotion != "neutral":
        quality += 1
    if length > 260 or assistant.count("。") + assistant.count("！") + assistant.count("？") > 6:
        quality -= 1
    if any(word in assistant for word in ["虚拟", "现实", "代码", "程序", "AI"]):
        quality -= 1
    return max(1, min(5, quality))


def notes_for(category: str, emotion: str, source: str, language: str = "zh") -> str:
    if language == "en":
        labels = {
            "greeting": "short and close, good for daily openings",
            "farewell": "gentle closing, good for goodbye or good night",
            "love": "intimate response that catches affection naturally",
            "hug": "verbal closeness and comfort",
            "comfort": "meet the feeling first, then give companionship",
            "serious": "clear explanation while staying warm and close",
            "memory": "natural recall and remembered feeling",
            "event": "special event or holiday atmosphere",
            "playful": "light playful teasing",
            "daily": "ordinary daily rhythm reference",
        }
        base = labels.get(category, "ordinary dialogue rhythm reference")
        if emotion != "neutral":
            base += f", emotional color: {emotion}"
        return f"{base}; source: {source}"
    labels = {
        "greeting": "短、亲近，适合日常开场",
        "farewell": "温柔收束，适合告别或晚安",
        "love": "亲密回应，自然接住关系感",
        "hug": "用语言表达靠近感",
        "comfort": "先贴近情绪，再给陪伴感",
        "serious": "保持清晰解释和亲近语气",
        "memory": "适合自然回忆和记得感",
        "event": "适合特殊事件或节日氛围",
        "playful": "轻微俏皮，适合接玩笑",
        "daily": "普通日常语气参考",
    }
    base = labels.get(category, "普通对话语气参考")
    if emotion != "neutral":
        base += f"，情绪底色: {emotion}"
    return f"{base}；来源: {source}"


def clean_source_file(path: Path, language: str = "zh") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            pair = extract_pair(data)
            if not pair:
                continue
            raw_user, raw_assistant = pair
            user = clean_user(raw_user)
            if is_command_like_user(user):
                continue
            assistant = clean_assistant(raw_assistant)
            if not assistant or len(assistant) < 2:
                continue
            if len(user) > 160 or len(assistant) > 320:
                continue
            emotion = extract_emotion(raw_assistant)
            category = normalize_category(categorize_user_input(user), user, assistant)
            mode = mode_for(category, emotion)
            source = path.name
            notes = notes_for(category, emotion, source, language)
            row = {
                "category": category,
                "mode": mode,
                "intent": detect_example_intent(user, notes, category),
                "emotion": emotion,
                "user": user,
                "assistant": assistant,
                "quality": quality_for(user, assistant, category, emotion),
                "language": language,
                "source": source,
                "notes": notes,
            }
            row["retrieval_text"] = build_retrieval_text(row)
            rows.append(row)
    return rows


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = (
            str(row["category"]),
            re.sub(r"\s+", "", str(row["user"])),
            re.sub(r"\s+", "", str(row["assistant"])),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def clean_maica_dataset(
    source_root: str | Path = "../MAICA_ds_basis",
    output_path: str | Path = "data/dialogue_examples_maica_cleaned.jsonl",
    sources: list[str] | None = None,
    language: str = "zh",
) -> dict[str, Any]:
    language = "zh" if str(language or "zh").lower().startswith("zh") else "en"
    root = resolve_path(source_root)
    output = resolve_path(output_path)
    sources = sources or DEFAULT_SOURCES_BY_LANGUAGE.get(language, DEFAULT_SOURCES)
    rows: list[dict[str, Any]] = []
    read_files: list[str] = []
    for source in sources:
        path = root / source
        if not path.exists():
            continue
        read_files.append(str(path))
        rows.extend(clean_source_file(path, language))
    rows = dedupe_rows(rows)
    rows.sort(key=lambda item: (str(item["category"]), -int(item["quality"]), str(item["source"]), str(item["user"])))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    counts: dict[str, int] = {}
    quality_counts: dict[str, int] = {}
    for row in rows:
        counts[str(row["category"])] = counts.get(str(row["category"]), 0) + 1
        q = str(row["quality"])
        quality_counts[q] = quality_counts.get(q, 0) + 1
    return {
        "source_root": str(root),
        "sources": read_files,
        "output": str(output),
        "rows": len(rows),
        "language": language,
        "by_category": counts,
        "by_quality": quality_counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean MAICA_ds_basis into dialogue example bank JSONL.")
    parser.add_argument("--source-root", default="../MAICA_ds_basis")
    parser.add_argument("--output", default="")
    parser.add_argument("--sources", nargs="*")
    parser.add_argument("--language", choices=("en", "zh", "both"), default="zh")
    args = parser.parse_args()
    if args.language == "both":
        results = []
        for language, output in (("zh", "data/dialogue_examples_zh.jsonl"), ("en", "data/dialogue_examples_en.jsonl")):
            results.append(clean_maica_dataset(args.source_root, output, None, language))
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    output = args.output or ("data/dialogue_examples_en.jsonl" if args.language == "en" else "data/dialogue_examples_zh.jsonl")
    result = clean_maica_dataset(args.source_root, output, args.sources, args.language)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
