# -*- coding: utf-8 -*-
"""Safe user-data export/import helpers."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from store import Store


EXPORT_TABLES = ('profile', 'memories', 'facts', 'summaries', 'events', 'translation_cache')


def _rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    rows = conn.execute(f'SELECT * FROM {table} ORDER BY 1 ASC').fetchall()
    return [dict(row) for row in rows]


def export_user_data(store: Store, target_zip: str | Path, include_events: bool = True) -> dict[str, Any]:
    target = Path(target_zip)
    target.parent.mkdir(parents=True, exist_ok=True)
    store.conn.commit()
    tables = list(EXPORT_TABLES)
    if not include_events:
        tables.remove('events')
    payload = {
        'format': 'maica-cli-gui-user-data',
        'version': 1,
        'exported_at': dt.datetime.now().isoformat(timespec='seconds'),
        'tables': {table: _rows(store.conn, table) for table in tables},
    }
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps({'format': payload['format'], 'version': payload['version'], 'exported_at': payload['exported_at']}, ensure_ascii=False, indent=2))
        archive.writestr('user_data.json', json.dumps(payload, ensure_ascii=False, indent=2))
    return {'ok': True, 'path': str(target), 'tables': {table: len(payload['tables'][table]) for table in payload['tables']}}


def preview_user_data(source_zip: str | Path) -> dict[str, Any]:
    source = Path(source_zip)
    with zipfile.ZipFile(source, 'r') as archive:
        with archive.open('user_data.json') as handle:
            payload = json.loads(handle.read().decode('utf-8'))
    tables = payload.get('tables') if isinstance(payload.get('tables'), dict) else {}
    return {
        'ok': True,
        'format': payload.get('format'),
        'version': payload.get('version'),
        'exported_at': payload.get('exported_at'),
        'tables': {name: len(rows) for name, rows in tables.items() if isinstance(rows, list)},
    }


def import_user_data(store: Store, source_zip: str | Path, replace: bool = False) -> dict[str, Any]:
    source = Path(source_zip)
    backup = store.backup_database(keep=12)
    with zipfile.ZipFile(source, 'r') as archive:
        with archive.open('user_data.json') as handle:
            payload = json.loads(handle.read().decode('utf-8'))
    if payload.get('format') != 'maica-cli-gui-user-data':
        raise ValueError('Unsupported export format')
    tables = payload.get('tables') if isinstance(payload.get('tables'), dict) else {}
    counts: dict[str, int] = {}
    conn = store.conn
    if replace:
        for table in EXPORT_TABLES:
            conn.execute(f'DELETE FROM {table}')
    for item in tables.get('profile', []):
        if isinstance(item, dict) and item.get('key') is not None:
            conn.execute(
                'INSERT INTO profile(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                (str(item.get('key')), str(item.get('value') or '')),
            )
            counts['profile'] = counts.get('profile', 0) + 1
    _append_rows(conn, 'memories', tables.get('memories', []), ('text', 'tags', 'importance', 'language', 'created_at', 'updated_at'), counts)
    _append_rows(conn, 'facts', tables.get('facts', []), ('category', 'text', 'source', 'importance', 'language', 'created_at', 'updated_at'), counts)
    _append_rows(conn, 'summaries', tables.get('summaries', []), ('kind', 'text', 'source_start_id', 'source_end_id', 'importance', 'language', 'created_at', 'updated_at'), counts)
    _append_rows(conn, 'events', tables.get('events', []), ('type', 'payload', 'created_at'), counts)
    _append_rows(
        conn,
        'translation_cache',
        tables.get('translation_cache', []),
        ('source_kind', 'source_id', 'source_hash', 'target_language', 'translated_text', 'created_at', 'updated_at'),
        counts,
    )
    conn.commit()
    store.add_event('user_data_imported', {'source': str(source), 'replace': replace, 'backup': str(backup) if backup else ''})
    return {'ok': True, 'backup': str(backup) if backup else '', 'counts': counts}


def _append_rows(conn: sqlite3.Connection, table: str, rows: Any, columns: tuple[str, ...], counts: dict[str, int]) -> None:
    if not isinstance(rows, list):
        return
    placeholders = ', '.join('?' for _ in columns)
    column_text = ', '.join(columns)
    for item in rows:
        if not isinstance(item, dict):
            continue
        values = [item.get(column) for column in columns]
        conn.execute(f'INSERT INTO {table}({column_text}) VALUES ({placeholders})', values)
        counts[table] = counts.get(table, 0) + 1
