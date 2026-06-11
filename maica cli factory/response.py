# -*- coding: utf-8 -*-
"""Structured assistant response parsing.

The display text must stay clean for future GUI rendering. Emotion and action
metadata are stored separately and must not appear in the dialogue body.
"""

from __future__ import annotations

import json
import re
from typing import Any


DEFAULT_EMOTION = "neutral"
ALLOWED_EMOTIONS = {
    "neutral",
    "smile",
    "happy",
    "gentle",
    "shy",
    "concerned",
    "sad",
    "surprised",
    "thinking",
    "playful",
}


EMOTION_ALIASES = {
    "微笑": "smile",
    "笑": "smile",
    "grin": "smile",
    "开心": "happy",
    "高兴": "happy",
    "happy": "happy",
    "温柔": "gentle",
    "凝视": "gentle",
    "gaze": "gentle",
    "害羞": "shy",
    "awkward": "shy",
    "担心": "concerned",
    "worry": "concerned",
    "worried": "concerned",
    "关心": "concerned",
    "生气": "concerned",
    "难过": "sad",
    "sad": "sad",
    "惊讶": "surprised",
    "surprise": "surprised",
    "思考": "thinking",
    "沉思": "thinking",
    "think": "thinking",
    "调皮": "playful",
    "playful": "playful",
}


ACTION_WORDS = [
    "微笑",
    "笑",
    "脸红",
    "害羞",
    "叹气",
    "眨眼",
    "抱住你",
    "抱抱",
    "看着你",
    "歪头",
    "沉思",
    "smile",
    "smiles",
    "hug",
    "hugs",
    "blush",
    "sigh",
    "wink",
    "thinking",
]


def _is_removable_marker(content: str) -> bool:
    lowered = content.strip().lower()
    if lowered in {"player", "name"}:
        return False
    if normalize_emotion(content) != DEFAULT_EMOTION:
        return True
    return any(word in content for word in ACTION_WORDS)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.I).strip()
        text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def normalize_emotion(value: Any) -> str:
    raw = str(value or "").strip().lower()
    raw = EMOTION_ALIASES.get(raw, raw)
    if raw in ALLOWED_EMOTIONS:
        return raw
    return DEFAULT_EMOTION


def clean_dialogue_text(text: str) -> tuple[str, list[str]]:
    """Remove bracket/parenthesis action markers from dialogue body."""
    text = str(text or "")
    removed = []

    def collect(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        if content and _is_removable_marker(content):
            removed.append(content)
            return ""
        if content:
            return match.group(0)
        return ""

    # Remove whole-line or inline action/emotion markers like [微笑], （抱抱）, (smile).
    text = re.sub(r"\[([^\[\]\n]{1,30})\]", collect, text)
    text = re.sub(r"（([^（）\n]{1,30})）", collect, text)
    text = re.sub(r"\(([^()\n]{1,30})\)", collect, text)

    # Remove common markdown/emote action style: *smiles*, *抱抱*.
    def collect_star(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        if any(word in content for word in ACTION_WORDS) or re.fullmatch(r"[A-Za-z _-]{1,30}", content):
            removed.append(content)
            return ""
        return match.group(0)

    text = re.sub(r"\*([^*\n]{1,30})\*", collect_star, text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.!?;:，。！？；：])", r"\1", text)
    return text, removed


def _parse_segments(data: dict[str, Any]) -> tuple[str, str, dict[str, Any], list[str]]:
    segments = data.get("segments")
    if not isinstance(segments, list):
        return "", DEFAULT_EMOTION, {}, []

    texts: list[str] = []
    cleaned_segments: list[dict[str, Any]] = []
    removed: list[str] = []
    first_emotion = DEFAULT_EMOTION
    for item in segments:
        if not isinstance(item, dict):
            continue
        raw_text = str(item.get("text") or item.get("dialogue") or "").strip()
        clean_text, removed_here = clean_dialogue_text(raw_text)
        removed.extend(removed_here)
        if not clean_text:
            continue
        emotion = normalize_emotion(item.get("emotion"))
        if first_emotion == DEFAULT_EMOTION and emotion != DEFAULT_EMOTION:
            first_emotion = emotion
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        texts.append(clean_text)
        cleaned_segments.append({"text": clean_text, "emotion": emotion, "action": action})

    if not texts:
        return "", DEFAULT_EMOTION, {}, removed
    action = {"segments": cleaned_segments}
    if isinstance(data.get("action"), dict):
        action.update(data["action"])
    return "\n".join(texts), first_emotion, action, removed


def parse_assistant_response(raw_reply: str) -> dict[str, Any]:
    """Return {text, emotion, action, raw, removed_markers}."""
    data = _extract_json_object(raw_reply)
    if data:
        segment_text, segment_emotion, segment_action, segment_removed = _parse_segments(data)
        if segment_text:
            return {
                "text": segment_text,
                "emotion": segment_emotion,
                "action": segment_action,
                "raw": raw_reply,
                "removed_markers": segment_removed,
            }
        text = str(data.get("text") or data.get("dialogue") or "").strip()
        emotion = normalize_emotion(data.get("emotion"))
        action = data.get("action") if isinstance(data.get("action"), dict) else {}
        if not isinstance(action, dict):
            action = {}
        clean_text, removed = clean_dialogue_text(text)
        if not clean_text:
            clean_text, removed_fallback = clean_dialogue_text(raw_reply)
            removed.extend(removed_fallback)
        return {
            "text": clean_text,
            "emotion": emotion,
            "action": action,
            "raw": raw_reply,
            "removed_markers": removed,
        }

    clean_text, removed = clean_dialogue_text(raw_reply)
    emotion = DEFAULT_EMOTION
    for marker in removed:
        normalized = normalize_emotion(marker)
        if normalized != DEFAULT_EMOTION:
            emotion = normalized
            break
    return {
        "text": clean_text,
        "emotion": emotion,
        "action": {"removed_markers": removed} if removed else {},
        "raw": raw_reply,
        "removed_markers": removed,
    }


def limit_dialogue_sentences(text: str, max_sentences: int) -> str:
    """Keep only the first N sentence-like chunks."""
    max_sentences = max(1, int(max_sentences))
    parts = re.findall(r"[^。！？.!?]+[。！？.!?]?", str(text or ""))
    parts = [part.strip() for part in parts if part.strip()]
    if len(parts) <= max_sentences:
        return str(text or "").strip()
    return "".join(parts[:max_sentences]).strip()


def response_format_instruction(language: str = "zh") -> str:
    if language.lower().startswith("en"):
        return (
            'Output exactly one JSON object. Prefer {"segments":[{"text":"one natural sentence",'
            '"emotion":"smile","action":{"type":"none"}}]}. You may also use the legacy '
            '{"text":"dialogue body","emotion":"smile","action":{"type":"none"}} format.'
        )
    return (
        '只输出一个 JSON 对象. 优先使用 {"segments":[{"text":"一句自然的话","emotion":"smile","action":{"type":"none"}}]}. '
        '也可以使用旧格式 {"text":"对话正文","emotion":"smile","action":{"type":"none"}}. '
        '每个 segment 对应一句话, emotion/action 表示这一句的情绪和动作元数据.'
    )
