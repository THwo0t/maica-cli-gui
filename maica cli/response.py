# -*- coding: utf-8 -*-
"""Assistant response parsing and cleanup.

The GUI/Live2D side needs structured metadata, but the language model may reply
as JSON or as natural text with a lightweight emotion marker.
"""

from __future__ import annotations

import re
from typing import Any

from text_utils import extract_json_object


DEFAULT_EMOTION = 'neutral'
ALLOWED_EMOTIONS = {
    'neutral',
    'smile',
    'happy',
    'gentle',
    'shy',
    'concerned',
    'sad',
    'surprised',
    'thinking',
    'playful',
    'angry',
}

EMOTION_ALIASES = {
    '微笑': 'smile',
    '笑': 'smile',
    '开心': 'happy',
    '高兴': 'happy',
    '温柔': 'gentle',
    '凝视': 'gentle',
    '害羞': 'shy',
    '尴尬': 'shy',
    '担心': 'concerned',
    '关心': 'concerned',
    '难过': 'sad',
    '惊讶': 'surprised',
    '思考': 'thinking',
    '沉思': 'thinking',
    '调皮': 'playful',
    '生气': 'angry',
    'worried': 'concerned',
    'worry': 'concerned',
    'awkward': 'shy',
    'gaze': 'gentle',
    'grin': 'smile',
    'think': 'thinking',
}

ACTION_WORDS = {
    '微笑',
    '笑',
    '脸红',
    '害羞',
    '叹气',
    '看着你',
    '抱住你',
    '抱抱',
    '歪头',
    '沉思',
    'smile',
    'smiles',
    'hug',
    'hugs',
    'blush',
    'sigh',
    'wink',
    'thinking',
    'looks at you',
    'holds your hand',
}


def normalize_emotion(value: Any) -> str:
    raw = str(value or '').strip().lower()
    raw = EMOTION_ALIASES.get(raw, raw)
    if raw in ALLOWED_EMOTIONS:
        return raw
    return DEFAULT_EMOTION


def _is_removable_marker(content: str) -> bool:
    marker = content.strip()
    lowered = marker.lower()
    if lowered in {'player', 'name'}:
        return False
    if normalize_emotion(marker) != DEFAULT_EMOTION:
        return True
    return any(word in marker or word in lowered for word in ACTION_WORDS)


def clean_dialogue_text(text: str) -> tuple[str, list[str]]:
    """Remove bracket/parenthesis action markers from dialogue body."""
    text = str(text or '')
    removed: list[str] = []

    def collect(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        if content and _is_removable_marker(content):
            removed.append(content)
            return ''
        return match.group(0) if content else ''

    text = re.sub(r'\[([^\[\]\n]{1,40})\]', collect, text)
    text = re.sub(r'（([^（）\n]{1,60})）', collect, text)
    text = re.sub(r'\(([^()\n]{1,60})\)', collect, text)

    def collect_star(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        lowered = content.lower()
        if any(word in content or word in lowered for word in ACTION_WORDS):
            removed.append(content)
            return ''
        return match.group(0)

    text = re.sub(r'\*([^*\n]{1,60})\*', collect_star, text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\s+([,.!?;:，。！？；：])', r'\1', text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return '\n'.join(lines).strip(), removed


def _extract_leading_marker(raw_reply: str) -> tuple[str, str, list[str]]:
    text = str(raw_reply or '').strip()
    markers: list[str] = []
    emotion = DEFAULT_EMOTION
    match = re.match(r'^\s*\[([^\[\]\n]{1,40})\]\s*', text)
    if match:
        marker = match.group(1).strip()
        normalized = normalize_emotion(marker)
        if normalized != DEFAULT_EMOTION:
            emotion = normalized
            markers.append(marker)
            text = text[match.end():]
    clean, removed = clean_dialogue_text(text)
    markers.extend(removed)
    if emotion == DEFAULT_EMOTION:
        for marker in markers:
            normalized = normalize_emotion(marker)
            if normalized != DEFAULT_EMOTION:
                emotion = normalized
                break
    return clean, emotion, markers


def _parse_segments(data: dict[str, Any]) -> tuple[str, str, dict[str, Any], list[str]]:
    segments = data.get('segments')
    if not isinstance(segments, list):
        return '', DEFAULT_EMOTION, {}, []

    texts: list[str] = []
    cleaned_segments: list[dict[str, Any]] = []
    removed: list[str] = []
    first_emotion = DEFAULT_EMOTION
    for item in segments:
        if not isinstance(item, dict):
            continue
        raw_text = str(item.get('text') or item.get('dialogue') or '').strip()
        clean_text, removed_here = clean_dialogue_text(raw_text)
        removed.extend(removed_here)
        if not clean_text:
            continue
        emotion = normalize_emotion(item.get('emotion'))
        if first_emotion == DEFAULT_EMOTION and emotion != DEFAULT_EMOTION:
            first_emotion = emotion
        action = item.get('action') if isinstance(item.get('action'), dict) else {}
        texts.append(clean_text)
        cleaned_segments.append({'text': clean_text, 'emotion': emotion, 'action': action})

    if not texts:
        return '', DEFAULT_EMOTION, {}, removed
    action = {'segments': cleaned_segments}
    if isinstance(data.get('action'), dict):
        action.update(data['action'])
    return '\n'.join(texts), first_emotion, action, removed


def parse_assistant_response(raw_reply: str) -> dict[str, Any]:
    """Return {text, emotion, action, raw, removed_markers}."""
    data = extract_json_object(raw_reply)
    if data:
        segment_text, segment_emotion, segment_action, segment_removed = _parse_segments(data)
        if segment_text:
            return {
                'text': segment_text,
                'emotion': segment_emotion,
                'action': segment_action,
                'raw': raw_reply,
                'removed_markers': segment_removed,
            }
        text = str(data.get('text') or data.get('dialogue') or '').strip()
        emotion = normalize_emotion(data.get('emotion'))
        action = data.get('action') if isinstance(data.get('action'), dict) else {}
        clean_text, removed = clean_dialogue_text(text)
        if not clean_text:
            clean_text, removed_fallback = _extract_leading_marker(raw_reply)[0::2]
            removed.extend(removed_fallback)
        return {
            'text': clean_text,
            'emotion': emotion,
            'action': action,
            'raw': raw_reply,
            'removed_markers': removed,
        }

    clean_text, emotion, removed = _extract_leading_marker(raw_reply)
    return {
        'text': clean_text,
        'emotion': emotion,
        'action': {'removed_markers': removed} if removed else {},
        'raw': raw_reply,
        'removed_markers': removed,
    }


def limit_dialogue_sentences(text: str, max_sentences: int) -> str:
    max_sentences = max(1, int(max_sentences))
    parts = re.findall(r'[^。！？.!?]+[。！？.!?]?', str(text or ''))
    parts = [part.strip() for part in parts if part.strip()]
    if len(parts) <= max_sentences:
        return str(text or '').strip()
    return ''.join(parts[:max_sentences]).strip()


def response_format_instruction(language: str = 'zh', mode: str = 'dual') -> str:
    english = str(language or '').lower().startswith('en')
    if mode == 'json':
        return (
            'Output exactly one JSON object: {"segments":[{"text":"one natural sentence",'
            '"emotion":"smile","action":{"type":"none"}}]}.'
        )
    if mode == 'legacy_marker':
        if english:
            return 'Reply in natural English. You may prefix one emotion marker like [smile], [shy], or [concerned].'
        return '请自然回复。可以在开头使用一个情绪标记，例如 [smile]、[shy]、[concerned]。'
    if english:
        return (
            'Reply in natural English. Prefer plain dialogue with an optional leading emotion marker '
            'like [smile], [shy], or [concerned]. JSON is also accepted if it feels natural.'
        )
    return '请自然回复。可以使用开头情绪标记，也可以输出 JSON；正文必须像自然聊天。'
