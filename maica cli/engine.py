# -*- coding: utf-8 -*-
"""Reusable MAICA runtime engine for CLI and future GUI frontends."""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Any

from client import OpenAICompatibleClient
from config_defaults import DEFAULT_CONFIG
from config_io import load_json
from mfocus import build_messages, build_spire_messages
from mtrigger import apply_mtrigger
from response import limit_dialogue_sentences, parse_assistant_response
from spire_topics import choose_spire_topic
from store import Store


APP_DIR = Path(__file__).resolve().parent


def write_jsonl_log(config: dict[str, Any], payload: dict[str, Any], app_dir: Path = APP_DIR) -> None:
    if not config.get("jsonl_logs_enabled", True):
        return
    now = dt.datetime.now()
    log_root = Path(str(config.get("jsonl_logs_path") or "logs"))
    if not log_root.is_absolute():
        log_root = app_dir / log_root
    log_dir = log_root / now.strftime("%Y-%m")
    log_dir.mkdir(parents=True, exist_ok=True)
    payload = {"time": now.isoformat(timespec="seconds"), **payload}
    with (log_dir / f"{now.strftime('%Y-%m-%d')}.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def apply_style_postprocess(reply: str, mfocus_plan: dict[str, Any], config: dict[str, Any]) -> str:
    if not config.get("style_enabled", True):
        return reply
    style = mfocus_plan.get("style") if isinstance(mfocus_plan, dict) else None
    if not isinstance(style, dict) or not style.get("enabled", False):
        return reply
    response_plan = mfocus_plan.get("response_plan") if isinstance(mfocus_plan, dict) else None
    response_category = ""
    response_length = ""
    if isinstance(response_plan, dict):
        response_category = str(response_plan.get("category") or "")
        response_length = str(response_plan.get("length") or "")

    category = response_category or str(style.get("category") or "")
    if category not in {"greeting", "return", "farewell", "love", "hug", "daily", "playful"}:
        return reply
    if response_length == "long":
        return limit_dialogue_sentences(reply, 6)
    if response_length == "medium":
        return limit_dialogue_sentences(reply, 4)
    if response_length == "short":
        return limit_dialogue_sentences(reply, 3)
    return limit_dialogue_sentences(reply, max(3, int(style.get("max_sentences", 2))))


def apply_response_meta_fallback(parsed: dict[str, Any], mfocus_plan: dict[str, Any]) -> dict[str, Any]:
    """Use planner metadata when the model leaves emotion/action too generic."""
    if not isinstance(parsed, dict) or not isinstance(mfocus_plan, dict):
        return parsed
    response_plan = mfocus_plan.get("response_plan")
    if not isinstance(response_plan, dict):
        return parsed
    planned_emotion = str(response_plan.get("emotion") or "").strip()
    if planned_emotion and parsed.get("emotion") in {"", None, "neutral"}:
        parsed["emotion"] = planned_emotion
    return parsed


def compact_debug_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Keep debug readable without dumping full prompt example text."""
    if not isinstance(plan, dict):
        return {}
    compact = dict(plan)
    response_plan = compact.get("response_plan")
    if isinstance(response_plan, dict):
        response_plan = dict(response_plan)
        examples = response_plan.pop("examples", [])
        response_plan.pop("rhythm_examples", None)
        if isinstance(examples, list) and examples:
            response_plan["example_summaries"] = [
                {
                    "category": item.get("category"),
                    "intent": item.get("intent"),
                    "source": item.get("source"),
                    "score": item.get("score"),
                    "vector_similarity": item.get("vector_similarity"),
                    "retrieval_similarity": item.get("retrieval_similarity"),
                }
                for item in examples
                if isinstance(item, dict)
            ]
        compact["response_plan"] = response_plan
    return compact


class MaicaEngine:
    """Single runtime entrypoint shared by CLI and future GUI."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        store: Store | None = None,
        config_path: str | Path | None = None,
        db_path: str | Path | None = None,
        app_dir: str | Path | None = None,
    ) -> None:
        self.app_dir = Path(app_dir).resolve() if app_dir else APP_DIR
        self.config_path = Path(config_path).resolve() if config_path else self.app_dir / "config.json"
        self.db_path = Path(db_path).resolve() if db_path else self.app_dir / "maica_cli.db"
        self.config = config if config is not None else load_json(self.config_path, DEFAULT_CONFIG)
        self.store = store if store is not None else Store(self.db_path)
        self.owns_store = store is None
        self.client = OpenAICompatibleClient(self.config)

    def close(self) -> None:
        if self.owns_store:
            self.store.close()

    def chat(self, user_input: str) -> dict[str, Any]:
        started = time.time()
        try:
            messages, mfocus_plan = build_messages(self.store, self.config, user_input, self.client)
            raw_reply = self.client.chat(messages)
            parsed = parse_assistant_response(raw_reply)
            parsed = apply_response_meta_fallback(parsed, mfocus_plan)
            reply = apply_style_postprocess(parsed["text"], mfocus_plan, self.config)

            self.store.add_message("user", user_input)
            self.store.add_message("assistant", reply)
            self.store.increment_chat_turns()
            self.store.add_event(
                "assistant_meta",
                {
                    "source": "chat",
                    "emotion": parsed["emotion"],
                    "action": parsed["action"],
                    "raw": parsed["raw"],
                    "removed_markers": parsed["removed_markers"],
                },
            )
            mtrigger_notices = apply_mtrigger(self.store, self.config, self.client, user_input, reply)
            response_time = round(time.time() - started, 3)
            write_jsonl_log(
                self.config,
                {
                    "source": "chat",
                    "user": user_input,
                    "assistant_text": reply,
                    "emotion": parsed["emotion"],
                    "action": parsed["action"],
                    "mfocus_plan": mfocus_plan,
                    "mtrigger_notices": mtrigger_notices,
                    "raw_reply": parsed["raw"],
                    "response_time": response_time,
                },
                self.app_dir,
            )
            return {
                "ok": True,
                "source": "chat",
                "user": user_input,
                "text": reply,
                "emotion": parsed["emotion"],
                "action": parsed["action"],
                "removed_markers": parsed["removed_markers"],
                "mfocus_plan": mfocus_plan,
                "mtrigger_notices": mtrigger_notices,
                "raw_reply": parsed["raw"],
                "response_time": response_time,
                "debug": {"mfocus_plan": compact_debug_plan(mfocus_plan)},
                "error": "",
            }
        except Exception as exc:
            return {
                "ok": False,
                "source": "chat",
                "user": user_input,
                "text": "",
                "emotion": "neutral",
                "action": {},
                "removed_markers": [],
                "mfocus_plan": {},
                "mtrigger_notices": [],
                "raw_reply": "",
                "response_time": round(time.time() - started, 3),
                "debug": {},
                "error": str(exc),
            }

    def spire(self, hint: str = "") -> dict[str, Any]:
        started = time.time()
        try:
            spire_topic = choose_spire_topic(self.store, self.config, hint)
            messages, mfocus_plan = build_spire_messages(
                self.store,
                self.config,
                self.client,
                spire_topic["hint"],
                spire_topic["mode"],
                spire_topic["topic_id"],
                spire_topic.get("wiki", {}),
            )
            raw_reply = self.client.chat(messages)
            parsed = parse_assistant_response(raw_reply)
            parsed = apply_response_meta_fallback(parsed, mfocus_plan)
            reply = apply_style_postprocess(parsed["text"], mfocus_plan, self.config)

            self.store.add_message("assistant", reply)
            self.store.increment_chat_turns()
            self.store.add_event(
                "assistant_meta",
                {
                    "source": "spire",
                    "emotion": parsed["emotion"],
                    "action": parsed["action"],
                    "raw": parsed["raw"],
                    "removed_markers": parsed["removed_markers"],
                },
            )
            self.store.add_event(
                "spire",
                {
                    "hint": hint,
                    "selected_hint": spire_topic["hint"],
                    "mode": spire_topic["mode"],
                    "topic_id": spire_topic["topic_id"],
                    "reply": reply,
                },
            )
            response_time = round(time.time() - started, 3)
            write_jsonl_log(
                self.config,
                {
                    "source": "spire",
                    "user": hint,
                    "spire_topic": spire_topic,
                    "assistant_text": reply,
                    "emotion": parsed["emotion"],
                    "action": parsed["action"],
                    "mfocus_plan": mfocus_plan,
                    "raw_reply": parsed["raw"],
                },
                self.app_dir,
            )
            return {
                "ok": True,
                "source": "spire",
                "user": hint,
                "text": reply,
                "emotion": parsed["emotion"],
                "action": parsed["action"],
                "removed_markers": parsed["removed_markers"],
                "mfocus_plan": mfocus_plan,
                "mtrigger_notices": [],
                "raw_reply": parsed["raw"],
                "response_time": response_time,
                "debug": {
                    "spire_topic": spire_topic,
                    "mfocus_plan": compact_debug_plan(mfocus_plan),
                },
                "error": "",
            }
        except Exception as exc:
            return {
                "ok": False,
                "source": "spire",
                "user": hint,
                "text": "",
                "emotion": "neutral",
                "action": {},
                "removed_markers": [],
                "mfocus_plan": {},
                "mtrigger_notices": [],
                "raw_reply": "",
                "response_time": round(time.time() - started, 3),
                "debug": {},
                "error": str(exc),
            }
