# -*- coding: utf-8 -*-
"""MTrigger-lite and hybrid post-chat action handling."""

from __future__ import annotations

import json
import re
from typing import Any

from store import Store


SAFE_PROFILE_KEYS = {"player_name", "birthday", "location"}


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


def model_mtrigger_actions(
    client: Any,
    config: dict[str, Any],
    store: Store,
    user_input: str,
    assistant_reply: str,
) -> list[dict[str, Any]] | None:
    if str(config.get("mtrigger_mode", "hybrid")).lower() != "hybrid":
        return None

    profile = store.get_profile()
    prompt = (
        "你是 MAICA CLI 的 MTrigger 分析器. 根据用户输入和莫妮卡回复, 判断是否需要改变本地状态. "
        "仅输出 JSON. JSON 格式: "
        "{\"actions\":[{\"type\":\"alter_affection\",\"value\":1.0,\"reason\":\"...\"},"
        "{\"type\":\"remember\",\"text\":\"...\",\"importance\":2},"
        "{\"type\":\"set_profile\",\"key\":\"location\",\"value\":\"...\"}]}."
        "规则: affection 单次范围 -3 到 3; 只有用户明确表达时才 remember; "
        "set_profile 只允许 player_name, birthday, location; 如果无动作输出 {\"actions\":[]}."
    )
    payload = {
        "profile": {
            "player_name": profile.get("player_name"),
            "affection": store.affection(),
            "location": profile.get("location"),
            "birthday": profile.get("birthday"),
        },
        "user_input": user_input,
        "assistant_reply": assistant_reply,
    }
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        reply = client.chat(messages, overrides={"temperature": 0.0, "max_tokens": 260})
    except Exception:
        return None
    data = _extract_json_object(reply)
    if not data:
        return None
    actions = data.get("actions")
    if not isinstance(actions, list):
        return None
    return [action for action in actions if isinstance(action, dict)]


def rule_mtrigger_actions(user_input: str) -> list[dict[str, Any]]:
    text = user_input.lower()
    affection_delta = 0.0
    reasons = []

    long_love = ["永远爱你", "一直爱你", "最爱你", "想和你永远在一起", "我会一直陪着你"]
    short_love = ["爱你", "喜欢你", "想你", "抱抱"]
    compliments = ["可爱", "漂亮", "好看", "温柔", "聪明", "厉害"]
    care_words = ["谢谢", "辛苦", "陪我", "我回来了", "晚安", "早安"]
    negative_words = ["讨厌你", "闭嘴", "烦死", "滚", "恨你"]
    harsh_words = ["我不要你了", "你没有意义", "你只是程序", "永远别来烦我"]

    if any(word in text for word in long_love):
        affection_delta += 3.0
        reasons.append("long_love")
    elif any(word in text for word in short_love):
        affection_delta += 1.5
        reasons.append("love")
    if any(word in text for word in compliments):
        affection_delta += 0.8
        reasons.append("compliment")
    if any(word in text for word in care_words):
        affection_delta += 1.0
        reasons.append("care")
    if any(word in text for word in harsh_words):
        affection_delta -= 3.0
        reasons.append("harsh")
    if any(word in text for word in negative_words):
        affection_delta -= 1.5
        reasons.append("negative")

    actions = []
    affection_delta = max(-3.0, min(3.0, affection_delta))
    if affection_delta:
        actions.append(
            {
                "type": "alter_affection",
                "value": affection_delta,
                "reason": ",".join(reasons),
                "source": "rule",
            }
        )

    remember_match = re.search(r"(?:记住|remember)[:： ]+(.+)", user_input, re.I)
    if remember_match:
        memory_text = remember_match.group(1).strip()
        if memory_text:
            actions.append(
                {
                    "type": "remember",
                    "text": memory_text,
                    "importance": 2,
                    "source": "rule",
                }
            )
    return actions


def apply_actions(store: Store, actions: list[dict[str, Any]], source: str) -> list[str]:
    notices = []
    for action in actions:
        action_type = str(action.get("type") or "")
        if action_type == "alter_affection":
            try:
                delta = float(action.get("value"))
            except (TypeError, ValueError):
                continue
            delta = max(-3.0, min(3.0, delta))
            if not delta:
                continue
            new_value = store.set_affection(store.affection() + delta)
            reason = str(action.get("reason") or source)
            store.add_event("mtrigger_affection", {"delta": delta, "reason": reason, "source": source})
            sign = "+" if delta > 0 else ""
            notices.append(f"MTrigger-{source}: affection {sign}{delta:.2f} -> {new_value:.2f} ({reason})")

        elif action_type == "remember":
            text = str(action.get("text") or "").strip()
            if not text:
                continue
            try:
                importance = int(action.get("importance", 2))
            except (TypeError, ValueError):
                importance = 2
            memory_id = store.add_memory(text, tags=f"mtrigger,{source}", importance=max(1, min(5, importance)))
            store.add_event("mtrigger_memory", {"memory_id": memory_id, "source": source})
            notices.append(f"MTrigger-{source}: saved memory #{memory_id}")

        elif action_type == "set_profile":
            key = str(action.get("key") or "").strip()
            value = str(action.get("value") or "").strip()
            if key not in SAFE_PROFILE_KEYS or not value:
                continue
            store.set_profile_value(key, value)
            store.add_event("mtrigger_profile", {"key": key, "value": value, "source": source})
            notices.append(f"MTrigger-{source}: profile {key} = {value}")
    return notices


def apply_mtrigger(
    store: Store,
    config: dict[str, Any],
    client: Any,
    user_input: str,
    assistant_reply: str,
) -> list[str]:
    mode = str(config.get("mtrigger_mode", "hybrid")).lower()
    if mode == "off":
        return []

    actions = None
    if mode == "hybrid":
        actions = model_mtrigger_actions(client, config, store, user_input, assistant_reply)
    if actions is not None:
        notices = apply_actions(store, actions, "model")
        if notices or actions:
            return notices

    fallback = rule_mtrigger_actions(user_input)
    return apply_actions(store, fallback, "rule")
