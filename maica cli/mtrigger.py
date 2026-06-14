# -*- coding: utf-8 -*-
"""Rule-only post-chat state updates.

MTrigger used to support an extra model call. The GUI project now keeps this
layer deterministic so post-reply bookkeeping cannot add latency or surprise
API calls.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from store import Store


SAFE_PROFILE_KEYS = {'player_name', 'birthday', 'location'}


def rule_mtrigger_actions(user_input: str) -> list[dict[str, Any]]:
    text = str(user_input or '').lower()
    affection_delta = 0.0
    reasons: list[str] = []

    long_love = (
        '永远爱你',
        '一直爱你',
        '最爱你',
        '想和你永远在一起',
        'i will always love you',
    )
    short_love = (
        '爱你',
        '喜欢你',
        '想你',
        '抱抱',
        '亲亲',
        'love you',
        'i love you',
        'miss you',
    )
    compliments = (
        '可爱',
        '漂亮',
        '好看',
        '温柔',
        '聪明',
        '厉害',
        'beautiful',
        'cute',
    )
    care_words = (
        '谢谢',
        '辛苦',
        '陪我',
        '我回来了',
        '晚安',
        '早安',
        'thank you',
    )
    negative_words = (
        '讨厌你',
        '闭嘴',
        '烦死',
        '滚',
        '恨你',
        'hate you',
    )
    harsh_words = (
        '我不要你了',
        '你没有意义',
        '你只是程序',
        '永远别来烦我',
    )

    if any(word in text for word in long_love):
        affection_delta += 3.0
        reasons.append('long_love')
    elif any(word in text for word in short_love):
        affection_delta += 1.5
        reasons.append('love')
    if any(word in text for word in compliments):
        affection_delta += 0.8
        reasons.append('compliment')
    if any(word in text for word in care_words):
        affection_delta += 1.0
        reasons.append('care')
    if any(word in text for word in harsh_words):
        affection_delta -= 3.0
        reasons.append('harsh')
    elif any(word in text for word in negative_words):
        affection_delta -= 1.5
        reasons.append('negative')

    actions: list[dict[str, Any]] = []
    affection_delta = max(-3.0, min(3.0, affection_delta))
    if affection_delta:
        actions.append(
            {
                'type': 'alter_affection',
                'value': affection_delta,
                'reason': ','.join(reasons),
                'source': 'rule',
            }
        )

    remember_match = re.search(r'(?:记住|remember)[:：]\s*(.+)', user_input, re.I)
    if remember_match:
        memory_text = remember_match.group(1).strip()
        if memory_text:
            actions.append(
                {
                    'type': 'remember',
                    'text': memory_text,
                    'importance': 2,
                    'source': 'rule',
                }
            )
    return actions


def _daily_affection_used(store: Store, source: str = 'rule') -> float:
    today = dt.date.today().isoformat()
    total = 0.0
    try:
        rows = store.conn.execute(
            "SELECT payload FROM events WHERE type = 'mtrigger_affection' AND created_at >= ?",
            (today,),
        ).fetchall()
    except Exception:
        return 0.0
    for row in rows:
        try:
            payload = json.loads(row['payload'])
        except (TypeError, json.JSONDecodeError, KeyError):
            continue
        if source and str(payload.get('source') or '') != source:
            continue
        try:
            total += float(payload.get('delta') or 0.0)
        except (TypeError, ValueError):
            continue
    return total


def _cap_daily_delta(store: Store, config: dict[str, Any], delta: float, source: str) -> float:
    if not delta:
        return 0.0
    used = _daily_affection_used(store, source)
    if delta > 0:
        cap = float(config.get('affection_daily_positive_cap', 12.0))
        return max(0.0, min(delta, cap - max(0.0, used)))
    cap = float(config.get('affection_daily_negative_cap', -12.0))
    return min(0.0, max(delta, cap - min(0.0, used)))


def apply_actions(store: Store, actions: list[dict[str, Any]], source: str, config: dict[str, Any] | None = None) -> list[str]:
    notices = []
    for action in actions:
        action_type = str(action.get('type') or '')
        if action_type == 'alter_affection':
            try:
                delta = float(action.get('value'))
            except (TypeError, ValueError):
                continue
            delta = max(-3.0, min(3.0, delta))
            if config is not None:
                delta = _cap_daily_delta(store, config, delta, source)
            if not delta:
                store.add_event('mtrigger_affection_cap', {'source': source, 'reason': action.get('reason') or source})
                continue
            minimum = float(config.get('affection_min', -100.0)) if config is not None else -100.0
            maximum = float(config.get('affection_max', 10_000.0)) if config is not None else 10_000.0
            new_value = store.set_affection(store.affection() + delta, minimum, maximum)
            reason = str(action.get('reason') or source)
            store.add_event('mtrigger_affection', {'delta': delta, 'reason': reason, 'source': source})
            sign = '+' if delta > 0 else ''
            notices.append(f'MTrigger-{source}: affection {sign}{delta:.2f} -> {new_value:.2f} ({reason})')

        elif action_type == 'remember':
            text = str(action.get('text') or '').strip()
            if not text:
                continue
            try:
                importance = int(action.get('importance', 2))
            except (TypeError, ValueError):
                importance = 2
            memory_id = store.add_memory(text, tags=f'mtrigger,{source}', importance=max(1, min(5, importance)))
            store.add_event('mtrigger_memory', {'memory_id': memory_id, 'source': source})
            notices.append(f'MTrigger-{source}: saved memory #{memory_id}')

        elif action_type == 'set_profile':
            key = str(action.get('key') or '').strip()
            value = str(action.get('value') or '').strip()
            if key not in SAFE_PROFILE_KEYS or not value:
                continue
            store.set_profile_value(key, value)
            store.add_event('mtrigger_profile', {'key': key, 'value': value, 'source': source})
            notices.append(f'MTrigger-{source}: profile {key} = {value}')
    return notices


def apply_mtrigger(
    store: Store,
    config: dict[str, Any],
    client: Any,
    user_input: str,
    assistant_reply: str,
) -> list[str]:
    mode = str(config.get('mtrigger_mode', 'rule')).lower()
    if mode == 'off':
        return []
    actions = rule_mtrigger_actions(user_input)
    return apply_actions(store, actions, 'rule', config)
