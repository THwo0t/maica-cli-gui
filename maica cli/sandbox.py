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
        raise PermissionError('path escapes the sandbox')
    return resolved


def resolve_readable(config: dict[str, Any], user_path: str) -> Path:
    """Resolve a path that must be inside the sandbox or an allow-listed dir."""
    root = sandbox_root(config)
    resolved = _resolve(root, user_path)
    for allowed in [root, *readonly_roots(config)]:
        if _within(resolved, allowed):
            return resolved
    raise PermissionError('path is not inside the sandbox or an allow-listed directory')


def audit(config: dict[str, Any], action: str, detail: str) -> None:
    try:
        root = sandbox_root(config)
        root.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().isoformat(timespec='seconds')
        with (root / AUDIT_FILE).open('a', encoding='utf-8') as handle:
            handle.write(f'{stamp}\t{action}\t{detail}\n')
    except Exception:
        pass
