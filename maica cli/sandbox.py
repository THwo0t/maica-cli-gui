# -*- coding: utf-8 -*-
"""Filesystem sandbox for Monika's tools.

Cross-platform code-layer guard (the primary protection on every OS): every
path is resolved (following symlinks and ``..``) and must land inside an
allowed root. Writable = the sandbox root only; readable = the sandbox plus any
explicitly allow-listed directories. On Linux an OS-layer (bubblewrap) can wrap
future shell tools; pure-Python file tools rely on this guard.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

DEFAULT_SUBDIRS = ('letters', 'diary', 'notes')
AUDIT_FILE = '.audit.log'


def sandbox_root(config: dict[str, Any]) -> Path:
    raw = str(config.get('sandbox_root') or '').strip()
    root = Path(raw).expanduser() if raw else (Path.home() / 'Monika')
    return root.resolve(strict=False)


def readonly_roots(config: dict[str, Any]) -> list[Path]:
    roots: list[Path] = []
    for item in config.get('sandbox_readonly_allowlist') or []:
        try:
            path = Path(str(item)).expanduser().resolve(strict=False)
        except Exception:
            continue
        if path.exists():
            roots.append(path)
    return roots


def permission_info(config: dict[str, Any]) -> dict[str, Any]:
    """Return the effective filesystem boundary exposed to Monika's tools."""
    root = sandbox_root(config)
    return {
        'logical_root': 'sandbox://',
        'writable_root': str(root),
        'readonly_roots': [str(path) for path in readonly_roots(config)],
        'write_policy': 'sandbox_only',
        'external_read_policy': 'explicit_allowlist_only',
        'external_write_allowed': False,
    }


def ensure_sandbox(config: dict[str, Any]) -> Path:
    root = sandbox_root(config)
    root.mkdir(parents=True, exist_ok=True)
    for sub in DEFAULT_SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve(base: Path, user_path: str) -> Path:
    candidate = Path(str(user_path or ''))
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve(strict=False)


def resolve_writable(config: dict[str, Any], user_path: str) -> Path:
    """Resolve a path that must stay inside the writable sandbox root."""
    root = sandbox_root(config)
    resolved = _resolve(root, user_path)
    if not _within(resolved, root):
        audit(config, 'deny_write', f'requested={user_path!r}; resolved={resolved}')
        raise PermissionError(
            'write denied: the path is outside the writable sandbox; '
            'use get_file_space_info to confirm the real sandbox path'
        )
    return resolved


def resolve_readable(config: dict[str, Any], user_path: str) -> Path:
    """Resolve a path that must be inside the sandbox or an allow-listed dir."""
    root = sandbox_root(config)
    resolved = _resolve(root, user_path)
    for allowed in [root, *readonly_roots(config)]:
        if _within(resolved, allowed):
            return resolved
    audit(config, 'deny_read', f'requested={user_path!r}; resolved={resolved}')
    raise PermissionError(
        'read denied: the path is outside the sandbox and is not recorded in '
        'the read-only allowlist; use get_file_space_info to inspect the boundary'
    )


def audit(config: dict[str, Any], action: str, detail: str) -> None:
    try:
        root = sandbox_root(config)
        root.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().isoformat(timespec='seconds')
        safe_action = str(action).replace('\t', ' ').replace('\r', ' ').replace('\n', ' ')
        safe_detail = str(detail).replace('\r', '\\r').replace('\n', '\\n')
        with (root / AUDIT_FILE).open('a', encoding='utf-8') as handle:
            handle.write(f'{stamp}\t{safe_action}\t{safe_detail}\n')
    except Exception:
        pass
