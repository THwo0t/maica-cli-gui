# -*- coding: utf-8 -*-
"""Runtime language boundaries for prompts, memory, and examples."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from text_utils import cjk_ratio, contains_cjk


def target_language(config_or_language: dict[str, Any] | str | None = None) -> str:
    if isinstance(config_or_language, dict):
        raw = str(config_or_language.get('language') or 'en').lower()
    else:
        raw = str(config_or_language or 'en').lower()
    return 'zh' if raw.startswith('zh') else 'en'


def text_language(text: str) -> str:
    value = str(text or '').strip()
    if not value:
        return 'unknown'
    ratio = cjk_ratio(value)
    letters = sum(1 for char in value if char.isascii() and char.isalpha())
    if ratio >= 0.2:
        return 'mixed' if letters >= 12 else 'zh'
    if ratio >= 0.08:
        return 'mixed'
    if letters >= 4:
        return 'en'
    if contains_cjk(value):
        return 'mixed'
    return 'unknown'


def conforms_to_language(text: str, language: str, *, allow_mixed_terms: bool = True) -> bool:
    value = str(text or '').strip()
    if not value:
        return True
    language = target_language(language)
    ratio = cjk_ratio(value)
    letters = sum(1 for char in value if char.isascii() and char.isalpha())
    if language == 'en':
        # A few CJK names or quoted words should not make an English context unusable.
        return ratio < (0.2 if allow_mixed_terms else 0.08)
    # Chinese text may naturally contain technical acronyms, model names, and code words.
    return ratio >= 0.08 or (not letters and contains_cjk(value))


def language_mismatch(text: str, language: str, *, allow_mixed_terms: bool = True) -> bool:
    value = str(text or '').strip()
    return bool(value) and not conforms_to_language(value, language, allow_mixed_terms=allow_mixed_terms)


def source_hash(text: str) -> str:
    return hashlib.sha256(str(text or '').encode('utf-8')).hexdigest()


def language_rule(language: str) -> str:
    if target_language(language) == 'en':
        return (
            'Highest-priority language rule: final dialogue body must be natural English, '
            'even if the user writes in Chinese or reference context was translated from Chinese. '
            'Do not output Chinese dialogue. If metadata is included in plain text, use one leading square-bracket marker such as [smile].'
        )
    return (
        '最高优先级语言规则：最终对话正文必须使用自然简体中文，'
        '即使用户使用英文、参考上下文由英文翻译而来，也不要改用英文回复。'
        '除必要的人名、缩写和术语外，不要输出英文对话。'
        '如果在纯文本里包含元数据，只允许在开头使用一个方括号标记，例如 [smile]。'
    )


def final_language_reminder(language: str, source: str = 'user') -> str:
    if target_language(language) == 'en':
        subject = 'the user message' if source == 'user' else 'the topic/example language'
        return f'Final reminder: obey the configured reply language above. Do not follow {subject} if it differs.'
    subject = '用户消息的语言' if source == 'user' else '话题或样例的语言'
    return f'最后提醒：必须遵守上面的回复语言设置；如果{subject}不同，也不要跟随它切换语言。'


def terminal_language_directive(language: str) -> str:
    if target_language(language) == 'en':
        return (
            'Reply in English only. Your entire reply must be natural English, '
            'no matter what language was used earlier in this conversation or by the user just now.'
        )
    return (
        '只用简体中文回复。无论本次对话之前或用户刚才用的是什么语言，'
        '你的整段回复都必须是自然的简体中文（必要的人名、缩写、术语除外）。'
    )


def rewrite_prompt(language: str) -> str:
    target = 'natural English' if target_language(language) == 'en' else 'natural Simplified Chinese'
    return (
        f"Rewrite the dialogue body into {target}. "
        "Preserve the meaning, intimacy, sentence breaks, and Monika's voice. "
        "Do not add explanations. Do not add metadata, brackets, JSON, or stage directions. "
        "Return only the rewritten dialogue body."
    )


def translation_prompt(language: str) -> str:
    if target_language(language) == 'en':
        target = 'natural English'
    else:
        target = 'natural Simplified Chinese'
    schema = json.dumps({'items': [{'id': 'same id', 'text': 'translated text'}]}, ensure_ascii=False)
    return (
        f'Translate memory/context snippets into {target}. '
        'Preserve names, IDs, technical terms, dates, and relationship meaning. '
        'Do not summarize, embellish, or add new facts. '
        f'Return only JSON in this exact shape: {schema}'
    )
