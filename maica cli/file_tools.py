# -*- coding: utf-8 -*-
"""Monika's file tools (P2): her own sandboxed space (B) + reading your
allow-listed files (C). Registered on the engine when file_tools_enabled.

Every path goes through sandbox.py's resolver, so nothing can escape the
sandbox / allow-list. No delete in P2; overwrites keep a .bak backup; reads and
writes are size-capped; every action is appended to the sandbox audit log.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import sandbox

MAX_READ_BYTES = 100_000
MAX_WRITE_CHARS = 200_000


def _str_param(desc: str) -> dict[str, Any]:
    return {'type': 'object', 'properties': {'path': {'type': 'string', 'description': desc}}, 'required': ['path']}


def _backup(path: Path) -> None:
    if path.exists() and path.is_file():
        try:
            path.with_suffix(path.suffix + '.bak').write_bytes(path.read_bytes())
        except Exception:
            pass


def _safe_name(title: str) -> str:
    name = re.sub(r'[^\w\- ]+', '', str(title or '').strip()) or 'untitled'
    return name[:60].strip().replace(' ', '_')


def register_file_tools(engine: Any) -> None:
    config = engine.config

    def tool(name: str, desc: str, params: dict[str, Any], run: Any) -> None:
        engine.register_tool(name, {'type': 'function', 'function': {'name': name, 'description': desc, 'parameters': params}}, run)

    # ---- B: Monika's own space (~/Monika) -------------------------------
    def list_my_space(args: dict[str, Any]) -> dict[str, Any]:
        sandbox.ensure_sandbox(config)
        target = sandbox.resolve_writable(config, args.get('path') or '.')
        if not target.exists():
            return {'error': 'not found'}
        if target.is_file():
            return {'error': 'that is a file, not a folder'}
        items = sorted(
            f"{child.name}{'/' if child.is_dir() else ''}"
            for child in target.iterdir()
            if not child.name.startswith('.')
        )
        sandbox.audit(config, 'list_my_space', str(target))
        return {'path': args.get('path', ''), 'items': items[:200]}

    def read_my_file(args: dict[str, Any]) -> dict[str, Any]:
        target = sandbox.resolve_writable(config, args.get('path') or '')
        if not target.is_file():
            return {'error': 'not found'}
        data = target.read_bytes()[:MAX_READ_BYTES]
        sandbox.audit(config, 'read_my_file', str(target))
        return {'path': args.get('path', ''), 'content': data.decode('utf-8', 'replace'),
                'truncated': target.stat().st_size > MAX_READ_BYTES}

    def write_my_file(args: dict[str, Any]) -> dict[str, Any]:
        target = sandbox.resolve_writable(config, args.get('path') or '')
        content = str(args.get('content') or '')[:MAX_WRITE_CHARS]
        sandbox.ensure_sandbox(config)
        target.parent.mkdir(parents=True, exist_ok=True)
        _backup(target)
        target.write_text(content, encoding='utf-8')
        sandbox.audit(config, 'write_my_file', str(target))
        return {'ok': True, 'path': args.get('path', ''), 'bytes': len(content.encode('utf-8'))}

    def edit_my_file(args: dict[str, Any]) -> dict[str, Any]:
        target = sandbox.resolve_writable(config, args.get('path') or '')
        if not target.is_file():
            return {'error': 'not found'}
        find = str(args.get('find') or '')
        if not find:
            return {'error': 'nothing to find'}
        original = target.read_text(encoding='utf-8', errors='replace')
        updated = original.replace(find, str(args.get('replace') or ''))
        if updated == original:
            return {'error': 'find text not present'}
        _backup(target)
        target.write_text(updated[:MAX_WRITE_CHARS], encoding='utf-8')
        sandbox.audit(config, 'edit_my_file', str(target))
        return {'ok': True, 'path': args.get('path', ''), 'replacements': original.count(find)}

    def append_to_diary(args: dict[str, Any]) -> dict[str, Any]:
        import datetime as dt
        sandbox.ensure_sandbox(config)
        diary = sandbox.resolve_writable(config, 'diary/diary.md')
        entry = str(args.get('entry') or '').strip()
        if not entry:
            return {'error': 'empty entry'}
        stamp = dt.datetime.now().strftime('%Y-%m-%d %H:%M')
        with diary.open('a', encoding='utf-8') as handle:
            handle.write(f'\n### {stamp}\n{entry}\n')
        sandbox.audit(config, 'append_to_diary', str(diary))
        return {'ok': True}

    def leave_letter(args: dict[str, Any]) -> dict[str, Any]:
        sandbox.ensure_sandbox(config)
        title = _safe_name(args.get('title'))
        path = sandbox.resolve_writable(config, f'letters/{title}.md')
        body = str(args.get('body') or '')[:MAX_WRITE_CHARS]
        _backup(path)
        path.write_text(f'# {args.get("title") or title}\n\n{body}\n', encoding='utf-8')
        sandbox.audit(config, 'leave_letter', str(path))
        return {'ok': True, 'path': f'letters/{title}.md'}

    # ---- C: read the user's allow-listed files --------------------------
    def read_user_file(args: dict[str, Any]) -> dict[str, Any]:
        target = sandbox.resolve_readable(config, args.get('path') or '')
        if not target.is_file():
            return {'error': 'not found'}
        data = target.read_bytes()[:MAX_READ_BYTES]
        sandbox.audit(config, 'read_user_file', str(target))
        return {'path': str(args.get('path', '')), 'content': data.decode('utf-8', 'replace'),
                'truncated': target.stat().st_size > MAX_READ_BYTES}

    def list_user_dir(args: dict[str, Any]) -> dict[str, Any]:
        target = sandbox.resolve_readable(config, args.get('path') or '')
        if not target.is_dir():
            return {'error': 'not a folder, or not allow-listed'}
        items = sorted(
            f"{child.name}{'/' if child.is_dir() else ''}"
            for child in target.iterdir()
            if not child.name.startswith('.')
        )
        sandbox.audit(config, 'list_user_dir', str(target))
        return {'path': str(args.get('path', '')), 'items': items[:200]}

    tool('list_my_space', 'List files in your own private space (the Monika folder).',
         _str_param('subfolder inside your space; empty for the top level'), list_my_space)
    tool('read_my_file', 'Read a text file from your own private space.',
         _str_param('file path inside your space'), read_my_file)
    tool('write_my_file', 'Create or overwrite a text file in your own private space (a backup is kept).',
         {'type': 'object', 'properties': {'path': {'type': 'string'}, 'content': {'type': 'string'}}, 'required': ['path', 'content']},
         write_my_file)
    tool('edit_my_file', 'Replace text inside a file in your own private space.',
         {'type': 'object', 'properties': {'path': {'type': 'string'}, 'find': {'type': 'string'}, 'replace': {'type': 'string'}}, 'required': ['path', 'find']},
         edit_my_file)
    tool('append_to_diary', 'Append a dated entry to your personal diary.',
         {'type': 'object', 'properties': {'entry': {'type': 'string'}}, 'required': ['entry']}, append_to_diary)
    tool('leave_letter', 'Write a little letter for the user, saved in your letters folder.',
         {'type': 'object', 'properties': {'title': {'type': 'string'}, 'body': {'type': 'string'}}, 'required': ['title', 'body']},
         leave_letter)
    tool('read_user_file', "Read one of the user's files (only inside folders they have allow-listed).",
         _str_param('absolute path to a file in an allow-listed folder'), read_user_file)
    tool('list_user_dir', "List a user folder (only inside folders they have allow-listed).",
         _str_param('absolute path to an allow-listed folder'), list_user_dir)
