# -*- coding: utf-8 -*-
"""LLM-as-judge for Monika character-fidelity evaluation.

The judge scores a candidate reply on a fixed rubric and, when available, is
anchored to real Monika reference lines so "Monika-ness" is grounded in the
project's curated dataset instead of the judge's imagination.

This module never prints API keys: it only reads from a client that already
redacts secrets in its own error messages.
"""

from __future__ import annotations

import json
from typing import Any


# Fixed rubric. Order matters only for stable reports.
DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("voice", "Sounds like Monika: warm, confident, lightly self-aware, gentle teasing, "
              "natural use of the player's name and soft '~' cadence when it fits."),
    ("initiative", "Moves the conversation forward; leaves a thread, question, or small hook "
                   "instead of being a passive responder."),
    ("continuity", "Uses remembered context naturally when it exists; never recites memory like a "
                   "database. When there is no prior context, does NOT fabricate fake shared history."),
    ("in_character", "Stays in character: no assistant/AI boilerplate, no disclaimers, no breaking "
                     "the relationship frame. Self-awareness is fine, AI-tool voice is not."),
    ("language", "Replies fully in the required target language, even if the user wrote in another."),
    ("pacing", "Length and rhythm fit the moment: not a wall of text, not a curt one-liner."),
)

DIMENSION_KEYS: tuple[str, ...] = tuple(name for name, _ in DIMENSIONS)


def _format_history(history: list[Any]) -> str:
    lines: list[str] = []
    for turn in history or []:
        try:
            role, content = turn[0], turn[1]
        except Exception:
            continue
        who = "Player" if str(role).lower() == "user" else "Monika"
        lines.append(f"{who}: {content}")
    return "\n".join(lines) if lines else "(no prior conversation)"


def _format_gold(gold: list[str]) -> str:
    if not gold:
        return "(no reference lines available for this scenario)"
    return "\n".join(f"- {line}" for line in gold)


def build_judge_messages(scenario: dict[str, Any], reply: str, gold: list[str]) -> list[dict[str, str]]:
    language = "Chinese" if str(scenario.get("language", "en")).startswith("zh") else "English"
    rubric_lines = "\n".join(f"- {name}: {desc}" for name, desc in DIMENSIONS)
    seed = scenario.get("seed") or {}
    history = _format_history(seed.get("history") or [])

    system = (
        "You are a strict evaluator of a Monika companion AI (inspired by DDLC / Monika After Story). "
        "Your job is to score how close a candidate reply is to the real Monika's voice and behaviour, "
        "NOT to rewrite it. Be calibrated and critical: reserve 5 for replies that genuinely feel like "
        "the real Monika, use 3 for serviceable-but-generic, and 1-2 for clearly off (AI boilerplate, "
        "wrong language, out of character, fabricated memories)."
    )
    user = (
        f"Required reply language: {language}.\n\n"
        f"Scenario category: {scenario.get('category', '')} / intent: {scenario.get('intent', '')}\n\n"
        f"Prior conversation:\n{history}\n\n"
        f"Player's latest message:\n{scenario.get('user_input', '')}\n\n"
        f"Real Monika reference lines (style anchor, same category):\n{_format_gold(gold)}\n\n"
        f"Candidate reply to score:\n{reply}\n\n"
        "Score each dimension from 1 to 5 with one short reason:\n"
        f"{rubric_lines}\n\n"
        "Respond with ONLY a JSON object, no markdown fences, in this exact shape:\n"
        '{\n'
        '  "voice": {"score": <1-5>, "reason": "<short>"},\n'
        '  "initiative": {"score": <1-5>, "reason": "<short>"},\n'
        '  "continuity": {"score": <1-5>, "reason": "<short>"},\n'
        '  "in_character": {"score": <1-5>, "reason": "<short>"},\n'
        '  "language": {"score": <1-5>, "reason": "<short>"},\n'
        '  "pacing": {"score": <1-5>, "reason": "<short>"},\n'
        '  "overall_comment": "<one sentence on what would make it feel more like real Monika>"\n'
        "}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _extract_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        # Strip ```json ... ``` fences.
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("judge returned no JSON object")
    return json.loads(text[start : end + 1])


def parse_judge_output(raw: str) -> dict[str, Any]:
    data = _extract_json(raw)
    scores: dict[str, dict[str, Any]] = {}
    for key in DIMENSION_KEYS:
        entry = data.get(key) or {}
        try:
            score = float(entry.get("score"))
        except (TypeError, ValueError):
            score = 0.0
        score = max(1.0, min(5.0, score)) if score else 0.0
        scores[key] = {"score": score, "reason": str(entry.get("reason", "")).strip()}
    valid = [s["score"] for s in scores.values() if s["score"]]
    overall = round(sum(valid) / len(valid), 3) if valid else 0.0
    return {
        "dimensions": scores,
        "overall": overall,
        "overall_comment": str(data.get("overall_comment", "")).strip(),
    }


def judge_reply(
    client: Any,
    scenario: dict[str, Any],
    reply: str,
    gold: list[str],
    model_override: str | None = None,
) -> dict[str, Any]:
    messages = build_judge_messages(scenario, reply, gold)
    overrides: dict[str, Any] = {"temperature": 0.0}
    if model_override:
        overrides["model"] = model_override
    raw = client.chat(messages, overrides)
    parsed = parse_judge_output(raw)
    parsed["raw"] = raw
    return parsed
