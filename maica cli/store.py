# -*- coding: utf-8 -*-
"""SQLite-backed local state for MAICA CLI/GUI."""

from __future__ import annotations

import datetime as dt
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from language_runtime import source_hash, target_language, text_language
from persona import relationship_stage
from text_utils import split_query_tokens


SCHEMA_VERSION = 4

PROFILE_DEFAULTS = {
    'player_name': 'player',
    'birthday': '',
    'location': '',
    'nicknames': '[]',
    'affection': '200',
    'relationship_stage': 'familiar lovers',
    'first_seen': '',
    'last_seen': '',
    'last_session_start': '',
    'session_count': '0',
    'total_chat_turns': '0',
}


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.ensure_schema()

    def ensure_schema(self) -> None:
        old_version = int(self.conn.execute('PRAGMA user_version').fetchone()[0] or 0)
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
                language TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER DEFAULT 1,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                language TEXT DEFAULT '',
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
                language TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT DEFAULT 'session',
                text TEXT NOT NULL,
                source_start_id INTEGER DEFAULT 0,
                source_end_id INTEGER DEFAULT 0,
                importance INTEGER DEFAULT 2,
                language TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                model TEXT DEFAULT '',
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                estimated_cost REAL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS translation_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_kind TEXT NOT NULL,
                source_id INTEGER DEFAULT 0,
                source_hash TEXT NOT NULL,
                target_language TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source_kind, source_id, source_hash, target_language)
            );
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL,
                note TEXT DEFAULT ''
            );
            """
        )
        self.run_migrations(old_version)
        for key, value in PROFILE_DEFAULTS.items():
            self.conn.execute(
                'INSERT OR IGNORE INTO profile(key, value) VALUES (?, ?)',
                (key, value),
            )
        self.conn.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
        self.conn.commit()

    def run_migrations(self, old_version: int) -> None:
        """Apply small idempotent migrations for older local databases."""
        if old_version < 4:
            for table, column in (
                ('memories', 'language'),
                ('messages', 'language'),
                ('facts', 'language'),
                ('summaries', 'language'),
            ):
                self._ensure_column(table, column, "TEXT DEFAULT ''")
            self.conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_language ON memories(language, updated_at);
                CREATE INDEX IF NOT EXISTS idx_messages_language ON messages(language, session_id, id);
                CREATE INDEX IF NOT EXISTS idx_facts_language ON facts(language, updated_at);
                CREATE INDEX IF NOT EXISTS idx_summaries_language ON summaries(language, updated_at);
                CREATE INDEX IF NOT EXISTS idx_translation_cache_lookup
                    ON translation_cache(source_kind, source_id, source_hash, target_language);
                """
            )
        if old_version < 3:
            self.conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id, id);
                CREATE INDEX IF NOT EXISTS idx_memories_updated ON memories(updated_at);
                CREATE INDEX IF NOT EXISTS idx_facts_updated ON facts(updated_at);
                CREATE INDEX IF NOT EXISTS idx_events_type_created ON events(type, created_at);
                CREATE INDEX IF NOT EXISTS idx_summaries_source_end ON summaries(source_end_id);
                """
            )
        if old_version < SCHEMA_VERSION:
            self.conn.execute(
                'INSERT OR REPLACE INTO schema_migrations(version, applied_at, note) VALUES (?, ?, ?)',
                (SCHEMA_VERSION, self.now(), f'upgraded from {old_version}'),
            )

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        rows = self.conn.execute(f'PRAGMA table_info({table})').fetchall()
        if column not in {row['name'] for row in rows}:
            self.conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')

    def close(self) -> None:
        self.conn.close()

    def now(self) -> str:
        return dt.datetime.now().isoformat(timespec='seconds')

    def get_profile(self) -> dict[str, str]:
        rows = self.conn.execute('SELECT key, value FROM profile ORDER BY key').fetchall()
        return {row['key']: row['value'] for row in rows}

    def get_profile_value(self, key: str, default: str = '') -> str:
        row = self.conn.execute('SELECT value FROM profile WHERE key = ?', (key,)).fetchone()
        return row['value'] if row else default

    def set_profile_value(self, key: str, value: str) -> None:
        self.conn.execute(
            'INSERT INTO profile(key, value) VALUES (?, ?) '
            'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
            (key, value),
        )
        self.conn.commit()

    def get_nicknames(self) -> list[str]:
        raw = self.get_profile_value('nicknames', '[]')
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
        self.set_profile_value('nicknames', json.dumps(cleaned, ensure_ascii=False))
        self.add_event('nicknames_updated', {'nicknames': cleaned})
        return cleaned

    def add_nickname(self, nickname: str) -> list[str]:
        nicknames = self.get_nicknames()
        nickname = nickname.strip()
        if nickname and nickname not in nicknames:
            nicknames.append(nickname)
        return self.set_nicknames(nicknames)

    def remove_nickname(self, nickname: str) -> bool:
        nicknames = self.get_nicknames()
        updated = [item for item in nicknames if item != nickname.strip()]
        if len(updated) == len(nicknames):
            return False
        self.set_nicknames(updated)
        return True

    def begin_session(self) -> None:
        now = self.now()
        if not self.get_profile_value('first_seen'):
            self.set_profile_value('first_seen', now)
        self.set_profile_value('last_session_start', now)
        self.set_profile_value('session_count', str(self.int_profile_value('session_count') + 1))
        self.add_event('session_start', {'at': now})

    def end_session(self) -> None:
        now = self.now()
        self.set_profile_value('last_seen', now)
        self.add_event('session_end', {'at': now})

    def int_profile_value(self, key: str, default: int = 0) -> int:
        try:
            return int(float(self.get_profile_value(key, str(default))))
        except ValueError:
            return default

    def increment_chat_turns(self) -> int:
        turns = self.int_profile_value('total_chat_turns') + 1
        self.set_profile_value('total_chat_turns', str(turns))
        return turns

    def affection(self) -> float:
        try:
            return float(self.get_profile_value('affection', '200'))
        except ValueError:
            return 200.0

    def set_affection(self, value: float, minimum: float = -100.0, maximum: float = 10_000.0) -> float:
        value = max(float(minimum), min(float(maximum), float(value)))
        self.set_profile_value('affection', f'{value:.2f}'.rstrip('0').rstrip('.'))
        self.set_profile_value('relationship_stage', relationship_stage(value))
        self.add_event('affection', {'value': value})
        return value

    def add_memory(self, text: str, tags: str = '', importance: int = 1, language: str = '') -> int:
        now = self.now()
        language = target_language(language) if language else text_language(text)
        cur = self.conn.execute(
            'INSERT INTO memories(text, tags, importance, language, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
            (text.strip(), tags, int(importance), language, now, now),
        )
        self.conn.commit()
        memory_id = int(cur.lastrowid)
        self.add_event('memory_added', {'memory_id': memory_id})
        self.mark_memory_vector_dirty('memory_added', memory_id)
        return memory_id

    def mark_memory_vector_dirty(self, reason: str, memory_id: int = 0) -> None:
        self.set_profile_value('memory_vector_dirty', '1')
        self.set_profile_value('memory_vector_dirty_reason', reason)
        if memory_id:
            self.set_profile_value('memory_vector_dirty_id', str(memory_id))

    def clear_memory_vector_dirty(self) -> None:
        self.set_profile_value('memory_vector_dirty', '0')
        self.set_profile_value('memory_vector_dirty_reason', '')
        self.set_profile_value('memory_vector_dirty_id', '')

    def memory_vector_dirty(self) -> bool:
        return self.get_profile_value('memory_vector_dirty', '0') == '1'

    def _score_rows(self, rows: list[sqlite3.Row], query: str, fields: tuple[str, ...], limit: int) -> list[sqlite3.Row]:
        tokens = split_query_tokens(query) or [query.lower()]
        scored = []
        for row in rows:
            haystack = ' '.join(str(row[field] or '') for field in fields).lower()
            score = sum(2 for token in tokens if token in haystack) + int(row['importance'])
            if score > int(row['importance']):
                scored.append((score, row))
        scored.sort(key=lambda item: (item[0], item[1]['updated_at']), reverse=True)
        return [row for _, row in scored[:limit]]

    def search_memories(self, query: str = '', limit: int = 8) -> list[sqlite3.Row]:
        query = query.strip()
        if not query:
            return self.conn.execute(
                'SELECT * FROM memories ORDER BY importance DESC, updated_at DESC LIMIT ?',
                (limit,),
            ).fetchall()
        return self._score_rows(self.conn.execute('SELECT * FROM memories').fetchall(), query, ('text', 'tags'), limit)

    def all_memories(self) -> list[sqlite3.Row]:
        return self.conn.execute('SELECT * FROM memories ORDER BY id ASC').fetchall()

    def delete_memory(self, memory_id: int) -> bool:
        cur = self.conn.execute('DELETE FROM memories WHERE id = ?', (memory_id,))
        self.conn.commit()
        deleted = cur.rowcount > 0
        if deleted:
            self.add_event('memory_deleted', {'memory_id': memory_id})
            self.mark_memory_vector_dirty('memory_deleted', memory_id)
        return deleted

    def update_memory_text(self, memory_id: int, text: str) -> bool:
        cur = self.conn.execute(
            'UPDATE memories SET text = ?, updated_at = ? WHERE id = ?',
            (text.strip(), self.now(), memory_id),
        )
        self.conn.commit()
        updated = cur.rowcount > 0
        if updated:
            self.add_event('memory_edited', {'memory_id': memory_id})
            self.mark_memory_vector_dirty('memory_edited', memory_id)
        return updated

    def update_memory_tags(self, memory_id: int, tags: str) -> bool:
        cur = self.conn.execute(
            'UPDATE memories SET tags = ?, updated_at = ? WHERE id = ?',
            (tags.strip(), self.now(), memory_id),
        )
        self.conn.commit()
        updated = cur.rowcount > 0
        if updated:
            self.add_event('memory_tags_updated', {'memory_id': memory_id, 'tags': tags})
            self.mark_memory_vector_dirty('memory_tags_updated', memory_id)
        return updated

    def update_memory_importance(self, memory_id: int, importance: int) -> bool:
        importance = max(1, min(5, int(importance)))
        cur = self.conn.execute(
            'UPDATE memories SET importance = ?, updated_at = ? WHERE id = ?',
            (importance, self.now(), memory_id),
        )
        self.conn.commit()
        updated = cur.rowcount > 0
        if updated:
            self.add_event('memory_importance_updated', {'memory_id': memory_id, 'importance': importance})
            self.mark_memory_vector_dirty('memory_importance_updated', memory_id)
        return updated

    def clear_memories(self) -> int:
        cur = self.conn.execute('DELETE FROM memories')
        self.conn.execute("DELETE FROM sqlite_sequence WHERE name = 'memories'")
        self.conn.commit()
        deleted = cur.rowcount
        self.add_event('memories_cleared', {'count': deleted})
        self.mark_memory_vector_dirty('memories_cleared')
        return deleted

    def add_fact(self, text: str, category: str = 'custom', source: str = 'user', importance: int = 2, language: str = '') -> int:
        now = self.now()
        language = target_language(language) if language else text_language(text)
        cur = self.conn.execute(
            'INSERT INTO facts(category, text, source, importance, language, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (category.strip() or 'custom', text.strip(), source.strip() or 'user', int(importance), language, now, now),
        )
        self.conn.commit()
        fact_id = int(cur.lastrowid)
        self.add_event('fact_added', {'fact_id': fact_id, 'category': category})
        return fact_id

    def search_facts(self, query: str = '', limit: int = 12) -> list[sqlite3.Row]:
        query = query.strip()
        if not query:
            return self.conn.execute(
                'SELECT * FROM facts ORDER BY importance DESC, updated_at DESC LIMIT ?',
                (limit,),
            ).fetchall()
        return self._score_rows(
            self.conn.execute('SELECT * FROM facts').fetchall(),
            query,
            ('text', 'category', 'source'),
            limit,
        )

    def update_fact_text(self, fact_id: int, text: str) -> bool:
        cur = self.conn.execute(
            'UPDATE facts SET text = ?, updated_at = ? WHERE id = ?',
            (text.strip(), self.now(), fact_id),
        )
        self.conn.commit()
        updated = cur.rowcount > 0
        if updated:
            self.add_event('fact_edited', {'fact_id': fact_id})
        return updated

    def delete_fact(self, fact_id: int) -> bool:
        cur = self.conn.execute('DELETE FROM facts WHERE id = ?', (fact_id,))
        self.conn.commit()
        deleted = cur.rowcount > 0
        if deleted:
            self.add_event('fact_deleted', {'fact_id': fact_id})
        return deleted

    def clear_facts(self) -> int:
        cur = self.conn.execute('DELETE FROM facts')
        self.conn.execute("DELETE FROM sqlite_sequence WHERE name = 'facts'")
        self.conn.commit()
        deleted = cur.rowcount
        self.add_event('facts_cleared', {'count': deleted})
        return deleted

    def add_message(self, role: str, content: str, session_id: int = 1, language: str = '') -> None:
        language = target_language(language) if language else text_language(content)
        self.conn.execute(
            'INSERT INTO messages(session_id, role, content, language, created_at) VALUES (?, ?, ?, ?, ?)',
            (session_id, role, content, language, self.now()),
        )
        self.conn.commit()

    def recent_messages(self, limit: int = 16, session_id: int = 1) -> list[dict[str, str]]:
        rows = self.conn.execute(
            'SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?',
            (session_id, limit),
        ).fetchall()
        return [{'role': row['role'], 'content': row['content']} for row in reversed(rows)]

    def recent_message_rows(self, limit: int = 40, session_id: int = 1) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            'SELECT * FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?',
            (session_id, limit),
        ).fetchall()
        return list(reversed(rows))

    def clear_messages(self, session_id: int = 1) -> None:
        self.conn.execute('DELETE FROM messages WHERE session_id = ?', (session_id,))
        self.conn.commit()

    def clear_all_messages(self) -> int:
        cur = self.conn.execute('DELETE FROM messages')
        self.conn.execute("DELETE FROM sqlite_sequence WHERE name = 'messages'")
        self.conn.commit()
        deleted = cur.rowcount
        self.add_event('messages_cleared', {'count': deleted})
        return deleted

    def add_summary(self, text: str, kind: str = 'session', source_start_id: int = 0, source_end_id: int = 0, importance: int = 2, language: str = '') -> int:
        now = self.now()
        language = target_language(language) if language else text_language(text)
        cur = self.conn.execute(
            'INSERT INTO summaries(kind, text, source_start_id, source_end_id, importance, language, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (kind, text.strip(), int(source_start_id), int(source_end_id), int(importance), language, now, now),
        )
        self.conn.commit()
        summary_id = int(cur.lastrowid)
        self.add_event('summary_added', {'summary_id': summary_id, 'kind': kind})
        return summary_id

    def recent_summaries(self, limit: int = 12) -> list[sqlite3.Row]:
        return self.conn.execute(
            'SELECT * FROM summaries ORDER BY importance DESC, updated_at DESC LIMIT ?',
            (limit,),
        ).fetchall()

    def last_summary_source_end_id(self) -> int:
        row = self.conn.execute('SELECT MAX(source_end_id) AS value FROM summaries').fetchone()
        return int(row['value'] or 0)

    def get_translation(self, source_kind: str, source_id: int, source_text: str, language: str) -> str:
        row = self.conn.execute(
            'SELECT translated_text FROM translation_cache '
            'WHERE source_kind = ? AND source_id = ? AND source_hash = ? AND target_language = ?',
            (source_kind, int(source_id), source_hash(source_text), target_language(language)),
        ).fetchone()
        return str(row['translated_text']) if row else ''

    def set_translation(self, source_kind: str, source_id: int, source_text: str, language: str, translated_text: str) -> None:
        now = self.now()
        self.conn.execute(
            'INSERT INTO translation_cache(source_kind, source_id, source_hash, target_language, translated_text, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?) '
            'ON CONFLICT(source_kind, source_id, source_hash, target_language) DO UPDATE SET '
            'translated_text = excluded.translated_text, updated_at = excluded.updated_at',
            (source_kind, int(source_id), source_hash(source_text), target_language(language), translated_text.strip(), now, now),
        )
        self.conn.commit()

    def translation_cache_count(self) -> int:
        row = self.conn.execute('SELECT COUNT(*) AS value FROM translation_cache').fetchone()
        return int(row['value'] or 0)

    def add_token_usage(self, source: str, model: str = '', prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0, estimated_cost: float = 0.0) -> int:
        cur = self.conn.execute(
            'INSERT INTO token_usage(source, model, prompt_tokens, completion_tokens, total_tokens, estimated_cost, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (source, model, int(prompt_tokens), int(completion_tokens), int(total_tokens), float(estimated_cost), self.now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def token_usage_summary(self) -> dict[str, Any]:
        row = self.conn.execute(
            'SELECT COUNT(*) AS calls, SUM(prompt_tokens) AS prompt_tokens, SUM(completion_tokens) AS completion_tokens, '
            'SUM(total_tokens) AS total_tokens, SUM(estimated_cost) AS estimated_cost FROM token_usage'
        ).fetchone()
        return {
            'calls': int(row['calls'] or 0),
            'prompt_tokens': int(row['prompt_tokens'] or 0),
            'completion_tokens': int(row['completion_tokens'] or 0),
            'total_tokens': int(row['total_tokens'] or 0),
            'estimated_cost': float(row['estimated_cost'] or 0.0),
        }

    def clear_events(self) -> int:
        cur = self.conn.execute('DELETE FROM events')
        self.conn.execute("DELETE FROM sqlite_sequence WHERE name = 'events'")
        self.conn.commit()
        return cur.rowcount

    def reset_profile(self) -> None:
        self.conn.execute('DELETE FROM profile')
        for key, value in PROFILE_DEFAULTS.items():
            self.conn.execute('INSERT INTO profile(key, value) VALUES (?, ?)', (key, value))
        self.conn.commit()
        self.add_event('profile_reset', {'at': self.now()})

    def reset_database(self) -> None:
        self.conn.executescript(
            """
            DELETE FROM memories;
            DELETE FROM messages;
            DELETE FROM facts;
            DELETE FROM summaries;
            DELETE FROM token_usage;
            DELETE FROM events;
            DELETE FROM translation_cache;
            DELETE FROM profile;
            DELETE FROM sqlite_sequence WHERE name IN ('memories', 'messages', 'facts', 'summaries', 'token_usage', 'events', 'translation_cache');
            """
        )
        for key, value in PROFILE_DEFAULTS.items():
            self.conn.execute('INSERT INTO profile(key, value) VALUES (?, ?)', (key, value))
        self.conn.commit()
        self.add_event('database_reset', {'at': self.now()})

    def add_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.conn.execute(
            'INSERT INTO events(type, payload, created_at) VALUES (?, ?, ?)',
            (event_type, json.dumps(payload, ensure_ascii=False), self.now()),
        )
        self.conn.commit()

    def recent_events(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            'SELECT * FROM events ORDER BY id DESC LIMIT ?',
            (limit,),
        ).fetchall()

    def backup_database(self, backup_dir: str | Path | None = None, keep: int = 8) -> Path | None:
        if not self.path.exists():
            return None
        backup_root = Path(backup_dir) if backup_dir else self.path.parent / 'backups'
        backup_root.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
        target = backup_root / f'{self.path.stem}-{stamp}{self.path.suffix}'
        self.conn.commit()
        shutil.copy2(self.path, target)
        backups = sorted(
            backup_root.glob(f'{self.path.stem}-*{self.path.suffix}'),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for old in backups[max(0, int(keep)):]:
            try:
                old.unlink()
            except OSError:
                pass
        return target
