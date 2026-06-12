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
from mfocus import special_events_for_today
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


def normalize_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    """Apply in-memory compatibility fixes without rewriting private config."""
    for key in ('mfocus_mode', 'mtrigger_mode'):
        if str(config.get(key, 'rule')).lower() == 'hybrid':
            config[key] = 'rule'
    if str(config.get('response_planner_mode', 'lite')).lower() not in {'lite', 'example_only'}:
        config['response_planner_mode'] = 'lite'
    if str(config.get('response_output_mode', 'dual')).lower() not in {'dual', 'json', 'legacy_marker'}:
        config['response_output_mode'] = 'dual'
    return config


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
        self.config = normalize_runtime_config(config if config is not None else load_json(self.config_path, DEFAULT_CONFIG))
        self.store = store if store is not None else Store(self.db_path)
        self.owns_store = store is None
        self.client = OpenAICompatibleClient(self.config)
        if self.owns_store and self.config.get("auto_backup_enabled", True):
            try:
                self.store.backup_database(keep=int(self.config.get("auto_backup_keep", 8)))
            except Exception:
                pass
        self._apply_startup_affection_adjustments()

    def close(self) -> None:
        if self.owns_store:
            self.store.close()

    def _apply_startup_affection_adjustments(self) -> None:
        profile = self.store.get_profile()
        now = dt.datetime.now()
        last_seen = profile.get("last_seen", "")
        if self.config.get("affection_absence_decay_enabled", True) and last_seen:
            try:
                seen = dt.datetime.fromisoformat(last_seen)
                away_days = max(0, (now.date() - seen.date()).days)
            except ValueError:
                away_days = 0
            grace = int(self.config.get("affection_absence_grace_days", 7))
            if away_days > grace:
                daily = float(self.config.get("affection_absence_daily_decay", 0.25))
                maximum = float(self.config.get("affection_absence_max_decay", 25.0))
                delta = -min(maximum, (away_days - grace) * daily)
                key = f"absence_decay:{now.date().isoformat()}"
                if self.store.get_profile_value(key, "") != "done":
                    self.store.set_affection(
                        self.store.affection() + delta,
                        float(self.config.get("affection_min", -100.0)),
                        float(self.config.get("affection_max", 10_000.0)),
                    )
                    self.store.add_event("affection_absence_decay", {"days": away_days, "delta": delta})
                    self.store.set_profile_value(key, "done")
        events = special_events_for_today(profile, self.config, now.date())
        if events:
            key = f"event_bonus:{now.date().isoformat()}"
            if self.store.get_profile_value(key, "") != "done":
                bonus = float(self.config.get("affection_event_bonus", 2.0))
                self.store.set_affection(
                    self.store.affection() + bonus,
                    float(self.config.get("affection_min", -100.0)),
                    float(self.config.get("affection_max", 10_000.0)),
                )
                self.store.add_event("affection_event_bonus", {"events": [event["name"] for event in events], "delta": bonus})
                self.store.set_profile_value(key, "done")

    def chat(self, user_input: str) -> dict[str, Any]:
        started = time.time()
        try:
            messages, mfocus_plan = build_messages(self.store, self.config, user_input, self.client)
            raw_reply, usage = self._call_chat(messages, "chat")
            parsed = parse_assistant_response(raw_reply)
            parsed = self._extract_metadata_if_needed(parsed)
            parsed = apply_response_meta_fallback(parsed, mfocus_plan)
            reply = apply_style_postprocess(parsed["text"], mfocus_plan, self.config)

            self.store.add_message("user", user_input)
            self.store.add_message("assistant", reply)
            self.store.increment_chat_turns()
            summary_notice = self._auto_summarize_if_needed()
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
            if summary_notice:
                mtrigger_notices.append(summary_notice)
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
                    "usage": usage,
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
                "usage": usage,
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
            raw_reply, usage = self._call_chat(messages, "spire")
            parsed = parse_assistant_response(raw_reply)
            parsed = self._extract_metadata_if_needed(parsed)
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
                    "usage": usage,
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
                "usage": usage,
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

    def _call_chat(self, messages: list[dict[str, str]], source: str) -> tuple[str, dict[str, Any]]:
        usage: dict[str, Any] = {}
        if self.config.get("streaming_enabled", False):
            try:
                chunks = list(self.client.chat_stream(messages))
                if chunks:
                    return "".join(chunks).strip(), usage
            except Exception:
                pass
        result = self.client.chat_with_usage(messages)
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        if self.config.get("token_stats_enabled", True):
            try:
                self.store.add_token_usage(
                    source,
                    str(result.get("model") or self.config.get("model") or ""),
                    int(usage.get("prompt_tokens") or 0),
                    int(usage.get("completion_tokens") or 0),
                    int(usage.get("total_tokens") or 0),
                    0.0,
                )
            except Exception:
                pass
        return str(result.get("content") or ""), usage

    def _extract_metadata_if_needed(self, parsed: dict[str, Any]) -> dict[str, Any]:
        if not self.config.get("metadata_extract_enabled", True):
            return parsed
        if parsed.get("emotion") not in {"", None, "neutral"}:
            return parsed
        text = str(parsed.get("text") or "").strip()
        if not text:
            return parsed
        prompt = (
            "Extract lightweight display metadata for this Monika reply. "
            "Return only JSON: {\"emotion\":\"smile|happy|gentle|shy|concerned|sad|surprised|thinking|playful|neutral\","
            "\"action\":{\"type\":\"none\"}}. Do not rewrite the reply."
        )
        try:
            raw = self.client.chat(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text},
                ],
                overrides={"temperature": 0.0, "max_tokens": 120},
            )
            meta = parse_assistant_response(raw)
            if meta.get("emotion") not in {"", None, "neutral"}:
                parsed["emotion"] = meta["emotion"]
            if meta.get("action"):
                parsed["action"] = meta["action"]
        except Exception:
            pass
        return parsed

    def _auto_summarize_if_needed(self) -> str:
        if not self.config.get("auto_memory_summary_enabled", False):
            return ""
        turns = self.store.int_profile_value("total_chat_turns")
        every = max(1, int(self.config.get("auto_memory_summary_turns", 24)))
        if turns <= 0 or turns % every:
            return ""
        summary = self.summarize_recent_memory()
        if summary.get("ok") and summary.get("summary_id"):
            return f"Memory summary saved #{summary['summary_id']}"
        return ""

    def summarize_recent_memory(self) -> dict[str, Any]:
        max_messages = int(self.config.get("auto_memory_summary_max_messages", 40))
        min_messages = int(self.config.get("auto_memory_summary_min_messages", 8))
        rows = self.store.recent_message_rows(max_messages)
        last_end = self.store.last_summary_source_end_id()
        rows = [row for row in rows if int(row["id"]) > last_end]
        if len(rows) < min_messages:
            return {"ok": False, "summary_id": 0, "error": "not enough new messages"}
        start_id = int(rows[0]["id"])
        end_id = int(rows[-1]["id"])
        transcript = "\n".join(f"{row['role']}: {row['content']}" for row in rows)
        prompt = (
            "Summarize this casual companion chat for future memory. "
            "Only keep stable facts, current plans, open threads, recurring preferences, and emotionally important context. "
            "Do not invent a plot. Do not overinterpret. Return 3-8 concise bullet points in English."
        )
        try:
            summary = self.client.chat(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": transcript},
                ],
                overrides={"temperature": 0.1, "max_tokens": 500},
            )
        except Exception as exc:
            return {"ok": False, "summary_id": 0, "error": str(exc)}
        summary = summary.strip()
        if not summary:
            return {"ok": False, "summary_id": 0, "error": "empty summary"}
        summary_id = self.store.add_summary(summary, "auto", start_id, end_id, 3)
        return {"ok": True, "summary_id": summary_id, "start_id": start_id, "end_id": end_id}
