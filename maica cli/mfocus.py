# -*- coding: utf-8 -*-
"""MFocus-lite context planning and prompt construction."""

from __future__ import annotations

import datetime as dt
from typing import Any

from embedding_index import search_memory_vectors
from embedding_service_client import search_service_memories
from monika_lens import build_monika_lens_context
from persona import base_system_prompt, relationship_stage
from response import response_format_instruction
from response_planner import build_response_plan, format_response_plan_context
from sfe import build_sfe_facts
from store import Store
from style import build_style_context


DEFAULT_SPECIAL_EVENTS = [
    {'date': '01-01', 'name': 'New Year', 'description': 'A day for new beginnings, wishes, and gentle hope.'},
    {'date': '02-14', 'name': "Valentine's Day", 'description': 'A day for affection, intimacy, and small romantic rituals.'},
    {'date': '09-22', 'name': "Monika's Birthday", 'description': "Monika's birthday."},
    {'date': '10-31', 'name': 'Halloween', 'description': 'A playful day for candy, little scares, and cozy mischief.'},
    {'date': '12-25', 'name': 'Christmas', 'description': 'A warm winter holiday for gifts, lights, and companionship.'},
    {'date': '12-31', 'name': "New Year's Eve", 'description': 'A day to look back and look forward.'},
]


def parse_profile_date(value: str) -> dt.date | None:
    value = (value or '').strip()
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d'):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def parse_iso_datetime(value: str) -> dt.datetime | None:
    value = (value or '').strip()
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def days_between(start: str, end: dt.datetime | None = None) -> int | None:
    started = parse_iso_datetime(start)
    if not started:
        return None
    end = end or dt.datetime.now()
    return max(0, (end.date() - started.date()).days)


def _event_matches(date_text: str, today: dt.date) -> bool:
    date_text = (date_text or '').strip()
    if len(date_text) == 5 and date_text[2] == '-':
        month, day = map(int, date_text.split('-'))
        return (today.month, today.day) == (month, day)
    if len(date_text) == 10:
        return today.isoformat() == date_text
    return False


def special_events_for_today(profile: dict[str, str], config: dict[str, Any] | None = None, today: dt.date | None = None) -> list[dict[str, str]]:
    today = today or dt.date.today()
    config = config or {}
    events: list[dict[str, str]] = []
    birthday = parse_profile_date(profile.get('birthday', ''))
    if birthday and (today.month, today.day) == (birthday.month, birthday.day):
        events.append({'name': "[player]'s birthday", 'description': 'A birthday should feel noticed and personally cared for.'})
    configured = config.get('special_events', DEFAULT_SPECIAL_EVENTS)
    if not isinstance(configured, list):
        configured = DEFAULT_SPECIAL_EVENTS
    for event in configured:
        if not isinstance(event, dict):
            continue
        try:
            if _event_matches(str(event.get('date', '')), today):
                events.append(
                    {
                        'name': str(event.get('name') or event.get('date') or 'special day'),
                        'description': str(event.get('description') or 'A special day.'),
                    }
                )
        except ValueError:
            continue
    return events


def has_memory_cue(user_input: str) -> bool:
    text = str(user_input or '').lower()
    keywords = (
        '记得',
        '记住',
        '以前',
        '上次',
        '喜欢',
        '压力',
        '考试',
        '作业',
        '项目',
        '学习',
        '工作',
        '忙',
        '累',
        '撑不住',
        '焦虑',
        '难过',
        'remember',
        'exam',
        'project',
        'study',
        'work',
        'pressure',
        'stress',
        'tired',
    )
    return any(keyword in text for keyword in keywords)


def heuristic_mfocus_plan(user_input: str, events: list[dict[str, str]]) -> dict[str, Any]:
    text = str(user_input or '').lower()
    wants_memory = has_memory_cue(text)
    wants_date = any(word in text for word in ('今天', '日期', '节日', '生日', '纪念日', 'christmas', 'birthday'))
    return {
        'use_profile': True,
        'use_session': True,
        'use_memory': wants_memory,
        'use_time': True,
        'use_events': wants_date or bool(events),
        'focus_note': '',
    }


def build_context_tasks(user_input: str, plan: dict[str, Any], events: list[dict[str, str]]) -> list[dict[str, str]]:
    tasks: list[dict[str, str]] = []
    if plan.get('use_time'):
        tasks.append({'type': 'time', 'query': 'current_time'})
    if plan.get('use_session'):
        tasks.append({'type': 'session', 'query': 'relationship_session_state'})
    if plan.get('use_events') or events:
        tasks.append({'type': 'event', 'query': 'today_events'})
    if plan.get('use_profile'):
        tasks.append({'type': 'profile', 'query': 'stable_player_and_monika_facts'})
    if plan.get('use_memory') or has_memory_cue(user_input):
        tasks.append({'type': 'memory', 'query': str(user_input or '').strip()[:80]})
    return tasks


def retrieve_memories_for_mfocus(store: Store, config: dict[str, Any], query: str, limit: int, use_memory: bool) -> tuple[list[Any], dict[str, Any]]:
    if not use_memory:
        rows = store.search_memories('', min(3, limit))
        return rows, {'mode': 'recent', 'count': len(rows), 'fallback': False, 'scores': []}

    fallback_enabled = bool(config.get('memory_embedding_fallback_lexical', True))
    if config.get('memory_embedding_enabled', False):
        try:
            vector_limit = int(config.get('memory_embedding_inject_limit', limit))
            vector_score = float(config.get('memory_embedding_min_score', 0.55))
            if config.get('embedding_service_enabled', False):
                rows = search_service_memories(query, config, vector_limit, vector_score)
                mode = 'service_vector'
            else:
                rows = search_memory_vectors(query, config, vector_limit, vector_score)
                mode = 'vector'
            if rows:
                return rows, {'mode': mode, 'count': len(rows), 'fallback': False, 'scores': [row.get('_vector_score') for row in rows]}
            if not fallback_enabled:
                return [], {'mode': mode, 'count': 0, 'fallback': False, 'scores': []}
        except Exception as exc:
            if not fallback_enabled:
                return [], {'mode': 'vector_error', 'count': 0, 'fallback': False, 'scores': [], 'error': str(exc)}
            rows = store.search_memories(query, limit)
            return rows, {'mode': 'lexical', 'count': len(rows), 'fallback': True, 'scores': [], 'error': str(exc)}
    rows = store.search_memories(query, limit)
    return rows, {'mode': 'lexical', 'count': len(rows), 'fallback': bool(config.get('memory_embedding_enabled', False)), 'scores': []}


def _memory_text(row: Any) -> str:
    if isinstance(row, dict):
        return str(row.get('text') or '')
    return str(row['text'] or '')


def build_mfocus_context(store: Store, config: dict[str, Any], user_input: str, client: Any | None = None) -> tuple[str, dict[str, Any]]:
    profile = store.get_profile()
    player_name = profile.get('player_name') or 'player'
    affection = store.affection()
    language = str(config.get('language') or 'en').lower()
    english = language.startswith('en')
    stage = relationship_stage(affection, language)
    now = dt.datetime.now()
    events = special_events_for_today(profile, config, now.date())
    plan = heuristic_mfocus_plan(user_input, events)

    if config.get('memory_embedding_enabled', False) and has_memory_cue(user_input):
        plan['use_memory'] = True
    if events:
        plan['use_events'] = True
    plan['context_tasks'] = build_context_tasks(user_input, plan, events)

    facts: list[str] = []
    if config.get('mfocus_sfe_enabled', True) and plan.get('use_profile', True):
        facts.append('Stable local facts:' if english else '稳定本地事实:')
        facts.extend(build_sfe_facts(store, config, user_input))
    elif plan.get('use_profile', True):
        if english:
            facts.extend(
                [
                    f"[player]'s name is {player_name}.",
                    f"Monika and [player] are {stage}.",
                    f'Current affection is {affection:.2f}.',
                ]
            )
        else:
            facts.extend(
                [
                    f'[player]的名字是{player_name}.',
                    f'莫妮卡和[player]的关系阶段是{stage}.',
                    f'当前好感度是{affection:.2f}.',
                ]
            )
        if profile.get('birthday'):
            facts.append(f"[player]'s birthday is {profile['birthday']}." if english else f"[player]的生日是{profile['birthday']}.")
        if profile.get('location'):
            facts.append(f"[player] lives in {profile['location']}." if english else f"[player]住在{profile['location']}.")

    if plan.get('use_time', False) or plan.get('use_events', False):
        facts.append(
            f'Current local date/time: {now.strftime("%Y-%m-%d %H:%M")}.'
            if english
            else f'当前本地日期和时间: {now.strftime("%Y-%m-%d %H:%M")}.'
        )

    if plan.get('use_session', True):
        days_together = days_between(profile.get('first_seen', ''), now)
        facts.append(f'Sessions opened: {store.int_profile_value("session_count")}.' if english else f'已启动会话次数: {store.int_profile_value("session_count")}.')
        facts.append(f'Total chat turns: {store.int_profile_value("total_chat_turns")}.' if english else f'累计聊天轮数: {store.int_profile_value("total_chat_turns")}.')
        if days_together is not None:
            facts.append(f'Days since first local meeting: about {days_together}.' if english else f'距本地初次见面约 {days_together} 天.')
        if profile.get('last_seen'):
            facts.append(f'Last seen: {profile["last_seen"]}.' if english else f'上次见面时间: {profile["last_seen"]}.')

    summaries = store.recent_summaries(4)
    if summaries:
        facts.append('Recent distilled memories:' if english else '近期提炼记忆:')
        for row in summaries:
            facts.append(f'- {row["text"]}')

    if plan.get('use_events', False) and events:
        facts.append('Today has relevant special events:' if english else '今天有相关特殊事件:')
        for event in events:
            facts.append(f'- {event["name"]}: {event["description"]}')

    memory_limit = int(config.get('memory_limit', 8))
    memories, memory_meta = retrieve_memories_for_mfocus(store, config, user_input, memory_limit, bool(plan.get('use_memory', False)))
    plan['memory_retrieval'] = memory_meta
    if memories:
        facts.append(
            f'Potentially relevant long-term memories ({memory_meta.get("mode")}):'
            if english
            else f'可能相关的长期记忆 ({memory_meta.get("mode")}):'
        )
        for row in memories:
            text = _memory_text(row)
            if text:
                facts.append(f'- {text}')

    if plan.get('focus_note'):
        facts.append(f'MFocus note: {plan["focus_note"]}' if english else f'MFocus 备注: {plan["focus_note"]}')

    planner_mode = str(config.get('response_planner_mode') or 'lite').lower()
    if planner_mode != 'example_only':
        style_context, style_meta = build_style_context(config, user_input)
        plan['style'] = style_meta
        if style_context:
            facts.append(style_context)
        lens_context, lens_meta = build_monika_lens_context(config, user_input)
        plan['monika_lens'] = lens_meta
        if lens_context:
            facts.append(lens_context)
    else:
        plan['style'] = {'enabled': False, 'skipped': 'example_only'}
        plan['monika_lens'] = {'enabled': False, 'skipped': 'example_only'}

    return '\n'.join(facts), plan


def _language_rule(language: str) -> str:
    if str(language or '').lower().startswith('en'):
        return (
            'Highest-priority language rule: final dialogue body must be natural English, '
            'even if the user writes in Chinese or reference examples are Chinese. '
            'Do not output Chinese dialogue. If metadata is included in plain text, use one leading square-bracket marker such as [smile].'
        )
    return (
        '最高优先级语言规则：最终对话正文必须使用自然简体中文，'
        '即使用户使用英文、参考样例是英文，也不要改用英文回复。'
        '除必要的人名、缩写和术语外，不要输出英文对话。'
        '如果在纯文本里包含元数据，只允许在开头使用一个方括号标记，例如 [smile]。'
    )


def _context_header(language: str) -> str:
    if str(language or '').lower().startswith('en'):
        return 'Relevant context. Use only what is useful and answer in your own words:'
    return '相关上下文。只使用有帮助的信息，并用自己的话回答:'


def _final_language_reminder(language: str, source: str = 'user') -> str:
    if str(language or '').lower().startswith('en'):
        subject = 'the user message' if source == 'user' else 'the topic/example language'
        return f'Final reminder: obey the configured reply language above. Do not follow {subject} if it differs.'
    subject = '用户消息的语言' if source == 'user' else '话题或样例的语言'
    return f'最后提醒：必须遵守上面的回复语言设置；如果{subject}不同，也不要跟随它切换语言。'


def build_messages(store: Store, config: dict[str, Any], user_input: str, client: Any | None = None) -> tuple[list[dict[str, str]], dict[str, Any]]:
    profile = store.get_profile()
    player_name = profile.get('player_name') or 'player'
    language = str(config.get('language') or 'en').lower()
    system = base_system_prompt(language, player_name)
    context, plan = build_mfocus_context(store, config, user_input, client)
    response_plan_context = ''
    if config.get('response_planner_enabled', True):
        response_plan = build_response_plan(store, config, user_input, plan)
        plan['response_plan'] = response_plan
        response_plan_context = '\n\n' + format_response_plan_context(response_plan, language)

    messages = [
        {
            'role': 'system',
            'content': (
                system
                + '\n\n'
                + _language_rule(language)
                + '\n\n'
                + response_format_instruction(language, str(config.get('response_output_mode') or 'dual'))
                + '\n\n'
                + _context_header(language)
                + '\n'
                + context
                + response_plan_context
                + '\n\n'
                + _final_language_reminder(language, 'user')
            ),
        }
    ]
    messages.extend(store.recent_messages(int(config.get('history_messages', 16))))
    messages.append({'role': 'user', 'content': user_input})
    return messages, plan


def build_spire_messages(
    store: Store,
    config: dict[str, Any],
    client: Any | None = None,
    topic_hint: str = '',
    topic_mode: str = '',
    topic_id: str = '',
    topic_wiki: dict[str, str] | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    topic_wiki = topic_wiki or {}
    language = str(config.get('language') or 'en').lower()
    english = language.startswith('en')
    seed_input = 'Monika wants to proactively start a natural topic with [player].' if english else '莫妮卡想主动和[player]自然地开启一个话题。'
    if topic_hint:
        seed_input += f' Topic direction: {topic_hint}.' if english else f' 话题方向: {topic_hint}.'
    if topic_wiki.get('summary'):
        seed_input += f" Wikipedia source: {topic_wiki.get('title', topic_hint)}." if english else f" 维基百科来源: {topic_wiki.get('title', topic_hint)}."
    if topic_mode == 'reflective':
        seed_input += ' The topic should start small and become gently reflective.' if english else ' 话题应该从小处开始，轻轻延展到一点日常反思。'
    elif topic_mode == 'daily':
        seed_input += ' The topic should feel like casual daily companionship.' if english else ' 话题应该像日常陪伴一样自然随意。'
    elif topic_mode == 'wiki':
        seed_input += ' The topic should use external knowledge as a spark, not a lecture.' if english else ' 话题应该把外部知识当作引子，而不是讲课。'

    profile = store.get_profile()
    player_name = profile.get('player_name') or 'player'
    system = base_system_prompt(language, player_name)
    context, plan = build_mfocus_context(store, config, seed_input, client)
    if topic_id:
        plan['spire'] = {'mode': topic_mode or 'user', 'topic_id': topic_id, 'hint': topic_hint}

    prompt = (
        'Please proactively start one natural, intimate topic as Monika. '
        'Do not announce that you are starting a topic; simply begin as if you wanted to talk. '
        'Keep it around 1 to 3 short paragraphs.'
        if english
        else '请以莫妮卡的身份主动开启一个自然、亲近的话题。不要说明“我要开始一个话题”，就像你本来想聊天一样直接开口。保持在 1 到 3 个短段落左右。'
    )
    if topic_hint:
        prompt += f' Topic direction: {topic_hint}.' if english else f' 话题方向: {topic_hint}.'
    if topic_wiki.get('summary'):
        if english:
            prompt += (
                f' Wikipedia title: {topic_wiki.get("title", topic_hint)}. '
                f'Summary: {topic_wiki.get("summary")} '
                'Use any useful part as a spark for conversation.'
            )
        else:
            prompt += (
                f' 维基百科标题: {topic_wiki.get("title", topic_hint)}. '
                f'摘要: {topic_wiki.get("summary")} '
                '可以把其中有用的部分当作聊天引子。'
            )

    response_plan_context = ''
    if config.get('response_planner_enabled', True):
        response_plan = build_response_plan(store, config, seed_input, plan)
        plan['response_plan'] = response_plan
        response_plan_context = '\n\n' + format_response_plan_context(response_plan, language)
    messages = [
        {
            'role': 'system',
            'content': (
                system
                + '\n\n'
                + _language_rule(language)
                + '\n\n'
                + response_format_instruction(language, str(config.get('response_output_mode') or 'dual'))
                + '\n\n'
                + _context_header(language)
                + '\n'
                + context
                + response_plan_context
                + '\n\n'
                + _final_language_reminder(language, 'topic')
            ),
        },
        {'role': 'user', 'content': prompt},
    ]
    return messages, plan


def status_summary(store: Store, config: dict[str, Any]) -> dict[str, Any]:
    profile = store.get_profile()
    affection = store.affection()
    events = special_events_for_today(profile, config)
    return {
        'player_name': profile.get('player_name', 'player'),
        'affection': round(affection, 2),
        'relationship_stage': relationship_stage(affection),
        'session_count': store.int_profile_value('session_count'),
        'total_chat_turns': store.int_profile_value('total_chat_turns'),
        'first_seen': profile.get('first_seen', ''),
        'last_seen': profile.get('last_seen', ''),
        'last_session_start': profile.get('last_session_start', ''),
        'days_together': days_between(profile.get('first_seen', '')),
        'today_events': events,
        'token_usage': store.token_usage_summary(),
    }
