# -*- coding: utf-8 -*-
"""Safe Cubism 4 model validation and ZIP import helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


MAX_ARCHIVE_FILES = 4096
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024


class Live2DModelError(ValueError):
    pass


@dataclass
class Live2DModelReport:
    ok: bool
    root: str = ''
    entry_point: str = ''
    model_name: str = ''
    file_count: int = 0
    referenced_files: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_model_library() -> Path:
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    else:
        base = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
    return base / 'maica-gui' / 'live2d-models'


def validate_cubism_core(path: str | Path | None) -> tuple[bool, str]:
    candidate = Path(str(path or '')).expanduser()
    if not str(path or '').strip():
        return False, 'Cubism Core path is empty'
    if not candidate.is_file():
        return False, 'Cubism Core JavaScript file was not found'
    if candidate.suffix.lower() != '.js':
        return False, 'Cubism Core must be a JavaScript file'
    try:
        sample = candidate.read_bytes()[:512 * 1024]
    except OSError as exc:
        return False, f'Cubism Core cannot be read: {exc}'
    if b'Live2DCubismCore' not in sample and b'live2dcubismcore' not in candidate.name.lower().encode('ascii', 'ignore'):
        return False, 'The selected file does not look like Cubism Core'
    return True, ''


def validate_live2d_model(source: str | Path) -> Live2DModelReport:
    source_path = Path(source).expanduser().resolve(strict=False)
    if source_path.is_file() and source_path.name.lower().endswith('.model3.json'):
        root = source_path.parent
        entries = [source_path]
    elif source_path.is_dir():
        root = source_path
        entries = sorted(source_path.rglob('*.model3.json'))
    else:
        return Live2DModelReport(False, errors=['Model directory or .model3.json file was not found'])

    report = Live2DModelReport(
        ok=False,
        root=str(root),
        file_count=sum(1 for item in root.rglob('*') if item.is_file()),
    )
    if not entries:
        report.errors.append('No .model3.json entry point was found')
        return report
    if len(entries) > 1:
        report.warnings.append(f'Multiple model entry points found; using {entries[0].name}')
    entry = entries[0]
    report.entry_point = str(entry)
    report.model_name = entry.name.removesuffix('.model3.json')

    try:
        settings = json.loads(entry.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        report.errors.append(f'Invalid model settings JSON: {exc}')
        return report
    if not isinstance(settings, dict):
        report.errors.append('Model settings must be a JSON object')
        return report
    if int(settings.get('Version') or 0) != 3:
        report.errors.append('Only Cubism 4 .model3.json (Version 3) is supported')

    references = _collect_references(settings.get('FileReferences'))
    report.referenced_files = len(references)
    moc_paths: list[Path] = []
    for kind, reference in references:
        try:
            resolved = _resolve_reference(entry.parent, root, reference)
        except Live2DModelError as exc:
            report.errors.append(f'Unsafe {kind} reference {reference!r}: {exc}')
            continue
        if not resolved.is_file():
            report.errors.append(f'Missing {kind} file: {reference}')
            continue
        if kind == 'Moc':
            moc_paths.append(resolved)

    if not moc_paths:
        report.errors.append('FileReferences.Moc must point to a .moc3 file')
    else:
        moc = moc_paths[0]
        if moc.suffix.lower() != '.moc3':
            report.errors.append('The Moc reference is not a .moc3 file')
        else:
            try:
                if moc.read_bytes()[:4] != b'MOC3':
                    report.errors.append('The .moc3 file has an invalid MOC3 header')
            except OSError as exc:
                report.errors.append(f'The .moc3 file cannot be read: {exc}')

    textures = [reference for kind, reference in references if kind == 'Texture']
    if not textures:
        report.errors.append('At least one texture is required')
    report.ok = not report.errors
    return report


def import_live2d_zip(
    archive: str | Path,
    destination_root: str | Path | None = None,
) -> Live2DModelReport:
    archive_path = Path(archive).expanduser().resolve(strict=True)
    if not archive_path.is_file() or not zipfile.is_zipfile(archive_path):
        raise Live2DModelError('The selected file is not a valid ZIP archive')
    destination = Path(destination_root or default_model_library()).expanduser().resolve(strict=False)
    destination.mkdir(parents=True, exist_ok=True)
    digest = _sha256_file(archive_path)[:10]
    slug = re.sub(r'[^A-Za-z0-9._-]+', '-', archive_path.stem).strip('-._') or 'model'
    final_root = destination / f'{slug}-{digest}'
    if final_root.exists():
        report = validate_live2d_model(final_root)
        if report.ok:
            return report
        raise Live2DModelError('An existing imported model with the same archive hash is invalid')

    staging = destination / f'.import-{uuid.uuid4().hex}'
    staging.mkdir(parents=False, exist_ok=False)
    try:
        with zipfile.ZipFile(archive_path) as package:
            entries = package.infolist()
            if len(entries) > MAX_ARCHIVE_FILES:
                raise Live2DModelError(f'Archive contains more than {MAX_ARCHIVE_FILES} entries')
            total_size = sum(max(0, int(item.file_size)) for item in entries if not item.is_dir())
            if total_size > MAX_ARCHIVE_BYTES:
                raise Live2DModelError('Archive expands beyond the 512 MB safety limit')
            seen_paths: set[str] = set()
            for item in entries:
                relative = _safe_archive_path(item)
                if relative is None:
                    continue
                folded = relative.as_posix().casefold()
                if folded in seen_paths:
                    raise Live2DModelError(f'Duplicate archive path: {item.filename}')
                seen_paths.add(folded)
                target = (staging / Path(*relative.parts)).resolve(strict=False)
                if not _is_within(target, staging):
                    raise Live2DModelError(f'Archive entry escapes its destination: {item.filename}')
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(item) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)

        report = validate_live2d_model(staging)
        if not report.ok:
            raise Live2DModelError('; '.join(report.errors[:5]))
        staging.rename(final_root)
        return validate_live2d_model(final_root)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _safe_archive_path(item: zipfile.ZipInfo) -> PurePosixPath | None:
    raw = item.filename.replace('\\', '/')
    path = PurePosixPath(raw)
    if not raw or raw.startswith('/') or path.is_absolute() or '..' in path.parts:
        raise Live2DModelError(f'Unsafe archive path: {item.filename}')
    if path.parts and re.match(r'^[A-Za-z]:$', path.parts[0]):
        raise Live2DModelError(f'Windows drive path is not allowed: {item.filename}')
    if item.flag_bits & 0x1:
        raise Live2DModelError('Encrypted ZIP entries are not supported')
    mode = (item.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    if file_type == stat.S_IFLNK:
        raise Live2DModelError(f'Symbolic links are not allowed: {item.filename}')
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise Live2DModelError(f'Special archive entries are not allowed: {item.filename}')
    if any(part in {'', '.', '__MACOSX'} or part.startswith('._') for part in path.parts):
        return None
    return path


def _collect_references(raw: Any) -> list[tuple[str, str]]:
    refs = raw if isinstance(raw, dict) else {}
    output: list[tuple[str, str]] = []

    def add(kind: str, value: Any) -> None:
        if isinstance(value, str) and value.strip():
            output.append((kind, value.strip()))

    add('Moc', refs.get('Moc'))
    for texture in refs.get('Textures') or []:
        add('Texture', texture)
    for key in ('Physics', 'Pose', 'DisplayInfo', 'UserData'):
        add(key, refs.get(key))
    for expression in refs.get('Expressions') or []:
        add('Expression', expression.get('File') if isinstance(expression, dict) else expression)
    motions = refs.get('Motions') if isinstance(refs.get('Motions'), dict) else {}
    for group in motions.values():
        if not isinstance(group, list):
            continue
        for motion in group:
            add('Motion', motion.get('File') if isinstance(motion, dict) else motion)
            if isinstance(motion, dict):
                add('MotionSound', motion.get('Sound'))
    return output


def _resolve_reference(base: Path, root: Path, reference: str) -> Path:
    if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', reference) or reference.startswith(('/', '\\')):
        raise Live2DModelError('absolute paths and URLs are not allowed')
    parts = PurePosixPath(reference.replace('\\', '/'))
    if '..' in parts.parts:
        raise Live2DModelError('parent traversal is not allowed')
    target = (base / Path(*parts.parts)).resolve(strict=False)
    if not _is_within(target, root):
        raise Live2DModelError('reference leaves the model directory')
    return target


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()
