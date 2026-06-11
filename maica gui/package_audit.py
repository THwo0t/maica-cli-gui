#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit packaged MAICA GUI output for private files and secret-like strings."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


BLOCKED_NAMES = {
    'config.json',
    'maica_cli.db',
}
BLOCKED_SUFFIXES = {
    '.db',
    '.faiss',
    '.safetensors',
}
BLOCKED_PARTS = {
    'logs',
    '.tts_cache',
    '.safe_test',
    'mas_raw',
    'Qwen3-Embedding-0.6B',
}
SECRET_PATTERNS = [
    re.compile(rb'sk-[A-Za-z0-9]{10,}'),
    re.compile(rb'cosyvoice-v3\.5-plus-bailian-[A-Za-z0-9-]+'),
    re.compile(rb'gho_[A-Za-z0-9_]+'),
]


def is_blocked_path(path: Path, root: Path) -> str:
    rel_parts = set(path.relative_to(root).parts)
    if path.name in BLOCKED_NAMES:
        return f'blocked name: {path.name}'
    if path.suffix in BLOCKED_SUFFIXES:
        return f'blocked suffix: {path.suffix}'
    overlap = BLOCKED_PARTS & rel_parts
    if overlap:
        return f'blocked path part: {sorted(overlap)[0]}'
    if path.name.endswith('_meta.jsonl'):
        return 'blocked vector metadata'
    return ''


def has_secret_like_content(path: Path) -> str:
    if path.stat().st_size > 5_000_000:
        return ''
    try:
        data = path.read_bytes()
    except OSError:
        return ''
    for pattern in SECRET_PATTERNS:
        if pattern.search(data):
            return f'secret-like pattern: {pattern.pattern.decode(errors="replace")}'
    return ''


def audit(root: Path) -> list[str]:
    findings: list[str] = []
    if not root.exists():
        return [f'missing path: {root}']
    for path in root.rglob('*'):
        if not path.is_file():
            continue
        reason = is_blocked_path(path, root)
        if reason:
            findings.append(f'{path}: {reason}')
            continue
        reason = has_secret_like_content(path)
        if reason:
            findings.append(f'{path}: {reason}')
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description='Audit MAICA GUI package output')
    parser.add_argument('path', nargs='?', default='dist/maica-gui')
    args = parser.parse_args()
    root = Path(args.path).resolve()
    findings = audit(root)
    if findings:
        print('package audit failed:')
        for finding in findings:
            print(f'- {finding}')
        return 1
    print(f'package audit ok: {root}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
