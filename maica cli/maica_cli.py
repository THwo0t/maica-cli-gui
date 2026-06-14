#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MAICA CLI entry point."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shlex
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

from client import OpenAICompatibleClient
from config_defaults import DEFAULT_CONFIG
from config_io import load_json, save_json
from dataset_builder import export_dialogue_dataset
from embedding_index import (
    build_memory_vector_index,
    build_vector_index,
    check_memory_vector_ready,
    print_vector_report,
    search_memory_vectors,
    search_vector_examples,
)
from engine import MaicaEngine
from example_bank import build_query_retrieval_text
from mfocus import build_messages, build_spire_messages, status_summary
from mtrigger import apply_mtrigger
from persona import relationship_stage
from response import limit_dialogue_sentences, parse_assistant_response
from response_planner import build_response_plan
from spire_topics import choose_spire_topic
from store import Store
from style import StyleStore, import_default_style_sources, style_db_path


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
DB_PATH = APP_DIR / "maica_cli.db"


HELP_TEXT = """\
Commands:
  /help                         Show this help.
  /exit                         Exit.
  /config                       Show current model config.
  /config set <key> <value>     Update config.json.
  /mode                         Show MFocus/MTrigger modes.
  /mode mfocus rule|off         Set MFocus mode.
  /mode mtrigger rule|off       Set MTrigger mode.
  /debug on|off                 Toggle debug output.
  /logs on|off                  Toggle JSONL logs.
  /db reset <password>          Clear local user database after password check.
  /db clear <part> <password>   Clear one part: profile, messages, memories, facts, events.
  /events [limit]               Show recent internal events.
  /dataset import [source_dir]   Import MAICA_ds_basis into style.db.
  /dataset stats                 Show style dataset stats.
  /dataset export [output_dir]   Export logs into reviewable dialogue dataset files.
  /style                         Show style settings.
  /style on|off                  Toggle dataset style retrieval.
  /style stats                   Show style.db stats.
  /style examples <query>        Preview style examples.
  /vector check                  Check optional vector/RAG dependencies and files.
  /vector build                  Build FAISS index for dialogue examples.
  /vector search <text>          Preview vector example retrieval.
  /vector debug <text>           Show response-plan and vector candidate debug.
  /vector on|off                 Toggle vector retrieval in chat.
  /status                       Show MAS-like session status.
  /profile                      Show profile and relationship state.
  /profile setup                Interactive profile initialization.
  /profile fields               Show SFE profile fields.
  /profile unset <key>          Clear one profile field.
  /profile set <key> <value>    Set profile item. Common keys: player_name, birthday, location.
  /nickname                     Show nickname list.
  /nickname add <name>          Add a nickname Monika may use for you.
  /nickname remove <name>       Remove a nickname.
  /nickname clear               Clear nickname list.
  /affection                    Show affection.
  /affection <value|+delta>     Set or adjust affection. Example: /affection 500, /affection +3.
  /remember <text>              Save a long-term memory.
  /memories [query]             List or search memories.
  /facts [query]                List or search stable SFE facts.
  /fact add <text>              Add one stable fact for MFocus.
  /fact edit <id> <text>        Edit one stable fact.
  /fact delete <id>             Delete one stable fact.
  /memory edit <id> <text>      Edit one memory.
  /memory tag <id> <tags>       Set comma-separated memory tags.
  /memory importance <id> <1-5> Set memory importance.
  /memory summarize [turns]     Summarize recent chat into memories.
  /memory vector check          Check memory vector dependencies and files.
  /memory vector build          Build FAISS index for memories.
  /memory vector search <text>  Preview memory vector retrieval.
  /memory vector on|off         Toggle future memory vector retrieval.
  /forget <id>                  Delete one memory by id.
  /forget all --yes             Delete all memories.
  /spire [hint]                 Let Monika proactively start a topic.
                                Without hint, daily and reflective topics are 50/50.
  /reset                        Clear chat history only.

Anything else will be sent as chat input.
"""


def configure_stdio() -> None:
    """Prefer UTF-8 on Windows terminals and redirected streams."""
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def ensure_config() -> dict[str, Any]:
    config_exists = CONFIG_PATH.exists()
    config = load_json(CONFIG_PATH, DEFAULT_CONFIG)
    if not config_exists:
        save_json(CONFIG_PATH, config)
        print(f"[setup] Created config: {CONFIG_PATH}")
    else:
        missing_keys = [key for key in DEFAULT_CONFIG if key not in load_json(CONFIG_PATH, {})]
        if missing_keys:
            save_json(CONFIG_PATH, config)
            print(f"[setup] Added config defaults: {', '.join(missing_keys)}")
    return config


def print_wrapped(prefix: str, text: str) -> None:
    width = 88
    paragraphs = split_display_sentences(text)
    for index, para in enumerate(paragraphs):
        if not para:
            print()
            continue
        wrapped = textwrap.wrap(para, width=width, replace_whitespace=False) or [""]
        for i, line in enumerate(wrapped):
            if index == 0 and i == 0:
                print(prefix + line)
            else:
                print(" " * len(prefix) + line)


def split_display_sentences(text: str) -> list[str]:
    """Split dialogue display so each sentence starts on a new CLI line."""
    lines: list[str] = []
    for paragraph in str(text or "").splitlines() or [str(text or "")]:
        paragraph = paragraph.strip()
        if not paragraph:
            lines.append("")
            continue
        parts = re.findall(r"[^。！？!?]+[。！？!?]?", paragraph)
        cleaned = [part.strip() for part in parts if part.strip()]
        lines.extend(cleaned or [paragraph])
    return lines or [""]


def parse_config_value(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def persist_config(config: dict[str, Any]) -> None:
    save_json(CONFIG_PATH, config)


def write_jsonl_log(config: dict[str, Any], payload: dict[str, Any]) -> None:
    if not config.get("jsonl_logs_enabled", True):
        return
    now = dt.datetime.now()
    log_root = Path(str(config.get("jsonl_logs_path") or "logs"))
    if not log_root.is_absolute():
        log_root = APP_DIR / log_root
    log_dir = log_root / now.strftime("%Y-%m")
    log_dir.mkdir(parents=True, exist_ok=True)
    payload = {"time": now.isoformat(timespec="seconds"), **payload}
    with (log_dir / f"{now.strftime('%Y-%m-%d')}.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def extract_json_object(text: str) -> dict[str, Any] | None:
    import re

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


def build_vector_debug_report(store: Store, config: dict[str, Any], query: str) -> dict[str, Any]:
    debug_config = dict(config)
    response_plan = build_response_plan(store, debug_config, query, {"use_session": True})
    retrieval_text = build_query_retrieval_text(query, response_plan)
    compact_plan = compact_debug_plan({"response_plan": response_plan}).get("response_plan", {})
    return {
        "query": query,
        "embedding_enabled": bool(config.get("embedding_enabled", False)),
        "category": response_plan.get("category"),
        "intent": response_plan.get("intent"),
        "mode": response_plan.get("mode"),
        "emotion": response_plan.get("emotion"),
        "retrieval_text": retrieval_text,
        "example_bank": response_plan.get("example_bank", {}),
        "example_summaries": compact_plan.get("example_summaries", []),
    }


def summarize_recent_memories(store: Store, client: OpenAICompatibleClient, turns: int = 8) -> list[int]:
    messages = store.recent_messages(max(2, turns * 2))
    if not messages:
        print("[memory] no recent chat history to summarize")
        return []
    prompt = (
        "你是 MAICA CLI 的长期记忆整理器. 从最近对话中提取值得长期记住的信息. "
        "只输出 JSON: {\"memories\":[{\"text\":\"...\",\"importance\":1-5,\"tags\":\"...\"}]}. "
        "保存范围限于稳定事实、偏好、关系信息和重要经历."
    )
    reply = client.chat(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(messages, ensure_ascii=False)},
        ],
        overrides={"temperature": 0.0, "max_tokens": 500},
    )
    data = extract_json_object(reply)
    if not data or not isinstance(data.get("memories"), list):
        print("[memory] summary produced no valid memories")
        return []
    saved = []
    for item in data["memories"]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        try:
            importance = int(item.get("importance", 2))
        except (TypeError, ValueError):
            importance = 2
        tags = str(item.get("tags") or "summary")
        saved.append(store.add_memory(text, tags=tags, importance=max(1, min(5, importance))))
    print(f"[memory] summarized and saved {len(saved)} memories")
    return saved


PROFILE_SETUP_FIELDS = [
    ("player_name", "玩家名", "莫妮卡平时称呼你的基础名字"),
    ("birthday", "生日", "格式建议 YYYY-MM-DD, 可跳过"),
    ("location", "所在地", "城市或大致地区, 可跳过"),
    ("nicknames", "昵称列表", "用逗号分隔, 例如 亲爱的,小太阳"),
    ("pronouns", "称呼/代词偏好", "例如 他/她/TA, 或你希望被怎么称呼"),
    ("gender", "性别信息", "可跳过"),
    ("favorite_color", "喜欢的颜色", "可跳过"),
    ("favorite_music", "喜欢的音乐", "例如 钢琴曲、摇滚、Vocaloid"),
    ("favorite_food", "喜欢的食物", "可跳过"),
    ("likes_rain", "雨天偏好", "例如 喜欢雨天 / 不太喜欢雨天"),
    ("likes_horror", "恐怖作品偏好", "例如 喜欢 / 不喜欢 / 可以接受"),
    ("likes_poetry", "诗歌偏好", "例如 喜欢现代诗"),
    ("personality", "性格倾向", "例如 偏内向、慢热、容易紧张"),
    ("appearance", "外貌备注", "只填你愿意让莫妮卡知道的内容"),
    ("family_note", "家庭备注", "敏感项, 可直接跳过"),
    ("health_note", "身心状态备注", "敏感项, 可直接跳过"),
    ("study_work", "学习/工作状态", "例如 正在准备考试 / 工作很忙"),
]


def profile_fields_info() -> list[dict[str, str]]:
    return [{"key": key, "label": label, "description": description} for key, label, description in PROFILE_SETUP_FIELDS]


def run_profile_setup(store: Store, input_fn=input, print_fn=print) -> None:
    print_fn("[profile setup] 回车跳过字段; 输入 '-' 可清空该字段.")
    for key, label, description in PROFILE_SETUP_FIELDS:
        if key == "nicknames":
            current = ", ".join(store.get_nicknames())
        else:
            current = store.get_profile_value(key, "")
        prompt = f"{label} ({key})"
        if current:
            prompt += f" [当前: {current}]"
        prompt += f" - {description}: "
        value = input_fn(prompt).strip()
        if not value:
            continue
        if key == "nicknames":
            if value == "-":
                store.set_nicknames([])
            else:
                nicknames = [item.strip() for item in re.split(r"[,，、]", value) if item.strip()]
                store.set_nicknames(nicknames)
        else:
            store.set_profile_value(key, "" if value == "-" else value)
    print_fn("[profile setup] completed")


def handle_command(
    command: str,
    store: Store,
    config: dict[str, Any],
    client: OpenAICompatibleClient | None = None,
    engine: MaicaEngine | None = None,
) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        print(f"[command] {exc}")
        return True
    if not parts:
        return True

    name = parts[0].lower()
    if name in {"/exit", "/quit"}:
        raise EOFError
    if name == "/help":
        print(HELP_TEXT)
        return True
    if name == "/config":
        if len(parts) >= 4 and parts[1] == "set":
            key = parts[2]
            value = parse_config_value(" ".join(parts[3:]))
            if key == "api_key":
                print("[config] Refusing to set api_key from command history. Edit config.json manually.")
                return True
            config[key] = value
            persist_config(config)
            print(f"[config] {key} = {value}")
        else:
            safe = config.copy()
            if safe.get("api_key"):
                safe["api_key"] = "***"
            print(json.dumps(safe, ensure_ascii=False, indent=2))
        return True
    if name == "/mode":
        valid = {"rule", "off"}
        if len(parts) == 1:
            print(
                json.dumps(
                    {
                        "mfocus_mode": config.get("mfocus_mode"),
                        "mtrigger_mode": config.get("mtrigger_mode"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return True
        if len(parts) != 3 or parts[1] not in {"mfocus", "mtrigger"} or parts[2] not in valid:
            print("[mode] Usage: /mode mfocus rule|off OR /mode mtrigger rule|off")
            return True
        key = "mfocus_mode" if parts[1] == "mfocus" else "mtrigger_mode"
        config[key] = parts[2]
        persist_config(config)
        print(f"[mode] {key} = {parts[2]}")
        return True
    if name == "/debug":
        if len(parts) != 2 or parts[1] not in {"on", "off"}:
            print("[debug] Usage: /debug on|off")
            return True
        config["show_debug"] = parts[1] == "on"
        persist_config(config)
        print(f"[debug] show_debug = {config['show_debug']}")
        return True
    if name == "/logs":
        if len(parts) != 2 or parts[1] not in {"on", "off"}:
            print("[logs] Usage: /logs on|off")
            return True
        config["jsonl_logs_enabled"] = parts[1] == "on"
        persist_config(config)
        print(f"[logs] jsonl_logs_enabled = {config['jsonl_logs_enabled']}")
        return True
    if name == "/db":
        if len(parts) < 2 or parts[1] not in {"reset", "clear"}:
            print("[db] Usage: /db reset <password> OR /db clear profile|messages|memories|facts|events <password>")
            return True
        if parts[1] == "clear":
            if len(parts) < 4:
                print("[db] Usage: /db clear profile|messages|memories|facts|events <password>")
                return True
            target = parts[2].lower()
            provided = " ".join(parts[3:]).strip()
        else:
            target = "all"
            provided = " ".join(parts[2:]).strip()
        expected = str(config.get("database_reset_password") or "")
        if not expected:
            print("[db] database_reset_password is empty; refusing reset")
            return True
        if not provided:
            print("[db] Usage: /db reset <password>")
            return True
        if provided != expected:
            print("[db] wrong password; database was not changed")
            return True
        if target == "all":
            store.reset_database()
            store.begin_session()
            print("[db] local user database cleared and profile reset")
            return True
        if target == "profile":
            store.reset_profile()
            store.begin_session()
            print("[db] profile reset")
            return True
        if target == "messages":
            deleted = store.clear_all_messages()
            print(f"[db] messages cleared ({deleted})")
            return True
        if target == "memories":
            deleted = store.clear_memories()
            print(f"[db] memories cleared ({deleted})")
            return True
        if target == "facts":
            deleted = store.clear_facts()
            print(f"[db] facts cleared ({deleted})")
            return True
        if target == "events":
            deleted = store.clear_events()
            print(f"[db] events cleared ({deleted})")
            return True
        print("[db] Unknown part. Use: profile, messages, memories, facts, events")
        return True
    if name == "/dataset":
        if len(parts) < 2 or parts[1] not in {"import", "stats", "export"}:
            print("[dataset] Usage: /dataset import [source_dir] OR /dataset stats OR /dataset export [output_dir]")
            return True
        if parts[1] == "import":
            source_root = parts[2] if len(parts) >= 3 else None
            try:
                result = import_default_style_sources(config, source_root=source_root)
            except Exception as exc:
                print(f"[dataset] import failed: {exc}")
                return True
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return True
        if parts[1] == "export":
            output_dir = parts[2] if len(parts) >= 3 else "dialogue_dataset"
            try:
                result = export_dialogue_dataset(config.get("jsonl_logs_path", "logs"), output_dir)
            except Exception as exc:
                print(f"[dataset] export failed: {exc}")
                return True
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return True
        try:
            db = StyleStore(style_db_path(config))
            try:
                print(json.dumps(db.stats(), ensure_ascii=False, indent=2))
            finally:
                db.close()
        except Exception as exc:
            print(f"[dataset] stats failed: {exc}")
        return True
    if name == "/style":
        if len(parts) == 1:
            print(
                json.dumps(
                    {
                        "style_enabled": config.get("style_enabled"),
                        "style_db_path": str(style_db_path(config)),
                        "style_example_limit": config.get("style_example_limit"),
                        "style_max_source_length": config.get("style_max_source_length"),
                        "anti_grandiosity": config.get("anti_grandiosity"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return True
        sub = parts[1].lower()
        if sub in {"on", "off"}:
            config["style_enabled"] = sub == "on"
            persist_config(config)
            print(f"[style] style_enabled = {config['style_enabled']}")
            return True
        if sub == "stats":
            db = StyleStore(style_db_path(config))
            try:
                print(json.dumps(db.stats(), ensure_ascii=False, indent=2))
            finally:
                db.close()
            return True
        if sub == "examples":
            query = command[len("/style examples") :].strip()
            if not query:
                print("[style] Usage: /style examples <query>")
                return True
            db = StyleStore(style_db_path(config))
            try:
                rows = db.search(
                    query,
                    language=str(config.get("language") or "zh"),
                    limit=int(config.get("style_example_limit", 3)),
                    max_length=int(config.get("style_max_source_length", 220)),
                )
            finally:
                db.close()
            if not rows:
                print("[style] no examples found. Try /dataset import first.")
            for row in rows:
                print(f"[#{row['id']}] ({row['category']}, {row['emotion']}) {row['user_text']} -> {row['assistant_text']}")
            return True
        print("[style] Usage: /style on|off|stats|examples <query>")
        return True
    if name == "/vector":
        if len(parts) == 2 and parts[1].lower() == "check":
            print_vector_report(config)
            return True
        if len(parts) == 2 and parts[1].lower() == "build":
            try:
                result = build_vector_index(config)
            except Exception as exc:
                print(f"[vector] build failed: {exc}")
                return True
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return True
        if len(parts) == 2 and parts[1].lower() in {"on", "off"}:
            config["embedding_enabled"] = parts[1].lower() == "on"
            persist_config(config)
            print(f"[vector] embedding_enabled = {config['embedding_enabled']}")
            return True
        if len(parts) >= 3 and parts[1].lower() == "search":
            query = command[len("/vector search") :].strip()
            if not query:
                print("[vector] Usage: /vector search <text>")
                return True
            try:
                rows = search_vector_examples(
                    query,
                    config,
                    limit=int(config.get("embedding_top_k", 30)),
                    min_score=float(config.get("embedding_min_score", 0.55)),
                )
            except Exception as exc:
                print(f"[vector] search failed: {exc}")
                return True
            if not rows:
                print("[vector] no examples found. Try /vector build or lower embedding_min_score.")
                return True
            for index, row in enumerate(rows[:10], start=1):
                score = row.get("_vector_score", 0)
                category = row.get("category", "")
                intent = row.get("intent", "")
                user = row.get("user", "")
                assistant = row.get("assistant", "")
                source = row.get("source", "")
                print(f"[{index}] score={score} category={category} intent={intent} source={source}")
                print(f"    user: {user}")
                print(f"    assistant: {assistant}")
            return True
        if len(parts) >= 3 and parts[1].lower() == "debug":
            query = command[len("/vector debug") :].strip()
            if not query:
                print("[vector] Usage: /vector debug <text>")
                return True
            try:
                report = build_vector_debug_report(store, config, query)
            except Exception as exc:
                print(f"[vector] debug failed: {exc}")
                return True
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return True
        print("[vector] Usage: /vector check|build|on|off|search <text>|debug <text>")
        return True
    if name == "/events":
        limit = 20
        if len(parts) >= 2:
            try:
                limit = max(1, min(100, int(parts[1])))
            except ValueError:
                print("[events] limit must be a number")
                return True
        events = store.recent_events(limit)
        if not events:
            print("[events] no events found")
        for event in events:
            print(f"[#{event['id']}] {event['created_at']} {event['type']} {event['payload']}")
        return True
    if name == "/status":
        print(json.dumps(status_summary(store, config), ensure_ascii=False, indent=2))
        return True
    if name == "/profile":
        if len(parts) >= 2 and parts[1] == "setup":
            run_profile_setup(store)
            return True
        if len(parts) >= 2 and parts[1] == "fields":
            print(json.dumps(profile_fields_info(), ensure_ascii=False, indent=2))
            return True
        if len(parts) >= 3 and parts[1] == "unset":
            key = parts[2]
            if key == "nicknames":
                store.set_nicknames([])
            elif key == "affection":
                store.set_affection(200)
            else:
                store.set_profile_value(key, "")
            print(f"[profile] cleared {key}")
            return True
        if len(parts) >= 4 and parts[1] == "set":
            key = parts[2]
            value = " ".join(parts[3:])
            if key == "affection":
                store.set_affection(float(value))
            else:
                store.set_profile_value(key, value)
            print(f"[profile] {key} = {value}")
        else:
            profile = store.get_profile()
            print(json.dumps(profile, ensure_ascii=False, indent=2))
        return True
    if name == "/nickname":
        if len(parts) == 1:
            print(json.dumps({"nicknames": store.get_nicknames()}, ensure_ascii=False, indent=2))
            return True
        sub = parts[1].lower()
        if sub == "add":
            nickname = command[len("/nickname add") :].strip()
            if not nickname:
                print("[nickname] Usage: /nickname add <name>")
                return True
            nicknames = store.add_nickname(nickname)
            print(json.dumps({"nicknames": nicknames}, ensure_ascii=False, indent=2))
            return True
        if sub == "remove":
            nickname = command[len("/nickname remove") :].strip()
            if not nickname:
                print("[nickname] Usage: /nickname remove <name>")
                return True
            ok = store.remove_nickname(nickname)
            print("[nickname] removed" if ok else "[nickname] not found")
            return True
        if sub == "clear":
            store.set_nicknames([])
            print("[nickname] cleared")
            return True
        print("[nickname] Usage: /nickname add|remove|clear")
        return True
    if name == "/affection":
        if len(parts) == 1:
            affection = store.affection()
            print(f"[affection] {affection:.2f} ({relationship_stage(affection)})")
        else:
            raw = parts[1]
            if raw.startswith(("+", "-")):
                value = store.set_affection(store.affection() + float(raw))
            else:
                value = store.set_affection(float(raw))
            print(f"[affection] {value:.2f} ({relationship_stage(value)})")
        return True
    if name == "/remember":
        text = command[len("/remember") :].strip()
        if not text:
            print("[memory] Usage: /remember <text>")
        else:
            memory_id = store.add_memory(text, importance=2)
            print(f"[memory] saved #{memory_id}")
        return True
    if name == "/memories":
        query = command[len("/memories") :].strip()
        rows = store.search_memories(query, limit=20 if not query else 8)
        if not rows:
            print("[memory] no memories found")
        for row in rows:
            print(
                f"[#{row['id']}] {row['text']} "
                f"(importance={row['importance']}, tags={row['tags']}, created={row['created_at']})"
            )
        return True
    if name == "/facts":
        query = command[len("/facts") :].strip()
        rows = store.search_facts(query, limit=30 if not query else int(config.get("sfe_fact_limit", 14)))
        if not rows:
            print("[facts] no custom facts found")
        for row in rows:
            print(
                f"[#{row['id']}] {row['text']} "
                f"(category={row['category']}, importance={row['importance']}, source={row['source']})"
            )
        return True
    if name == "/fact":
        if len(parts) < 2:
            print("[facts] Usage: /fact add <text> OR /fact edit <id> <text> OR /fact delete <id>")
            return True
        sub = parts[1].lower()
        if sub == "add":
            text = command[len("/fact add") :].strip()
            if not text:
                print("[facts] Usage: /fact add <text>")
                return True
            fact_id = store.add_fact(text, category="custom", source="user", importance=2)
            print(f"[facts] saved #{fact_id}")
            return True
        if sub in {"edit", "delete"}:
            if len(parts) < 3:
                print("[facts] fact id is required")
                return True
            try:
                fact_id = int(parts[2])
            except ValueError:
                print("[facts] fact id must be a number")
                return True
            if sub == "delete":
                print(f"[facts] deleted #{fact_id}" if store.delete_fact(fact_id) else f"[facts] no fact #{fact_id}")
                return True
            text = " ".join(parts[3:]).strip()
            if not text:
                print("[facts] Usage: /fact edit <id> <text>")
                return True
            print(f"[facts] updated #{fact_id}" if store.update_fact_text(fact_id, text) else f"[facts] no fact #{fact_id}")
            return True
        print("[facts] Unknown subcommand. Use add, edit, or delete.")
        return True
    if name == "/memory":
        if len(parts) < 2:
            print("[memory] Usage: /memory edit|tag|importance|summarize|vector ...")
            return True
        sub = parts[1].lower()
        if sub == "vector":
            if len(parts) < 3:
                print("[memory] Usage: /memory vector check|build|on|off|search <text>")
                return True
            vector_sub = parts[2].lower()
            if vector_sub == "check":
                print(json.dumps(check_memory_vector_ready(store, config), ensure_ascii=False, indent=2))
                return True
            if vector_sub == "build":
                try:
                    result = build_memory_vector_index(store, config)
                except Exception as exc:
                    print(f"[memory] vector build failed: {exc}")
                    return True
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return True
            if vector_sub in {"on", "off"}:
                config["memory_embedding_enabled"] = vector_sub == "on"
                persist_config(config)
                print(f"[memory] memory_embedding_enabled = {config['memory_embedding_enabled']}")
                return True
            if vector_sub == "search":
                query = command[len("/memory vector search") :].strip()
                if not query:
                    print("[memory] Usage: /memory vector search <text>")
                    return True
                try:
                    rows = search_memory_vectors(
                        query,
                        config,
                        limit=int(config.get("memory_embedding_top_k", 8)),
                        min_score=float(config.get("memory_embedding_min_score", 0.55)),
                    )
                except Exception as exc:
                    print(f"[memory] vector search failed: {exc}")
                    return True
                if not rows:
                    print("[memory] no vector memories found. Try /memory vector build or lower memory_embedding_min_score.")
                    return True
                for index, row in enumerate(rows, start=1):
                    print(
                        f"[{index}] score={row.get('_vector_score')} "
                        f"id={row.get('id')} importance={row.get('importance')} tags={row.get('tags')}"
                    )
                    print(f"    {row.get('text')}")
                return True
            print("[memory] Usage: /memory vector check|build|on|off|search <text>")
            return True
        if sub == "summarize":
            if client is None:
                print("[memory] model client unavailable")
                return True
            turns = 8
            if len(parts) >= 3:
                try:
                    turns = max(1, min(30, int(parts[2])))
                except ValueError:
                    print("[memory] turns must be a number")
                    return True
            summarize_recent_memories(store, client, turns)
            return True
        if len(parts) < 4:
            print("[memory] Usage: /memory edit <id> <text> OR /memory tag <id> <tags> OR /memory importance <id> <1-5>")
            return True
        try:
            memory_id = int(parts[2])
        except ValueError:
            print("[memory] memory id must be a number")
            return True
        value = " ".join(parts[3:])
        if sub == "edit":
            ok = store.update_memory_text(memory_id, value)
        elif sub == "tag":
            ok = store.update_memory_tags(memory_id, value)
        elif sub == "importance":
            try:
                ok = store.update_memory_importance(memory_id, int(value))
            except ValueError:
                print("[memory] importance must be 1-5")
                return True
        else:
            print("[memory] Unknown subcommand. Use edit, tag, importance, or summarize.")
            return True
        print(f"[memory] updated #{memory_id}" if ok else f"[memory] no memory #{memory_id}")
        return True
    if name == "/forget":
        if len(parts) < 2:
            print("[memory] Usage: /forget <id> OR /forget all --yes")
            return True
        target = parts[1].lower()
        if target == "all":
            if "--yes" not in parts[2:]:
                print("[memory] This deletes all memories. Use: /forget all --yes")
                return True
            deleted = store.clear_memories()
            print(f"[memory] deleted all memories ({deleted})")
            return True
        try:
            memory_id = int(target)
        except ValueError:
            print("[memory] memory id must be a number")
            return True
        if store.delete_memory(memory_id):
            print(f"[memory] deleted #{memory_id}")
        else:
            print(f"[memory] no memory #{memory_id}")
        return True
    if name == "/spire":
        if engine is None:
            print("[spire] engine unavailable")
            return True
        topic_hint = command[len("/spire") :].strip()
        result = engine.spire(topic_hint)
        if not result.get("ok"):
            print(f"[spire error] {result.get('error')}")
            return True
        print_wrapped("monika> ", str(result.get("text") or ""))
        if config.get("show_debug", True):
            print(f"[debug] response_meta={{'emotion': {result.get('emotion')!r}, 'action': {result.get('action')!r}}}")
            debug = result.get("debug") if isinstance(result.get("debug"), dict) else {}
            print(f"[debug] spire_topic={debug.get('spire_topic')}")
            print(f"[debug] spire_mfocus_plan={debug.get('mfocus_plan')}")
            print(f"[debug] response_time={float(result.get('response_time') or 0):.2f}s")
        return True
    if name == "/reset":
        store.clear_messages()
        print("[chat] history cleared")
        return True

    print(f"[command] Unknown command: {name}. Try /help.")
    return True


def repl(config: dict[str, Any], store: Store) -> None:
    engine = MaicaEngine(config=config, store=store, app_dir=APP_DIR)
    client = engine.client
    print("MAICA CLI Debugger v0.11.7")
    print("Type /help for debug commands, /exit to quit. Use maica gui/gui_app.py for the GUI.")
    print()

    while True:
        try:
            user_input = input("you> ").strip()
        except EOFError:
            print("\nbye.")
            return
        except KeyboardInterrupt:
            print("\nbye.")
            return

        if not user_input:
            continue
        if user_input.startswith("/"):
            try:
                handle_command(user_input, store, config, client, engine)
            except EOFError:
                print("bye.")
                return
            except Exception as exc:
                print(f"[command error] {exc}")
            continue

        result = engine.chat(user_input)
        if not result.get("ok"):
            print(f"[model error] {result.get('error')}")
            continue

        print_wrapped("monika> ", str(result.get("text") or ""))
        for notice in result.get("mtrigger_notices", []):
            print(f"[{notice}]")

        if config.get("show_debug", True):
            print(f"[debug] response_meta={{'emotion': {result.get('emotion')!r}, 'action': {result.get('action')!r}}}")
            debug = result.get("debug") if isinstance(result.get("debug"), dict) else {}
            print(f"[debug] mfocus_plan={debug.get('mfocus_plan')}")
            print(f"[debug] response_time={float(result.get('response_time') or 0):.2f}s")


def main() -> int:
    global CONFIG_PATH, DB_PATH

    configure_stdio()

    parser = argparse.ArgumentParser(description="MAICA CLI minimal prototype")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="Path to config.json")
    parser.add_argument("--db", default=str(DB_PATH), help="Path to SQLite database")
    args = parser.parse_args()

    CONFIG_PATH = Path(args.config).resolve()
    DB_PATH = Path(args.db).resolve()

    config = ensure_config()
    store = Store(DB_PATH)
    store.begin_session()
    try:
        repl(config, store)
    finally:
        store.end_session()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
