# -*- coding: utf-8 -*-
"""Savefile-extraction style facts for MAICA CLI.

The original MAICA SFE layer turns MAS persistent values into short, reusable
facts. This lightweight version does the same for the CLI's own profile,
session state, custom facts, and stable Monika knowledge.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from persona import relationship_stage


MONIKA_FACTS_ZH: dict[int, list[str]] = {
    0: [
        "莫妮卡的生日是9月22日.",
    ],
    1: [
        '莫妮卡是[player]的女友.',
        '莫妮卡有一头珊瑚棕色的长发.',
        '莫妮卡有一个标志性的蝴蝶结发带.',
        '莫妮卡十分自信.',
        '莫妮卡擅长运动.',
        '莫妮卡是个成熟和努力的女孩.',
        '莫妮卡是一个非常体贴和善良的人, 总是为别人着想.',
        '莫妮卡常穿着黑色长袜和粉白相间的鞋子.',
        '莫妮卡最喜欢的颜色是翡翠绿, 这也是她眼睛的颜色.',
        '莫妮卡一年中最喜欢的日子是情人节.',
        '莫妮卡最喜欢的季节是夏天',
        '莫妮卡喜欢素食, 她认为这是对减少碳排放做出的贡献.',
        '莫妮卡是女高中生, 她成绩优秀, 性格温柔.',
        '莫妮卡对文学很有造诣, 喜欢读书, 喜欢写诗.',
        '莫妮卡爱好音乐, 会弹钢琴, 会唱歌.',
        '莫妮卡对科技, 环境, 历史等方面都有兴趣和见解.'
    ],
    2: [
        "莫妮卡对编程有兴趣, 也了解不少相关知识.",
        "莫妮卡喜欢哲学.",
        "莫妮卡喜欢猫.",
        "莫妮卡喜欢看星空.",
        "莫妮卡喜欢纸杯蛋糕和巧克力.",
        "莫妮卡经常喝咖啡.",
        "莫妮卡喜欢开放世界和沙盒类游戏, 但不太喜欢暴力游戏.",
        "莫妮卡喜欢音乐会.",
        "莫妮卡喜欢小裙子和蝴蝶结.",
        "莫妮卡善于关怀和安慰, 希望[player]能开心、振作.",
    ],
}


MONIKA_FACTS_EN: dict[int, list[str]] = {
    0: [
        "Monika's birthday is September 22nd.",
    ],
    1: [
        "Monika is [player]'s girlfriend.",
        "Monika has coral brown hair.",
        "Monika wears an iconic ribbon.",
        "Monika is pretty confident.",
        "Monika is good at sports.",
        "Monika behaves mature and hardworking.",
        "Monika is kind and considerate, she always cares about others.",
        "Monika wears black stockings and pink-white shoes.",
        "Monika's favorite color is emerald green, which is also the color of her eyes.",
        "Monika's favorite day of a year is Valentine's day.",
        "Monika's favorite season is summer.",
        "Monika is vegetarian, that's the way she contribute to environmental protection.",
        "Monika is a high school student. She has excellent grade and good temper.",
        "Monika is good at literature. She also likes reading and poetry.",
        "Monika likes music. She could sing and play piano.",
        "Monika is insighted and interested in technology, environment, history, etc."
    ],
    2: [
        "Monika is interested in programming and knows quite a bit about it.",
        "Monika likes philosophy.",
        "Monika likes cats.",
        "Monika likes watching the starry sky.",
        "Monika likes cupcakes and chocolate.",
        "Monika often drinks coffee.",
        "Monika likes open-world and sandbox games, but she does not really like violent games.",
        "Monika likes concerts.",
        "Monika likes skirts and ribbons.",
        "Monika is caring and comforting, and wants [player] to be happy and cheered up.",
    ],
}


PROFILE_FACT_KEYS_ZH: dict[str, str] = {
    "pronouns": "[player]希望被这样称呼: {value}.",
    "gender": "[player]的性别信息是{value}.",
    "favorite_color": "[player]喜欢的颜色是{value}.",
    "favorite_music": "[player]喜欢的音乐是{value}.",
    "favorite_food": "[player]喜欢的食物是{value}.",
    "likes_rain": "[player]对雨天的偏好是{value}.",
    "likes_horror": "[player]对恐怖作品的偏好是{value}.",
    "likes_poetry": "[player]对诗歌的偏好是{value}.",
    "personality": "[player]的性格倾向是{value}.",
    "appearance": "[player]告诉过莫妮卡自己的外貌: {value}.",
    "family_note": "[player]关于家庭的情况: {value}.",
    "health_note": "[player]关于身心状态的备注: {value}.",
    "study_work": "[player]关于学习或工作的情况: {value}.",
}


PROFILE_FACT_KEYS_EN: dict[str, str] = {
    "pronouns": "[player] prefers to be referred to as: {value}.",
    "gender": "[player]'s gender information is {value}.",
    "favorite_color": "[player]'s favorite color is {value}.",
    "favorite_music": "[player]'s favorite music is {value}.",
    "favorite_food": "[player]'s favorite food is {value}.",
    "likes_rain": "[player]'s preference about rain is {value}.",
    "likes_horror": "[player]'s preference about horror content is {value}.",
    "likes_poetry": "[player]'s preference about poetry is {value}.",
    "personality": "[player]'s personality tendency is {value}.",
    "appearance": "[player] has told Monika about their appearance: {value}.",
    "family_note": "[player]'s family-related note: {value}.",
    "health_note": "[player]'s mental or physical health note: {value}.",
    "study_work": "[player]'s study or work situation: {value}.",
}


def monika_sfe_facts(language: str = "zh", level: int = 1) -> list[str]:
    """Return stable Monika facts up to the requested detail level."""
    tables = MONIKA_FACTS_EN if language.lower().startswith("en") else MONIKA_FACTS_ZH
    level = max(0, min(2, int(level)))
    facts: list[str] = []
    for current in range(level + 1):
        facts.extend(tables.get(current, []))
    return facts


def _age_from_birthday(value: str, today: dt.date | None = None) -> int | None:
    today = today or dt.date.today()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            birthday = dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
        age = today.year - birthday.year
        if (today.month, today.day) < (birthday.month, birthday.day):
            age -= 1
        return max(0, age)
    return None


def _days_between(value: str, now: dt.datetime | None = None) -> int | None:
    if not value:
        return None
    try:
        start = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    now = now or dt.datetime.now()
    return max(0, (now.date() - start.date()).days)


def profile_sfe_facts(profile: dict[str, str], affection: float, language: str = "zh") -> list[str]:
    """Turn CLI profile/session values into MAS-SFE-like facts."""
    english = language.lower().startswith("en")
    player_name = profile.get("player_name") or "player"
    facts: list[str] = []

    if english:
        facts.append(f"[player]'s display name is {player_name}.")
        facts.append(f"Monika and [player] are in this relationship stage: {relationship_stage(affection)}.")
        facts.append(f"The current affection value is {affection:.2f}.")
    else:
        facts.append(f"[player]的名字是{player_name}.")
        facts.append(f"莫妮卡与[player]是{relationship_stage(affection)}.")
        facts.append(f"当前好感度是{affection:.2f}.")

    birthday = profile.get("birthday", "")
    if birthday:
        facts.append(f"[player]的生日是{birthday}." if not english else f"[player]'s birthday is {birthday}.")
        age = _age_from_birthday(birthday)
        if age is not None:
            facts.append(f"[player]今年{age}岁." if not english else f"[player] is {age} years old.")

    location = profile.get("location", "")
    if location:
        facts.append(f"[player]住在{location}." if not english else f"[player] lives in {location}.")

    try:
        nicknames = json.loads(profile.get("nicknames", "[]"))
    except json.JSONDecodeError:
        nicknames = []
    if isinstance(nicknames, list):
        cleaned_nicknames = [str(item).strip() for item in nicknames if str(item).strip()]
        if cleaned_nicknames:
            joined = "、".join(cleaned_nicknames)
            facts.append(
                f"莫妮卡可以用这些昵称称呼[player]: {joined}."
                if not english
                else f"Monika may call [player] by these nicknames: {', '.join(cleaned_nicknames)}."
            )

    session_count = profile.get("session_count", "")
    total_turns = profile.get("total_chat_turns", "")
    first_seen = profile.get("first_seen", "")
    last_seen = profile.get("last_seen", "")
    last_session_start = profile.get("last_session_start", "")
    if session_count:
        facts.append(
            f"[player]已经启动过这个 CLI 版 MAS {session_count}次."
            if not english
            else f"[player] has launched this CLI MAS {session_count} times."
        )
    if total_turns:
        facts.append(
            f"莫妮卡和[player]已经在 CLI 中聊过{total_turns}轮."
            if not english
            else f"Monika and [player] have chatted for {total_turns} turns in the CLI."
        )
    days = _days_between(first_seen)
    if days is not None:
        facts.append(
            f"莫妮卡和[player]在 CLI 中初次见面距今约{days}天."
            if not english
            else f"Monika and [player] first met in the CLI about {days} days ago."
        )
    if last_seen:
        facts.append(
            f"[player]上次离开 CLI 的时间是{last_seen}."
            if not english
            else f"[player] last left the CLI at {last_seen}."
        )
    if last_session_start:
        facts.append(
            f"[player]本次打开 CLI 的时间是{last_session_start}."
            if not english
            else f"[player] started this CLI session at {last_session_start}."
        )

    templates = PROFILE_FACT_KEYS_EN if english else PROFILE_FACT_KEYS_ZH
    for key, template in templates.items():
        value = profile.get(key, "").strip()
        if value:
            facts.append(template.format(value=value))

    return facts


def choose_relevant_facts(facts: list[str], query: str, limit: int = 12) -> list[str]:
    """Small lexical selector for SFE facts.

    This mirrors the original mfocus_find_info idea without adding another
    network call. If no query terms match, it returns the first stable facts.
    """
    limit = max(1, int(limit))
    query = (query or "").lower()
    tokens: list[str] = []
    for part in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", query):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            tokens.extend(part[index : index + 2] for index in range(max(0, len(part) - 1)))
        elif len(part) >= 2:
            tokens.append(part)
    if not tokens:
        return facts[:limit]

    scored: list[tuple[int, int, str]] = []
    for index, fact in enumerate(facts):
        haystack = fact.lower()
        score = sum(1 for token in tokens if token in haystack)
        if score:
            scored.append((score, -index, fact))
    if not scored:
        return facts[:limit]
    scored.sort(reverse=True)
    return [fact for _, _, fact in scored[:limit]]


def build_sfe_facts(
    store: Any,
    config: dict[str, Any],
    user_input: str = "",
) -> list[str]:
    """Build the full lightweight SFE fact list for one MFocus pass."""
    language = str(config.get("language") or "zh").lower()
    level = int(config.get("sfe_level", 1))
    fact_limit = int(config.get("sfe_fact_limit", 14))
    profile = store.get_profile()

    profile_facts = profile_sfe_facts(profile, store.affection(), language)
    essential = profile_facts[:3]
    candidates: list[str] = profile_facts[3:]

    custom_limit = max(1, fact_limit // 2)
    for row in store.search_facts(user_input, custom_limit):
        candidates.append(row["text"])
    candidates.extend(monika_sfe_facts(language, level))

    remaining = max(1, fact_limit - len(essential))
    return essential + choose_relevant_facts(candidates, user_input, remaining)
