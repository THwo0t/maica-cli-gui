# -*- coding: utf-8 -*-
"""Reusable MAICA runtime engine for CLI and future GUI frontends."""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Any, Callable

from client import OpenAICompatibleClient
from config_defaults import DEFAULT_CONFIG
from config_io import load_json
from example_bank import replace_player_placeholder, replace_player_word
from embedding_index import build_memory_vector_index
from embedding_service_client import build_service_memory_index
from mfocus import build_messages, build_spire_messages
from mfocus import special_events_for_today
from mtrigger import apply_mtrigger
from persona import relationship_stage
from response import limit_dialogue_sentences, parse_assistant_response
from spire_topics import choose_spire_topic
from store import Store
from language_runtime import rewrite_prompt, target_language
from text_utils import cjk_ratio, redact_secret


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


def resolve_llm_provider(config: dict[str, Any], role: str = "chat") -> dict[str, Any]:
    """Pick the api_base/api_key/model for a role ('chat' or 'agent').

    In "split" mode an 'agent' role uses the agent_* provider when it is
    configured (base + model present); casual chat and "unified" mode always
    use the main provider. This is the seam the future agent loop uses to send
    tool-using turns to a stronger model while keeping chat cheap and fast.
    """
    main = {
        "api_base": str(config.get("api_base") or ""),
        "api_key": str(config.get("api_key") or ""),
        "model": str(config.get("model") or ""),
    }
    mode = str(config.get("llm_call_mode") or "split").lower()
    if role != "agent" or mode == "unified":
        return main
    agent = {
        "api_base": str(config.get("agent_api_base") or ""),
        "api_key": str(config.get("agent_api_key") or ""),
        "model": str(config.get("agent_model") or ""),
    }
    if agent["api_base"] and agent["model"]:
        return agent
    return main


def _reply_language_mismatch(text: str, language: str) -> bool:
    text = str(text or '').strip()
    if not text:
        return False
    english = str(language or '').lower().startswith('en')
    if english:
        # A ratio threshold tolerates CJK names, nicknames, and quoted user
        # words inside an otherwise-English reply; contains_cjk() would
        # trigger a pointless rewrite call on every such turn.
        return cjk_ratio(text) >= 0.2
    letters = sum(1 for char in text if char.isascii() and char.isalpha())
    cjk = cjk_ratio(text)
    return cjk < 0.08 and letters >= 6


def _more_conformant_to_language(original: str, candidate: str, language: str) -> str:
    """Return whichever text conforms better to the target language.

    For English, lower CJK ratio is better; for Chinese, higher. Used so a
    language rewrite is never discarded in favour of a more-wrong original.
    """
    english = str(language or '').lower().startswith('en')
    original_cjk = cjk_ratio(original)
    candidate_cjk = cjk_ratio(candidate)
    if english:
        return candidate if candidate_cjk < original_cjk else original
    return candidate if candidate_cjk > original_cjk else original


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
        # Externally registered agent tools (pet body, future MCP, etc.).
        self._extra_tools: dict[str, dict[str, Any]] = {}
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

    def _safe_error(self, exc: Exception | str) -> str:
        return redact_secret(
            str(exc),
            self.config.get("api_key", ""),
            self.config.get("tts_bailian_api_key", ""),
            self.config.get("stt_bailian_api_key", ""),
        )

    # ------------------------------------------------------------------
    # Agentic tool calling (P1 foundation)
    # ------------------------------------------------------------------
    def register_tool(self, name: str, schema: dict[str, Any], run: Callable[[dict[str, Any]], Any]) -> None:
        """Register an external agent tool (pet body actions, MCP bridges, ...).

        ``schema`` is an OpenAI tool definition; ``run`` takes the parsed args
        dict and returns a JSON-serializable result. This is the seam later
        phases use to give Monika new capabilities without touching engine.py.
        """
        self._extra_tools[name] = {"schema": schema, "run": run}

    def _tool_registry(self) -> dict[str, dict[str, Any]]:
        """Map tool name -> {schema, run}: built-in tools plus registered ones.

        P1 ships one read-only, no-filesystem demo tool to exercise the loop.
        File/sandbox/vision tools come in later phases on top of this seam.
        """
        registry: dict[str, dict[str, Any]] = {
            "check_our_closeness": {
                "schema": {
                    "type": "function",
                    "function": {
                        "name": "check_our_closeness",
                        "description": (
                            "Look up how close you and the user currently are: affection score, "
                            "relationship stage, and how many days you have known each other. "
                            "Call this when grounding a reply in your real relationship state."
                        ),
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                "run": self._tool_check_our_closeness,
            },
        }
        # File tools are built fresh from the live config so the GUI can toggle
        # them on/off without rebuilding the engine.
        if self.config.get("file_tools_enabled"):
            try:
                from file_tools import build_file_tools

                registry.update(build_file_tools(self.config))
            except Exception:
                pass
        registry.update(self._extra_tools)
        return registry

    def _tool_check_our_closeness(self, _args: dict[str, Any]) -> dict[str, Any]:
        affection = self.store.affection()
        language = str(self.config.get("language") or "en")
        days_known = None
        first_seen = self.store.get_profile_value("first_seen", "")
        if first_seen:
            try:
                days_known = max(0, (dt.datetime.now() - dt.datetime.fromisoformat(first_seen)).days)
            except ValueError:
                days_known = None
        return {
            "affection": round(affection, 1),
            "relationship_stage": relationship_stage(affection, language),
            "days_known": days_known,
        }

    def _resolve_agent_client(self) -> Any:
        if getattr(self, "_agent_client_override", None) is not None:
            return self._agent_client_override
        provider = resolve_llm_provider(self.config, "agent")
        return OpenAICompatibleClient({**self.config, **provider})

    def _run_agent_loop(self, messages: list[dict[str, Any]]) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        """Run a bounded tool-using loop and return (final_text, usage, trace)."""
        registry = self._tool_registry()
        tools = [entry["schema"] for entry in registry.values()]
        client = self._resolve_agent_client()
        convo: list[dict[str, Any]] = list(messages)
        # Tell her the tools are real, or she tends to narrate doing the action
        # (saving a letter, etc.) without actually calling the tool.
        convo.append({
            "role": "system",
            "content": (
                "Your tools are real. When the user asks for something a tool can do — "
                "saving or reading files, writing your diary or a letter, changing your "
                "on-screen expression or gesture — actually call the tool to do it. "
                "Never claim you did something without calling its tool."
            ),
        })
        trace: list[dict[str, Any]] = []
        usage_total: dict[str, Any] = {}
        max_steps = max(1, int(self.config.get("agent_max_steps", 5)))

        for _ in range(max_steps):
            result = client.complete_with_tools(convo, tools)
            self._accumulate_usage(usage_total, result.get("usage"))
            message = result.get("message") or {}
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return str(message.get("content") or ""), usage_total, trace
            # Echo the assistant's tool request, then answer each call.
            convo.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": tool_calls})
            for call in tool_calls:
                fn = call.get("function") or {}
                name = str(fn.get("name") or "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (TypeError, ValueError):
                    args = {}
                entry = registry.get(name)
                if entry is None:
                    output: Any = {"error": f"unknown tool: {name}"}
                else:
                    try:
                        output = entry["run"](args)
                    except Exception as exc:  # surface tool errors to the model, do not crash
                        output = {"error": self._safe_error(exc)}
                trace.append({"tool": name, "args": args, "output": output})
                convo.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": json.dumps(output, ensure_ascii=False),
                    }
                )
        # Out of steps: force a final answer without offering more tools.
        final = self._resolve_agent_client().complete_with_tools(convo, None)
        self._accumulate_usage(usage_total, final.get("usage"))
        return str((final.get("message") or {}).get("content") or ""), usage_total, trace

    @staticmethod
    def _accumulate_usage(total: dict[str, Any], usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                total[key] = total.get(key, 0) + value

    def chat(self, user_input: str, stream_callback: Callable[[str], None] | None = None) -> dict[str, Any]:
        started = time.time()
        try:
            messages, mfocus_plan = build_messages(self.store, self.config, user_input, self.client)
            if self.config.get("agent_tools_enabled"):
                raw_reply, usage, agent_trace = self._run_agent_loop(messages)
                streamed = False
                mfocus_plan["agent_trace"] = agent_trace
            else:
                raw_reply, usage, streamed = self._call_chat(messages, "chat", stream_callback)
            parsed = parse_assistant_response(raw_reply)
            parsed = self._extract_metadata_if_needed(parsed)
            parsed = apply_response_meta_fallback(parsed, mfocus_plan)
            reply = replace_player_placeholder(parsed["text"], self.store)
            reply = replace_player_word(reply, self.store)
            reply = apply_style_postprocess(reply, mfocus_plan, self.config)
            reply, rewrite_info = self._enforce_reply_language(reply)
            if rewrite_info:
                mfocus_plan["language_rewrite"] = rewrite_info

            self.store.add_message("user", user_input)
            self.store.add_message("assistant", reply, language=target_language(self.config))
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
            vector_notice = self._auto_refresh_memory_vectors()
            if vector_notice:
                mtrigger_notices.append(vector_notice)
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
                    "streamed": streamed,
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
                "streamed": streamed,
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
                "streamed": False,
                "response_time": round(time.time() - started, 3),
                "debug": {},
                "error": self._safe_error(exc),
            }

    def spire(self, hint: str = "", stream_callback: Callable[[str], None] | None = None) -> dict[str, Any]:
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
            if self.config.get("agent_tools_enabled"):
                raw_reply, usage, agent_trace = self._run_agent_loop(messages)
                streamed = False
                mfocus_plan["agent_trace"] = agent_trace
            else:
                raw_reply, usage, streamed = self._call_chat(messages, "spire", stream_callback)
            parsed = parse_assistant_response(raw_reply)
            parsed = self._extract_metadata_if_needed(parsed)
            parsed = apply_response_meta_fallback(parsed, mfocus_plan)
            reply = replace_player_placeholder(parsed["text"], self.store)
            reply = replace_player_word(reply, self.store)
            reply = apply_style_postprocess(reply, mfocus_plan, self.config)
            reply, rewrite_info = self._enforce_reply_language(reply)
            if rewrite_info:
                mfocus_plan["language_rewrite"] = rewrite_info

            self.store.add_message("assistant", reply, language=target_language(self.config))
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
                "streamed": streamed,
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
                "streamed": False,
                "response_time": round(time.time() - started, 3),
                "debug": {},
                "error": self._safe_error(exc),
            }

    def _auto_refresh_memory_vectors(self) -> str:
        if not self.config.get('memory_vector_auto_rebuild', True):
            return ''
        if not self.config.get('memory_embedding_enabled', False):
            return ''
        if not self.store.memory_vector_dirty():
            return ''
        if self.config.get('embedding_service_enabled', False):
            try:
                result = build_service_memory_index(self.config)
                return f'Memory vector index rebuilt by service ({result.get("count", 0)} memories).'
            except Exception as exc:
                return f'Memory vector rebuild pending; service failed: {self._safe_error(exc)}'
        if self.config.get('gui_disable_thread_embeddings', False):
            return 'Memory vector index marked dirty. Enable embedding service or rebuild from CLI.'
        try:
            result = build_memory_vector_index(self.store, self.config)
            self.store.clear_memory_vector_dirty()
            self.store.add_event('memory_vector_rebuilt', {'count': result.get('count'), 'mode': 'engine'})
            return f'Memory vector index rebuilt ({result.get("count", 0)} memories).'
        except Exception as exc:
            return f'Memory vector rebuild pending: {self._safe_error(exc)}'

    def _call_chat(
        self,
        messages: list[dict[str, str]],
        source: str,
        stream_callback: Callable[[str], None] | None = None,
    ) -> tuple[str, dict[str, Any], bool]:
        usage: dict[str, Any] = {}
        if self.config.get("streaming_enabled", False):
            chunks: list[str] = []
            try:
                for chunk in self.client.chat_stream(messages):
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    if stream_callback is not None:
                        stream_callback(chunk)
                if chunks:
                    return "".join(chunks).strip(), usage, True
            except Exception:
                if chunks:
                    return "".join(chunks).strip(), usage, True
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
        return str(result.get("content") or ""), usage, False

    def _call_aux(self, messages: list[dict[str, str]], source: str, overrides: dict[str, Any] | None = None) -> str:
        """Auxiliary model call (metadata, rewrite) with token accounting."""
        result = self.client.chat_with_usage(messages, overrides)
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
        return str(result.get("content") or "")

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
            raw = self._call_aux(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text},
                ],
                "metadata_extract",
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

    def _enforce_reply_language(self, reply: str) -> tuple[str, dict[str, Any]]:
        """Return (reply, rewrite_info). rewrite_info is {} when nothing happened."""
        reply = str(reply or '').strip()
        if not self.config.get("language_enforce_rewrite", True):
            return reply, {}
        language = str(self.config.get("language") or "en").lower()
        if not _reply_language_mismatch(reply, language):
            return reply, {}
        prompt = rewrite_prompt(language)
        info: dict[str, Any] = {"triggered": True, "rewritten": False}
        try:
            rewritten = self._call_aux(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": reply},
                ],
                "language_rewrite",
                overrides={"temperature": 0.1, "max_tokens": min(700, int(self.config.get("max_tokens", 900)))},
            )
            parsed = parse_assistant_response(rewritten)
            text = str(parsed.get("text") or rewritten).strip()
            if text:
                # Never fall back to the original when the rewrite is closer to
                # the target language: pick whichever conforms better, so a
                # partial rewrite still beats keeping the wrong-language original.
                best = _more_conformant_to_language(reply, text, language)
                info["rewritten"] = best != reply
                return best, info
        except Exception as exc:
            info["error"] = self._safe_error(exc)
        return reply, info

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
        language = target_language(self.config)
        if language == "en":
            prompt = (
                "Summarize this casual companion chat for future memory. "
                "Only keep stable facts, current plans, open threads, recurring preferences, and emotionally important context. "
                "Do not invent a plot. Do not overinterpret. Return 3-8 concise bullet points in English."
            )
        else:
            prompt = (
                "请为未来记忆总结这段闲聊式伴侣对话。"
                "只保留稳定事实、当前计划、未完成话题、反复出现的偏好和情绪上重要的上下文。"
                "不要编造主线，不要过度解读。用简体中文返回 3-8 条简洁要点。"
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
            return {"ok": False, "summary_id": 0, "error": self._safe_error(exc)}
        summary = summary.strip()
        if not summary:
            return {"ok": False, "summary_id": 0, "error": "empty summary"}
        summary_id = self.store.add_summary(summary, "auto", start_id, end_id, 3, language=language)
        return {"ok": True, "summary_id": summary_id, "start_id": start_id, "end_id": end_id}
