# -*- coding: utf-8 -*-
"""SQLite-backed local state for MAICA CLI."""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from persona import relationship_stage


PROFILE_DEFAULTS = {
    "player_name": "player",
    "birthday": "",
    "location": "",
    "nicknames": "[]",
    "affection": "200",
    "relationship_stage": "亲密的情侣关系",
    "first_seen": "",
    "last_seen": "",
    "last_session_start": "",
    "session_count": "0",
    "total_chat_turns": "0",
}


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.ensure_schema()

    def ensure_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS profile (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                tags TEXT DEFAULT '',
                importance INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER DEFAULT 1,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT DEFAULT 'custom',
                text TEXT NOT NULL,
                source TEXT DEFAULT 'user',
                importance INTEGER DEFAULT 2,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        for key, value in PROFILE_DEFAULTS.items():
            self.conn.execute(
                "INSERT OR IGNORE INTO profile(key, value) VALUES (?, ?)",
                (key, value),
            )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def now(self) -> str:
        return dt.datetime.now().isoformat(timespec="seconds")

    def get_profile(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT key, value FROM profile ORDER BY key").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def get_profile_value(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM profile WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_profile_value(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO profile(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_nicknames(self) -> list[str]:
        raw = self.get_profile_value("nicknames", "[]")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = []
        if not isinstance(data, list):
            return []
        result = []
        for item in data:
            nickname = str(item).strip()
            if nickname and nickname not in result:
                result.append(nickname)
        return result

    def set_nicknames(self, nicknames: list[str]) -> list[str]:
        cleaned = []
        for item in nicknames:
            nickname = str(item).strip()
            if nickname and nickname not in cleaned:
                cleaned.append(nickname)
        self.set_profile_value("nicknames", json.dumps(cleaned, ensure_ascii=False))
        self.add_event("nicknames_updated", {"nicknames": cleaned})
        return cleaned

    def add_nickname(self, nickname: str) -> list[str]:
        nicknames = self.get_nicknames()
        nickname = nickname.strip()
        if nickname and nickname not in nicknames:
            nicknames.append(nickname)
        return self.set_nicknames(nicknames)

    def remove_nickname(self, nickname: str) -> bool:
        nicknames = self.get_nicknames()
        nickname = nickname.strip()
        updated = [item for item in nicknames if item != nickname]
        if len(updated) == len(nicknames):
            return False
        self.set_nicknames(updated)
        return True

    def begin_session(self) -> None:
        now = self.now()
        if not self.get_profile_value("first_seen"):
            self.set_profile_value("first_seen", now)
        self.set_profile_value("last_session_start", now)
        self.set_profile_value("session_count", str(self.int_profile_value("session_count") + 1))
        self.add_event("session_start", {"at": now})

    def end_session(self) -> None:
        now = self.now()
        self.set_profile_value("last_seen", now)
        self.add_event("session_end", {"at": now})

    def int_profile_value(self, key: str, default: int = 0) -> int:
        try:
            return int(float(self.get_profile_value(key, str(default))))
        except ValueError:
            return default

    def increment_chat_turns(self) -> int:
        turns = self.int_profile_value("total_chat_turns") + 1
        self.set_profile_value("total_chat_turns", str(turns))
        return turns

    def affection(self) -> float:
        try:
            return float(self.get_profile_value("affection", "200"))
        except ValueError:
            return 200.0

    def set_affection(self, value: float) -> float:
        value = max(-100.0, min(10_000.0, value))
        self.set_profile_value("affection", f"{value:.2f}".rstrip("0").rstrip("."))
        self.set_profile_value("relationship_stage", relationship_stage(value))
        self.add_event("affection", {"value": value})
        return value

    def add_memory(self, text: str, tags: str = "", importance: int = 1) -> int:
        now = self.now()
        cur = self.conn.execute(
            "INSERT INTO memories(text, tags, importance, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (text.strip(), tags, int(importance), now, now),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def search_memories(self, query: str = "", limit: int = 8) -> list[sqlite3.Row]:
        query = query.strip()
        if not query:
            return self.conn.execute(
                "SELECT * FROM memories ORDER BY importance DESC, updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        tokens = [token for token in re.findall(r"[\w\u4e00-\u9fff]{2,}", query.lower()) if token]
        if not tokens:
            tokens = [query.lower()]

        rows = self.conn.execute("SELECT * FROM memories").fetchall()
        scored = []
        for row in rows:
            haystack = (row["text"] + " " + row["tags"]).lower()
            score = sum(2 for token in tokens if token in haystack) + int(row["importance"])
            if score > int(row["importance"]):
                scored.append((score, row))
        scored.sort(key=lambda item: (item[0], item[1]["updated_at"]), reverse=True)
        return [row for _, row in scored[:limit]]

    def all_memories(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM memories ORDER BY id ASC"
        ).fetchall()

    def delete_memory(self, memory_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.conn.commit()
        deleted = cur.rowcount > 0
        if deleted:
            self.add_event("memory_deleted", {"memory_id": memory_id})
        return deleted

    def update_memory_text(self, memory_id: int, text: str) -> bool:
        cur = self.conn.execute(
            "UPDATE memories SET text = ?, updated_at = ? WHERE id = ?",
            (text.strip(), self.now(), memory_id),
        )
        self.conn.commit()
        updated = cur.rowcount > 0
        if updated:
            self.add_event("memory_edited", {"memory_id": memory_id})
        return updated

    def update_memory_tags(self, memory_id: int, tags: str) -> bool:
        cur = self.conn.execute(
            "UPDATE memories SET tags = ?, updated_at = ? WHERE id = ?",
            (tags.strip(), self.now(), memory_id),
        )
        self.conn.commit()
        updated = cur.rowcount > 0
        if updated:
            self.add_event("memory_tags_updated", {"memory_id": memory_id, "tags": tags})
        return updated

    def update_memory_importance(self, memory_id: int, importance: int) -> bool:
        importance = max(1, min(5, int(importance)))
        cur = self.conn.execute(
            "UPDATE memories SET importance = ?, updated_at = ? WHERE id = ?",
            (importance, self.now(), memory_id),
        )
        self.conn.commit()
        updated = cur.rowcount > 0
        if updated:
            self.add_event("memory_importance_updated", {"memory_id": memory_id, "importance": importance})
        return updated

    def clear_memories(self) -> int:
        cur = self.conn.execute("DELETE FROM memories")
        self.conn.execute("DELETE FROM sqlite_sequence WHERE name = 'memories'")
        self.conn.commit()
        deleted = cur.rowcount
        self.add_event("memories_cleared", {"count": deleted})
        return deleted

    def clear_facts(self) -> int:
        cur = self.conn.execute("DELETE FROM facts")
        self.conn.execute("DELETE FROM sqlite_sequence WHERE name = 'facts'")
        self.conn.commit()
        deleted = cur.rowcount
        self.add_event("facts_cleared", {"count": deleted})
        return deleted

    def add_fact(
        self,
        text: str,
        category: str = "custom",
        source: str = "user",
        importance: int = 2,
    ) -> int:
        now = self.now()
        cur = self.conn.execute(
            "INSERT INTO facts(category, text, source, importance, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (category.strip() or "custom", text.strip(), source.strip() or "user", int(importance), now, now),
        )
        self.conn.commit()
        fact_id = int(cur.lastrowid)
        self.add_event("fact_added", {"fact_id": fact_id, "category": category})
        return fact_id

    def search_facts(self, query: str = "", limit: int = 12) -> list[sqlite3.Row]:
        query = query.strip()
        if not query:
            return self.conn.execute(
                "SELECT * FROM facts ORDER BY importance DESC, updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        tokens = []
        for part in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", query.lower()):
            if re.fullmatch(r"[\u4e00-\u9fff]+", part):
                tokens.extend(part[index : index + 2] for index in range(max(0, len(part) - 1)))
            elif len(part) >= 2:
                tokens.append(part)
        if not tokens:
            tokens = [query.lower()]

        rows = self.conn.execute("SELECT * FROM facts").fetchall()
        scored = []
        for row in rows:
            haystack = (row["text"] + " " + row["category"] + " " + row["source"]).lower()
            score = sum(2 for token in tokens if token in haystack) + int(row["importance"])
            if score > int(row["importance"]):
                scored.append((score, row))
        scored.sort(key=lambda item: (item[0], item[1]["updated_at"]), reverse=True)
        return [row for _, row in scored[:limit]]

    def update_fact_text(self, fact_id: int, text: str) -> bool:
        cur = self.conn.execute(
            "UPDATE facts SET text = ?, updated_at = ? WHERE id = ?",
            (text.strip(), self.now(), fact_id),
        )
        self.conn.commit()
        updated = cur.rowcount > 0
        if updated:
            self.add_event("fact_edited", {"fact_id": fact_id})
        return updated

    def delete_fact(self, fact_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        self.conn.commit()
        deleted = cur.rowcount > 0
        if deleted:
            self.add_event("fact_deleted", {"fact_id": fact_id})
        return deleted

    def add_message(self, role: str, content: str, session_id: int = 1) -> None:
        self.conn.execute(
            "INSERT INTO messages(session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, self.now()),
        )
        self.conn.commit()

    def recent_messages(self, limit: int = 16, session_id: int = 1) -> list[dict[str, str]]:
        rows = self.conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    def clear_messages(self, session_id: int = 1) -> None:
        self.conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        self.conn.commit()

    def clear_all_messages(self) -> int:
        cur = self.conn.execute("DELETE FROM messages")
        self.conn.execute("DELETE FROM sqlite_sequence WHERE name = 'messages'")
        self.conn.commit()
        deleted = cur.rowcount
        self.add_event("messages_cleared", {"count": deleted})
        return deleted

    def clear_events(self) -> int:
        cur = self.conn.execute("DELETE FROM events")
        self.conn.execute("DELETE FROM sqlite_sequence WHERE name = 'events'")
        self.conn.commit()
        return cur.rowcount

    def reset_profile(self) -> None:
        self.conn.execute("DELETE FROM profile")
        for key, value in PROFILE_DEFAULTS.items():
            self.conn.execute(
                "INSERT INTO profile(key, value) VALUES (?, ?)",
                (key, value),
            )
        self.conn.commit()
        self.add_event("profile_reset", {"at": self.now()})

    def reset_database(self) -> None:
        self.conn.executescript(
            """
            DELETE FROM memories;
            DELETE FROM messages;
            DELETE FROM facts;
            DELETE FROM events;
            DELETE FROM profile;
            DELETE FROM sqlite_sequence WHERE name IN ('memories', 'messages', 'facts', 'events');
            """
        )
        for key, value in PROFILE_DEFAULTS.items():
            self.conn.execute(
                "INSERT INTO profile(key, value) VALUES (?, ?)",
                (key, value),
            )
        self.conn.commit()
        self.add_event("database_reset", {"at": self.now()})

    def add_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO events(type, payload, created_at) VALUES (?, ?, ?)",
            (event_type, json.dumps(payload, ensure_ascii=False), self.now()),
        )
        self.conn.commit()

    def recent_events(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
