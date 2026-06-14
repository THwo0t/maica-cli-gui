# -*- coding: utf-8 -*-
"""Shared UTF-8-safe text helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


MOJIBAKE_MARKERS = (
    '浣犳',
    '鑾',
    '濂',
    '鏄',
    '绋',
    '锛',
    '瘂',
)


def split_query_tokens(text: str) -> list[str]:
    """Split mixed Chinese/English text into search-friendly tokens."""
    tokens: list[str] = []
    for part in re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]+', str(text or '').lower()):
        if re.fullmatch(r'[\u4e00-\u9fff]+', part):
            if len(part) == 1:
                tokens.append(part)
            else:
                tokens.extend(part[index:index + 2] for index in range(len(part) - 1))
        elif len(part) >= 2:
            tokens.append(part)
    return tokens


def contains_cjk(text: str) -> bool:
    return any('\u4e00' <= char <= '\u9fff' for char in str(text or ''))


def cjk_ratio(text: str) -> float:
    chars = [char for char in str(text or '') if not char.isspace()]
    if not chars:
        return 0.0
    return sum(1 for char in chars if '\u4e00' <= char <= '\u9fff') / len(chars)


def redact_secret(text: str, *secrets: str) -> str:
    """Remove API keys and bearer tokens before text reaches UI/log output."""
    out = str(text or '')
    for secret in secrets:
        secret = str(secret or '').strip()
        if secret and len(secret) >= 6:
            out = out.replace(secret, '***')
    out = re.sub(r'(?i)(authorization\s*:\s*bearer\s+)\S+', r'\1***', out)
    out = re.sub(r'(?i)(bearer)\s+\S+', r'\1 ***', out)
    out = re.sub(r'(?i)(sk-)[A-Za-z0-9._\-]{6,}', r'\1***', out)
    out = re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password)\s*[=:]\s*)[A-Za-z0-9._\-]{6,}', r'\1***', out)
    return out


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from a model reply."""
    text = str(text or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?', '', text, flags=re.I).strip()
        text = re.sub(r'```$', '', text).strip()
    match = re.search(r'\{.*\}', text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def has_mojibake(text: str) -> bool:
    """Heuristic check for UTF-8 text that was previously decoded as GBK."""
    value = str(text or '')
    return any(marker in value for marker in MOJIBAKE_MARKERS)


def read_utf8(path: str | Path) -> str:
    return Path(path).read_text(encoding='utf-8-sig')


def write_utf8(path: str | Path, text: str) -> None:
    Path(path).write_text(text, encoding='utf-8')
