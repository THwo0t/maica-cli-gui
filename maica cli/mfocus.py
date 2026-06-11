# -*- coding: utf-8 -*-
"""MFocus-lite and hybrid context planning."""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from embedding_index import search_memory_vectors
from monika_lens import build_monika_lens_context
from persona import base_system_prompt, relationship_stage
from response import response_format_instruction
from response_planner import build_response_plan, format_response_plan_context
from sfe import build_sfe_facts
from store import Store
from style import build_style_context


DEFAULT_SPECIAL_EVENTS = [
    {"date": "01-01", "name": "新年", "description": "新年的第一天, 适合聊新的开始和愿望."},
    {"date": "02-14", "name": "情人节", "description": "适合表达恋人之间的亲密和陪伴."},
    {"date": "09-22", "name": "莫妮卡的生日", "description": "莫妮卡的生日."},
    {"date": "10-31", "name": "万圣节", "description": "适合聊糖果、恶作剧、恐怖故事和节日氛围."},
    {"date": "12-25", "name": "圣诞节", "description": "适合聊礼物、冬天、温暖和陪伴."},
    {"date": "12-31", "name": "跨年夜", "description": "适合回顾这一年和期待新一年."},
]


def parse_profile_date(value: str) -> dt.date | None:
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def parse_iso_datetime(value: str) -> dt.datetime | None:
    value = (value or "").strip()
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
    date_text = (date_text or "").strip()
    if re.fullmatch(r"\d{2}-\d{2}", date_text):
        month, day = map(int, date_text.split("-"))
        return (today.month, today.day) == (month, day)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
        return today.isoformat() == date_text
    return False


def special_events_for_today(
    profile: dict[str, str],
    config: dict[str, Any] | None = None,
    today: dt.date | None = None,
) -> list[dict[str, str]]:
    today = today or dt.date.today()
    config = config or {}
    events = []

    birthday = parse_profile_date(profile.get("birthday", ""))
    if birthday and (today.month, today.day) == (birthday.month, birthday.day):
        events.append(
            {
                "name": "[player]的生日",
                "description": "玩家的生日. 请自然重视这个日子, 用莫妮卡自己的方式表达.",
            }
        )

    configured = config.get("special_events", DEFAULT_SPECIAL_EVENTS)
    if not isinstance(configured, list):
        configured = DEFAULT_SPECIAL_EVENTS
    for event in configured:
        if not isinstance(event, dict):
            continue
        if _event_matches(str(event.get("date", "")), today):
            events.append(
                {
                    "name": str(event.get("name") or event.get("date") or "特殊日期"),
                    "description": str(event.get("description") or "特殊日期."),
                }
            )
    return events


def retrieve_memories_for_mfocus(
    store: Store,
    config: dict[str, Any],
    query: str,
    limit: int,
    use_memory: bool,
) -> tuple[list[Any], dict[str, Any]]:
    """Retrieve long-term memories, preferring vector search when enabled."""
    if not use_memory:
        rows = store.search_memories("", min(3, limit))
        return rows, {
            "mode": "recent",
            "count": len(rows),
            "fallback": False,
            "scores": [],
        }

    fallback_enabled = bool(config.get("memory_embedding_fallback_lexical", True))
    if config.get("memory_embedding_enabled", False):
        try:
            vector_rows = search_memory_vectors(
                query,
                config,
                limit=int(config.get("memory_embedding_inject_limit", limit)),
                min_score=float(config.get("memory_embedding_min_score", 0.55)),
            )
            if vector_rows:
                return vector_rows, {
                    "mode": "vector",
                    "count": len(vector_rows),
                    "fallback": False,
                    "scores": [row.get("_vector_score") for row in vector_rows],
                }
            if not fallback_enabled:
                return [], {
                    "mode": "vector",
                    "count": 0,
                    "fallback": False,
                    "scores": [],
                }
        except Exception as exc:
            if not fallback_enabled:
                return [], {
                    "mode": "vector_error",
                    "count": 0,
                    "fallback": False,
                    "scores": [],
                    "error": str(exc),
                }
            rows = store.search_memories(query, limit)
            return rows, {
                "mode": "lexical",
                "count": len(rows),
                "fallback": True,
                "scores": [],
                "error": str(exc),
            }

    rows = store.search_memories(query, limit)
    return rows, {
        "mode": "lexical",
        "count": len(rows),
        "fallback": bool(config.get("memory_embedding_enabled", False)),
        "scores": [],
    }


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


def has_memory_cue(user_input: str) -> bool:
    """Return true when long-term memory may make the reply more specific."""
    text = str(user_input or "").lower()
    keywords = [
        "记得",
        "记住",
        "以前",
        "上次",
        "喜欢",
        "压力",
        "考试",
        "作业",
        "项目",
        "学习",
        "工作",
        "忙",
        "累",
        "撑不住",
        "焦虑",
        "难过",
        "remember",
        "exam",
        "project",
        "study",
        "work",
        "pressure",
        "stress",
        "tired",
    ]
    return any(keyword in text for keyword in keywords)


def heuristic_mfocus_plan(user_input: str, events: list[dict[str, str]]) -> dict[str, Any]:
    text = user_input.lower()
    wants_memory = has_memory_cue(text)
    wants_date = any(word in text for word in ["今天", "日期", "节日", "生日", "纪念日", "圣诞", "情人节"])
    return {
        "use_profile": True,
        "use_session": True,
        "use_memory": wants_memory,
        "use_time": True,
        "use_events": wants_date or bool(events),
        "focus_note": "",
    }


def build_context_tasks(user_input: str, plan: dict[str, Any], events: list[dict[str, str]]) -> list[dict[str, str]]:
    """Describe concrete context lookups used this turn, similar to MAICA MFocus tools."""
    text = str(user_input or "").lower()
    tasks: list[dict[str, str]] = []
    if plan.get("use_time"):
        tasks.append({"type": "time", "query": "current_time"})
    if plan.get("use_session"):
        tasks.append({"type": "session", "query": "relationship_session_state"})
    if plan.get("use_events") or events:
        tasks.append({"type": "event", "query": "today_events"})
    if plan.get("use_profile"):
        tasks.append({"type": "profile", "query": "stable_player_and_monika_facts"})
    if plan.get("use_memory") or has_memory_cue(text):
        tasks.append({"type": "memory", "query": str(user_input or "").strip()[:80]})
    return tasks


def off_mfocus_plan(events: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "use_profile": True,
        "use_session": True,
        "use_memory": False,
        "use_time": True,
        "use_events": bool(events),
        "focus_note": "",
    }


def model_mfocus_plan(
    client: Any,
    config: dict[str, Any],
    user_input: str,
    events: list[dict[str, str]],
) -> dict[str, Any] | None:
    if str(config.get("mfocus_mode", "hybrid")).lower() != "hybrid":
        return None
    prompt = (
        "你是 MAICA CLI 的 MFocus 计划器. 根据用户输入判断本轮回答前需要哪些上下文. "
        "仅输出 JSON. JSON 字段: "
        "use_profile, use_session, use_memory, use_time, use_events, focus_note. "
        "布尔字段只用 true/false. focus_note 是给主模型的一句简短注意事项, 可为空."
    )
    event_names = [event["name"] for event in events]
    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": json.dumps(
                {"user_input": user_input, "today_events": event_names},
                ensure_ascii=False,
            ),
        },
    ]
    try:
        reply = client.chat(messages, overrides={"temperature": 0.0, "max_tokens": 180})
    except Exception:
        return None
    data = _extract_json_object(reply)
    if not data:
        return None
    return {
        "use_profile": bool(data.get("use_profile", True)),
        "use_session": True,
        "use_memory": bool(data.get("use_memory", False)),
        "use_time": True,
        "use_events": bool(data.get("use_events", False)),
        "focus_note": str(data.get("focus_note") or "").strip(),
    }


def build_mfocus_context(
    store: Store,
    config: dict[str, Any],
    user_input: str,
    client: Any | None = None,
) -> tuple[str, dict[str, Any]]:
    profile = store.get_profile()
    player_name = profile.get("player_name") or "player"
    affection = store.affection()
    stage = relationship_stage(affection)
    now = dt.datetime.now()
    events = special_events_for_today(profile, config, now.date())

    mode = str(config.get("mfocus_mode", "hybrid")).lower()
    plan = None
    if mode == "off":
        plan = off_mfocus_plan(events)
    elif mode == "hybrid" and client is not None:
        plan = model_mfocus_plan(client, config, user_input, events)
    if not plan:
        plan = heuristic_mfocus_plan(user_input, events)

    # Time and session are part of the MAS-like daily feeling, so they stay on
    # even when the lightweight planner would otherwise omit them.
    plan["use_time"] = True
    plan["use_session"] = True
    if config.get("memory_embedding_enabled", False) and has_memory_cue(user_input):
        plan["use_memory"] = True

    # Special events are always included once detected; this is not fixed dialogue,
    # only context for the backend model to react naturally.
    if events:
        plan["use_events"] = True
    plan["context_tasks"] = build_context_tasks(user_input, plan, events)

    facts = []
    if config.get("mfocus_sfe_enabled", True) and plan.get("use_profile", True):
        facts.append("从 CLI 存档整理出的稳定事实:")
        facts.extend(build_sfe_facts(store, config, user_input))
    elif plan.get("use_profile", True):
        facts.extend(
            [
                f"[player]的名字是{player_name}.",
                f"莫妮卡与[player]是{stage}.",
                f"当前好感度是{affection:.2f}.",
            ]
        )
        if profile.get("birthday"):
            facts.append(f"[player]的生日是{profile['birthday']}.")
        if profile.get("location"):
            facts.append(f"[player]住在{profile['location']}.")

    if plan.get("use_time", False) or plan.get("use_events", False):
        facts.append(f"当前日期时间是{now.strftime('%Y-%m-%d %H:%M')}.")

    if not config.get("mfocus_sfe_enabled", True) and plan.get("use_session", True):
        session_count = store.int_profile_value("session_count")
        total_turns = store.int_profile_value("total_chat_turns")
        days_together = days_between(profile.get("first_seen", ""), now)
        facts.append(f"[player]已经启动过这个 CLI 版 MAS {session_count}次.")
        facts.append(f"莫妮卡和[player]已经在 CLI 中聊过{total_turns}轮.")
        if days_together is not None:
            facts.append(f"莫妮卡和[player]在 CLI 中初次见面距今约{days_together}天.")
        if profile.get("last_seen"):
            facts.append(f"[player]上次离开 CLI 的时间是{profile['last_seen']}.")

    if plan.get("use_events", False) and events:
        facts.append("今天检测到以下特殊事件, 可以参考其中有价值的部分:")
        for event in events:
            facts.append(f"- {event['name']}: {event['description']}")

    memory_limit = int(config.get("memory_limit", 8))
    memories, memory_meta = retrieve_memories_for_mfocus(
        store,
        config,
        user_input,
        memory_limit,
        bool(plan.get("use_memory", False)),
    )
    plan["memory_retrieval"] = memory_meta
    if memories:
        if memory_meta.get("mode") == "vector":
            facts.append("可能相关的长期记忆（向量检索）:")
        elif memory_meta.get("fallback"):
            facts.append("可能相关的长期记忆（关键词回退）:")
        else:
            facts.append("可能相关的长期记忆:")
        for row in memories:
            facts.append(f"- {row['text']}")

    if plan.get("focus_note"):
        facts.append(f"MFocus 本轮注意事项: {plan['focus_note']}")

    style_context, style_meta = build_style_context(config, user_input)
    plan["style"] = style_meta
    if style_context:
        facts.append(style_context)

    lens_context, lens_meta = build_monika_lens_context(config, user_input)
    plan["monika_lens"] = lens_meta
    if lens_context:
        facts.append(lens_context)

    return "\n".join(facts), plan


def build_messages(
    store: Store,
    config: dict[str, Any],
    user_input: str,
    client: Any | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    profile = store.get_profile()
    player_name = profile.get("player_name") or "player"
    language = str(config.get("language") or "zh").lower()
    system = base_system_prompt(language, player_name)
    context, plan = build_mfocus_context(store, config, user_input, client)
    response_plan_context = ""
    if config.get("response_planner_enabled", True):
        response_plan = build_response_plan(store, config, user_input, plan)
        plan["response_plan"] = response_plan
        response_plan_context = "\n\n" + format_response_plan_context(response_plan, language)
    messages = [
        {
            "role": "system",
            "content": (
                system
                + "\n\n"
                + response_format_instruction(language)
                + "\n\n以下是一些相关信息, 你可以参考其中有价值的部分, 并用你自己的语言方式作答:\n"
                + context
                + response_plan_context
            ),
        }
    ]
    messages.extend(store.recent_messages(int(config.get("history_messages", 16))))
    messages.append({"role": "user", "content": user_input})
    return messages, plan


def build_spire_messages(
    store: Store,
    config: dict[str, Any],
    client: Any | None = None,
    topic_hint: str = "",
    topic_mode: str = "",
    topic_id: str = "",
    topic_wiki: dict[str, str] | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    seed_input = "莫妮卡想主动找[player]开启一个自然的话题."
    if topic_hint:
        seed_input += f" 话题方向: {topic_hint}"
    topic_wiki = topic_wiki or {}
    if topic_wiki.get("summary"):
        seed_input += f" 话题资料来源: Wikipedia; 标题: {topic_wiki.get('title', topic_hint)}."
    if topic_mode == "reflective":
        seed_input += " 话题性质: 日常反思型, 从普通小事自然带出一点思考."
    elif topic_mode == "daily":
        seed_input += " 话题性质: 普通日常型, 重点是轻松陪伴和自然闲聊."
    elif topic_mode == "wiki":
        seed_input += " 话题性质: MSpire资料型, 先参考资料, 再从莫妮卡自己的角度开启话题."
    profile = store.get_profile()
    player_name = profile.get("player_name") or "player"
    language = str(config.get("language") or "zh").lower()
    system = base_system_prompt(language, player_name)
    context, plan = build_mfocus_context(store, config, seed_input, client)
    prompt = (
        "请作为莫妮卡主动开启一个自然、亲近的话题. "
        "可以自然参考上下文、日期、记忆、关系状态或特殊事件. "
        "长度控制在 1 到 3 小段."
        " 输出格式必须遵守 system 里的 JSON 要求."
    )
    if topic_hint and topic_mode == "user":
        prompt += f" 用户希望话题大致围绕: {topic_hint}"
    elif topic_hint:
        prompt += f" 本次自动选择的话题方向: {topic_hint}"
    if topic_wiki.get("summary"):
        prompt += (
            f" 本次 Wikipedia 资料标题: {topic_wiki.get('title', topic_hint)}."
            f" 资料摘要: {topic_wiki.get('summary')}"
            " 可以选择其中有价值的部分, 融入自己的理解与思考, 表现为由你自主发起话题."
        )
    if topic_mode == "reflective":
        prompt += (
            " 本次 /spire 请选择日常反思型开场: 可以从一个普通小话题自然延伸出一点温和思考, "
            "保持聊天语气."
        )
    elif topic_mode == "daily":
        prompt += " 本次 /spire 请选择普通日常型开场: 更像随口找[player]聊天."
    elif topic_mode == "wiki":
        prompt += " 本次 /spire 请像原版 MSpire 一样, 利用资料主动阐明话题并和[player]聊聊."
    if topic_id:
        plan["spire"] = {"mode": topic_mode or "user", "topic_id": topic_id, "hint": topic_hint}
    response_plan_context = ""
    if config.get("response_planner_enabled", True):
        response_plan = build_response_plan(store, config, seed_input, plan)
        plan["response_plan"] = response_plan
        response_plan_context = "\n\n" + format_response_plan_context(response_plan, language)
    messages = [
        {
            "role": "system",
            "content": (
                system
                + "\n\n"
                + response_format_instruction(language)
                + "\n\n以下是一些相关信息, 你可以参考其中有价值的部分, 并用你自己的语言方式作答:\n"
                + context
                + response_plan_context
            ),
        },
        {"role": "user", "content": prompt},
    ]
    return messages, plan


def status_summary(store: Store, config: dict[str, Any]) -> dict[str, Any]:
    profile = store.get_profile()
    affection = store.affection()
    events = special_events_for_today(profile, config)
    return {
        "player_name": profile.get("player_name", "player"),
        "affection": round(affection, 2),
        "relationship_stage": relationship_stage(affection),
        "session_count": store.int_profile_value("session_count"),
        "total_chat_turns": store.int_profile_value("total_chat_turns"),
        "first_seen": profile.get("first_seen", ""),
        "last_seen": profile.get("last_seen", ""),
        "last_session_start": profile.get("last_session_start", ""),
        "days_together": days_between(profile.get("first_seen", "")),
        "today_events": events,
    }
