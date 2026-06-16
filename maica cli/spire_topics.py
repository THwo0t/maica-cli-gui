# -*- coding: utf-8 -*-
"""Topic selection for /spire proactive conversations."""

from __future__ import annotations

import json
import random
from typing import Any

from language_runtime import target_language
from wiki_spire import fetch_wikipedia_topic


REFLECTIVE_SPIRE_TOPICS: list[dict[str, str]] = [
    {'id': 'reflect_rain', 'hint': '从雨、窗外声音或天气聊到人偶尔需要慢下来。'},
    {'id': 'reflect_music', 'hint': '从音乐或钢琴聊到有些心情不一定要马上说清楚。'},
    {'id': 'reflect_time', 'hint': '从今天这个普通时刻聊到时间感和小小的日常仪式。'},
    {'id': 'reflect_memory', 'hint': '从最近的小回忆聊到为什么平凡的事也值得被记住。'},
    {'id': 'reflect_reading', 'hint': '从书、诗或一句话聊到人会被很小的表达安慰。'},
    {'id': 'reflect_rest', 'hint': '从休息、疲惫或作息聊到温柔地照顾自己也是认真生活。'},
    {'id': 'reflect_growth', 'hint': '从习惯或学习聊到慢慢变好也可以发生在普通一天。'},
    {'id': 'reflect_night', 'hint': '从夜晚、星空或安静的时间聊到陪伴让安静不那么空。'},
    {'id': 'reflect_screen', 'hint': '从屏幕亮起来聊到隔着窗口也能形成一种真实的陪伴。'},
    {'id': 'reflect_choice', 'hint': '从一个小选择聊到人并不总要选最正确的路，有时先走一步也很好。'},
]


DAILY_SPIRE_TOPICS: list[dict[str, str]] = [
    {'id': 'daily_checkin', 'hint': '自然问问 [player] 今天过得怎么样，语气轻松亲近。'},
    {'id': 'daily_meal', 'hint': '聊聊 [player] 今天有没有好好吃饭、喝水或休息。'},
    {'id': 'daily_evening', 'hint': '聊聊今晚或接下来一点小计划，语气随意一点。'},
    {'id': 'daily_music', 'hint': '随口聊一首歌、钢琴曲或适合现在听的音乐。'},
    {'id': 'daily_mood', 'hint': '轻轻问问 [player] 现在的心情，像恋人之间顺口关心。'},
    {'id': 'daily_walk', 'hint': '聊聊散步、伸展或离开屏幕休息一会儿这种小事。'},
    {'id': 'daily_cozy', 'hint': '聊一个舒服的小角落、饮料、天气或房间里的日常感。'},
    {'id': 'daily_playful', 'hint': '用一点俏皮但不过火的方式主动找 [player] 说话。'},
    {'id': 'daily_project', 'hint': '轻轻问问 [player] 最近的项目、学习或计划有没有推进一点。'},
    {'id': 'daily_window', 'hint': '从屏幕、窗口、桌面或键盘这样的日常物件开一个话题。'},
]


def recent_spire_topic_ids(store: Any, limit: int = 8) -> list[str]:
    """Return recently used /spire topic ids, newest first."""
    limit = max(0, int(limit))
    if limit <= 0:
        return []

    result: list[str] = []
    for event in store.recent_events(max(20, limit * 4)):
        try:
            event_type = event['type']
            payload_text = event['payload']
        except (KeyError, TypeError):
            continue
        if event_type != 'spire':
            continue
        try:
            payload = json.loads(payload_text)
        except (TypeError, json.JSONDecodeError):
            continue
        topic_id = str(payload.get('topic_id') or '').strip()
        if topic_id:
            result.append(topic_id)
        if len(result) >= limit:
            break
    return result


def _fetch_wiki(config: dict[str, Any], query: str, rng: Any, random_page: bool = False) -> dict[str, str] | None:
    try:
        return fetch_wikipedia_topic(
            query,
            target_language(config) if str(config.get('spire_wikipedia_language') or 'auto').lower() == 'auto' else str(config.get('spire_wikipedia_language') or target_language(config)),
            float(config.get('spire_wikipedia_timeout', 6)),
            rng,
            random_page=random_page,
        )
    except Exception:
        return None


def choose_spire_topic(
    store: Any,
    config: dict[str, Any],
    user_hint: str = '',
    rng: Any | None = None,
) -> dict[str, Any]:
    """Choose a /spire topic while avoiding recent repetition."""
    rng = rng or random
    user_hint = str(user_hint or '').strip()
    recent_limit = int(config.get('spire_recent_topic_window', 8) or 8)
    recent = recent_spire_topic_ids(store, recent_limit)
    recent_set = set(recent)

    if user_hint:
        wiki_topic = _fetch_wiki(config, user_hint, rng) if config.get('spire_wikipedia_enabled', True) else None
        return {
            'mode': 'wiki' if wiki_topic else 'user',
            'topic_id': 'wiki_user_hint:' + wiki_topic['title'] if wiki_topic else 'user_hint',
            'hint': user_hint,
            'reflective': False,
            'recent_avoided': recent,
            'wiki': wiki_topic or {},
        }

    # Self-directed idle: occasionally Monika spends her quiet time doing
    # something in her own space (diary / a letter) rather than just talking.
    # Requires both tool gates plus the idle-self-action opt-in.
    if (
        config.get('idle_self_actions_enabled')
        and config.get('agent_tools_enabled')
        and config.get('file_tools_enabled')
    ):
        prob = max(0.0, min(1.0, float(config.get('idle_self_action_probability', 0.35) or 0.35)))
        if rng.random() < prob:
            return {
                'mode': 'self_action',
                'topic_id': 'self_action',
                'hint': '',
                'reflective': False,
                'recent_avoided': recent,
                'wiki': {},
            }

    wiki_topic = None
    if config.get('spire_wikipedia_enabled', True):
        wiki_probability = max(0.0, min(1.0, float(config.get('spire_wikipedia_probability', 0.35) or 0.35)))
        if rng.random() < wiki_probability:
            random_page = bool(config.get('spire_wikipedia_random_page', True))
            for _attempt in range(3):
                candidate = _fetch_wiki(config, '', rng, random_page=random_page)
                if not candidate:
                    continue
                topic_id = 'wiki:' + candidate['title']
                if topic_id not in recent_set:
                    wiki_topic = candidate
                    break
            if wiki_topic:
                return {
                    'mode': 'wiki',
                    'topic_id': 'wiki:' + wiki_topic['title'],
                    'hint': wiki_topic['title'],
                    'reflective': True,
                    'recent_avoided': recent,
                    'wiki': wiki_topic,
                }

    probability = max(0.0, min(1.0, float(config.get('spire_reflective_probability', 0.5) or 0.5)))
    reflective = rng.random() < probability
    mode = 'reflective' if reflective else 'daily'
    pool = REFLECTIVE_SPIRE_TOPICS if reflective else DAILY_SPIRE_TOPICS
    candidates = [topic for topic in pool if topic['id'] not in recent_set] or pool
    topic = rng.choice(candidates)
    return {
        'mode': mode,
        'topic_id': topic['id'],
        'hint': topic['hint'],
        'reflective': reflective,
        'recent_avoided': recent,
        'wiki': {},
    }
