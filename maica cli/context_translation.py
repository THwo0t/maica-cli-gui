# -*- coding: utf-8 -*-
"""Translate cross-language context snippets and cache the results."""

from __future__ import annotations

import json
from typing import Any

from language_runtime import conforms_to_language, target_language, translation_prompt


def _row_id(row: Any) -> int:
    try:
        return int(row['id'])
    except Exception:
        try:
            return int(row.get('id') or 0)
        except Exception:
            return 0


def _call_translation_model(
    store: Any,
    client: Any,
    config: dict[str, Any],
    language: str,
    missing: list[dict[str, Any]],
) -> dict[str, str]:
    if not client or not missing:
        return {}
    payload = {'items': [{'id': item['cache_key'], 'text': item['text']} for item in missing]}
    messages = [
        {'role': 'system', 'content': translation_prompt(language)},
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
    ]
    overrides = {
        'temperature': 0.0,
        'max_tokens': min(1200, max(300, int(config.get('max_tokens', 900)))),
    }
    if hasattr(client, 'chat_with_usage'):
        result = client.chat_with_usage(messages, overrides)
        usage = result.get('usage') if isinstance(result.get('usage'), dict) else {}
        try:
            store.add_token_usage(
                'context_translation',
                str(result.get('model') or config.get('model') or ''),
                int(usage.get('prompt_tokens') or 0),
                int(usage.get('completion_tokens') or 0),
                int(usage.get('total_tokens') or 0),
                0.0,
            )
        except Exception:
            pass
        raw = str(result.get('content') or '')
    else:
        raw = str(client.chat(messages, overrides))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find('{')
        end = raw.rfind('}')
        if start < 0 or end <= start:
            return {}
        try:
            data = json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            return {}
    rows = data.get('items') if isinstance(data, dict) else []
    if not isinstance(rows, list):
        return {}
    translated: dict[str, str] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        key = str(item.get('id') or '')
        text = str(item.get('text') or '').strip()
        if key and text:
            translated[key] = text
    return translated


def translate_context_items(
    store: Any,
    client: Any,
    config: dict[str, Any],
    items: list[dict[str, Any]],
    language: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return target-language context items with cached translations.

    Each item must include source_kind, source_id, and text. Existing target
    language text passes through; cross-language text is looked up in
    translation_cache or translated in one batch call.
    """
    language = target_language(language or config)
    if not items:
        return [], {'target_language': language, 'input_count': 0, 'cache_hits': 0, 'translated': 0, 'skipped': 0}

    output: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    cache_hits = 0
    skipped = 0
    for index, raw_item in enumerate(items):
        text = str(raw_item.get('text') or '').strip()
        if not text:
            continue
        item = dict(raw_item)
        source_kind = str(item.get('source_kind') or 'context')
        source_id = int(item.get('source_id') or _row_id(item) or 0)
        if conforms_to_language(text, language):
            item['translated'] = False
            item['cache_hit'] = False
            item['target_text'] = text
            output.append(item)
            continue
        cached = store.get_translation(source_kind, source_id, text, language)
        if cached and conforms_to_language(cached, language):
            item['translated'] = True
            item['cache_hit'] = True
            item['target_text'] = cached
            output.append(item)
            cache_hits += 1
            continue
        item['cache_key'] = f'{source_kind}:{source_id}:{index}'
        missing.append(item)

    translated_map: dict[str, str] = {}
    if missing and config.get('context_translation_enabled', True):
        translated_map = _call_translation_model(store, client, config, language, missing)

    translated_count = 0
    for item in missing:
        translated = translated_map.get(str(item.get('cache_key') or ''), '').strip()
        if translated and conforms_to_language(translated, language):
            source_kind = str(item.get('source_kind') or 'context')
            source_id = int(item.get('source_id') or 0)
            original = str(item.get('text') or '')
            store.set_translation(source_kind, source_id, original, language, translated)
            item['translated'] = True
            item['cache_hit'] = False
            item['target_text'] = translated
            output.append(item)
            translated_count += 1
        else:
            skipped += 1

    return output, {
        'target_language': language,
        'input_count': len(items),
        'cache_hits': cache_hits,
        'translated': translated_count,
        'skipped': skipped,
    }
