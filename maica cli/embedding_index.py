# -*- coding: utf-8 -*-
"""Optional embedding/FAISS retrieval layer for dialogue examples."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import warnings
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent


OPTIONAL_MODULES = {
    "numpy": "numpy",
    "torch": "torch",
    "transformers": "transformers",
    "sentence_transformers": "sentence-transformers",
    "faiss": "faiss-cpu",
}

_MODEL_CACHE: dict[tuple[str, str], Any] = {}
_INDEX_CACHE: dict[tuple[str, str, float, float], tuple[Any, list[dict[str, Any]]]] = {}


def resolve_app_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (APP_DIR / path).resolve()


def module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _ensure_vector_dependencies() -> None:
    missing = [
        package
        for module, package in OPTIONAL_MODULES.items()
        if not module_available(module)
    ]
    if missing:
        raise RuntimeError(
            "Missing vector dependencies: "
            + ", ".join(missing)
            + ". Run: py -3.13 -m pip install -r requirements.txt"
        )


def _config_paths(config: dict[str, Any]) -> tuple[Path, Path, Path]:
    model_path = resolve_app_path(config.get("embedding_model_path", "../Qwen3-Embedding-0.6B"))
    index_path = resolve_app_path(config.get("embedding_index_path", "data/example_vectors.faiss"))
    meta_path = resolve_app_path(config.get("embedding_meta_path", "data/example_vectors_meta.jsonl"))
    return model_path, index_path, meta_path


def _memory_paths(config: dict[str, Any]) -> tuple[Path, Path, Path]:
    model_path = resolve_app_path(config.get("embedding_model_path", "../Qwen3-Embedding-0.6B"))
    index_path = resolve_app_path(config.get("memory_embedding_index_path", "data/memory_vectors.faiss"))
    meta_path = resolve_app_path(config.get("memory_embedding_meta_path", "data/memory_vectors_meta.jsonl"))
    return model_path, index_path, meta_path


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_embedding_model(config: dict[str, Any]) -> Any:
    _ensure_vector_dependencies()
    from sentence_transformers import SentenceTransformer

    model_path, _index_path, _meta_path = _config_paths(config)
    device = str(config.get("embedding_device") or "cpu")
    cache_key = (str(model_path), device)
    if cache_key not in _MODEL_CACHE:
        _MODEL_CACHE[cache_key] = SentenceTransformer(str(model_path), device=device)
    return _MODEL_CACHE[cache_key]


def prewarm_embedding_model(config: dict[str, Any], quiet: bool = True) -> dict[str, Any]:
    """Load the embedding model early so the first GUI chat does not pay the cost."""
    try:
        if quiet:
            with open(os.devnull, "w", encoding="utf-8") as sink:
                with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                    model = _load_embedding_model(config)
        else:
            model = _load_embedding_model(config)
        dimension_getter = getattr(model, "get_embedding_dimension", None)
        if dimension_getter is None:
            dimension_getter = getattr(model, "get_sentence_embedding_dimension", None)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            dimension = dimension_getter() if dimension_getter is not None else None
        return {"ok": True, "dimension": dimension, "error": ""}
    except Exception as exc:
        return {"ok": False, "dimension": None, "error": str(exc)}


def _example_paths(config: dict[str, Any]) -> list[Path]:
    core_paths = _as_list(config.get("example_bank_core_paths"))
    extra_paths = _as_list(config.get("example_bank_paths") or ["data/dialogue_examples_maica_cleaned.jsonl"])
    return [resolve_app_path(path) for path in [*core_paths, *extra_paths]]


def _load_examples_for_index(config: dict[str, Any]) -> list[dict[str, Any]]:
    from example_bank import build_retrieval_text

    min_quality = _safe_int(config.get("example_bank_min_quality", 4), 4)
    max_length = max(1, _safe_int(config.get("example_bank_max_assistant_length", 220), 220))
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for path in _example_paths(config):
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                user = str(item.get("user") or "").strip()
                assistant = str(item.get("assistant") or "").strip()
                if not user or not assistant:
                    continue
                if _safe_int(item.get("quality"), 0) < min_quality:
                    continue
                if len(assistant) > max_length:
                    continue
                source = str(item.get("source") or path.name)
                key = (user, assistant, source)
                if key in seen:
                    continue
                seen.add(key)
                row = dict(item)
                row["_source_path"] = str(path)
                row["_source_line"] = line_number
                row["retrieval_text"] = str(row.get("retrieval_text") or "").strip() or build_retrieval_text(row)
                rows.append(row)
    return rows


def _encode_texts(texts: list[str], config: dict[str, Any], show_progress: bool = False) -> Any:
    model = _load_embedding_model(config)
    batch_size = max(1, _safe_int(config.get("embedding_batch_size", 8), 8))
    return model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=show_progress,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )


def check_vector_ready(config: dict[str, Any]) -> dict[str, Any]:
    """Return a non-invasive readiness report for v0.7 vector retrieval."""
    modules = {
        module: module_available(module)
        for module in OPTIONAL_MODULES
    }
    missing_packages = [
        package
        for module, package in OPTIONAL_MODULES.items()
        if not modules[module]
    ]

    model_path, index_path, meta_path = _config_paths(config)

    model_files = {
        "config.json": (model_path / "config.json").exists(),
        "model.safetensors": (model_path / "model.safetensors").exists(),
        "tokenizer.json": (model_path / "tokenizer.json").exists(),
        "modules.json": (model_path / "modules.json").exists(),
    }

    example_paths = _example_paths(config)
    example_files = [
        {
            "path": str(path),
            "exists": path.exists(),
            "size": path.stat().st_size if path.exists() else 0,
        }
        for path in example_paths
    ]

    ready = (
        not missing_packages
        and model_path.exists()
        and all(model_files.values())
        and any(item["exists"] and item["size"] > 0 for item in example_files)
    )

    return {
        "ready_for_vector_build": ready,
        "embedding_enabled": bool(config.get("embedding_enabled", False)),
        "missing_packages": missing_packages,
        "install_command": "py -3.13 -m pip install -r requirements.txt",
        "modules": modules,
        "model_path": str(model_path),
        "model_files": model_files,
        "index_path": str(index_path),
        "index_exists": index_path.exists(),
        "meta_path": str(meta_path),
        "meta_exists": meta_path.exists(),
        "example_files": example_files,
        "next_step": "Run /vector build to create the FAISS index, then /vector on to use it in chat.",
    }


def print_vector_report(config: dict[str, Any]) -> None:
    print(json.dumps(check_vector_ready(config), ensure_ascii=False, indent=2))


def build_vector_index(config: dict[str, Any]) -> dict[str, Any]:
    """Build a cosine-similarity FAISS index for dialogue examples."""
    report = check_vector_ready(config)
    if not report["ready_for_vector_build"]:
        raise RuntimeError(json.dumps(report, ensure_ascii=False, indent=2))

    import faiss

    _model_path, index_path, meta_path = _config_paths(config)
    examples = _load_examples_for_index(config)
    if not examples:
        raise RuntimeError("No indexable dialogue examples found.")

    texts = [str(item.get("retrieval_text") or "") for item in examples]
    embeddings = _encode_texts(texts, config, show_progress=True).astype("float32")
    dimension = int(embeddings.shape[1])
    index = faiss.IndexFlatIP(dimension)
    index.add(embeddings)

    index_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(index_path))
    with meta_path.open("w", encoding="utf-8") as handle:
        for item in examples:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    _INDEX_CACHE.clear()
    return {
        "built": True,
        "count": len(examples),
        "dimension": dimension,
        "index_path": str(index_path),
        "meta_path": str(meta_path),
    }


def _load_index(config: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    _ensure_vector_dependencies()
    import faiss

    _model_path, index_path, meta_path = _config_paths(config)
    if not index_path.exists() or not meta_path.exists():
        raise FileNotFoundError("Vector index not found. Run /vector build first.")
    cache_key = (
        str(index_path),
        str(meta_path),
        index_path.stat().st_mtime,
        meta_path.stat().st_mtime,
    )
    if cache_key not in _INDEX_CACHE:
        index = faiss.read_index(str(index_path))
        rows: list[dict[str, Any]] = []
        with meta_path.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
        _INDEX_CACHE.clear()
        _INDEX_CACHE[cache_key] = (index, rows)
    return _INDEX_CACHE[cache_key]


def _load_index_from_paths(index_path: Path, meta_path: Path) -> tuple[Any, list[dict[str, Any]]]:
    _ensure_vector_dependencies()
    import faiss

    if not index_path.exists() or not meta_path.exists():
        raise FileNotFoundError(f"Vector index not found: {index_path}")
    cache_key = (
        str(index_path),
        str(meta_path),
        index_path.stat().st_mtime,
        meta_path.stat().st_mtime,
    )
    if cache_key not in _INDEX_CACHE:
        index = faiss.read_index(str(index_path))
        rows: list[dict[str, Any]] = []
        with meta_path.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
        _INDEX_CACHE.clear()
        _INDEX_CACHE[cache_key] = (index, rows)
    return _INDEX_CACHE[cache_key]


def search_vector_examples(
    query_text: str,
    config: dict[str, Any],
    limit: int | None = None,
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    """Search indexed examples by semantic similarity.

    Scores are cosine-like values because embeddings are normalized and the
    FAISS index uses inner product.
    """
    if not str(query_text or "").strip():
        return []
    index, rows = _load_index(config)
    if not rows:
        return []
    top_k = limit if limit is not None else _safe_int(config.get("embedding_top_k", 30), 30)
    top_k = max(1, min(int(top_k), len(rows)))
    threshold = _safe_float(
        config.get("embedding_min_score", 0.55) if min_score is None else min_score,
        0.55,
    )
    query_embedding = _encode_texts([query_text], config, show_progress=False).astype("float32")
    scores, indices = index.search(query_embedding, top_k)
    results: list[dict[str, Any]] = []
    for score, raw_index in zip(scores[0].tolist(), indices[0].tolist()):
        if raw_index < 0 or raw_index >= len(rows):
            continue
        score = float(score)
        if score < threshold:
            continue
        item = dict(rows[raw_index])
        item["_vector_score"] = round(score, 6)
        item["_vector_rank"] = len(results) + 1
        results.append(item)
    return results


def _memory_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "text": str(row["text"] or ""),
        "tags": str(row["tags"] or ""),
        "importance": int(row["importance"] or 1),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def build_memory_retrieval_text(memory: dict[str, Any]) -> str:
    parts = [
        f"memory_id: {memory.get('id')}",
        f"memory_text: {memory.get('text', '')}",
        f"tags: {memory.get('tags', '')}",
        f"importance: {memory.get('importance', 1)}",
    ]
    return "; ".join(part for part in parts if str(part).strip())


def check_memory_vector_ready(store: Any, config: dict[str, Any]) -> dict[str, Any]:
    report = check_vector_ready(config)
    _model_path, index_path, meta_path = _memory_paths(config)
    try:
        memory_count = len(store.all_memories())
    except Exception:
        memory_count = 0
    return {
        "ready_for_memory_vector_build": bool(report["ready_for_vector_build"] and memory_count > 0),
        "memory_embedding_enabled": bool(config.get("memory_embedding_enabled", False)),
        "missing_packages": report["missing_packages"],
        "model_path": report["model_path"],
        "memory_count": memory_count,
        "index_path": str(index_path),
        "index_exists": index_path.exists(),
        "meta_path": str(meta_path),
        "meta_exists": meta_path.exists(),
        "next_step": "Run /memory vector build, then /memory vector search <text>.",
    }


def build_memory_vector_index(store: Any, config: dict[str, Any]) -> dict[str, Any]:
    report = check_memory_vector_ready(store, config)
    if not report["ready_for_memory_vector_build"]:
        raise RuntimeError(json.dumps(report, ensure_ascii=False, indent=2))

    import faiss

    _model_path, index_path, meta_path = _memory_paths(config)
    memories = [_memory_row_to_dict(row) for row in store.all_memories()]
    texts = [build_memory_retrieval_text(item) for item in memories]
    embeddings = _encode_texts(texts, config, show_progress=True).astype("float32")
    dimension = int(embeddings.shape[1])
    index = faiss.IndexFlatIP(dimension)
    index.add(embeddings)

    index_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(index_path))
    with meta_path.open("w", encoding="utf-8") as handle:
        for item in memories:
            item = dict(item)
            item["retrieval_text"] = build_memory_retrieval_text(item)
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    _INDEX_CACHE.clear()
    return {
        "built": True,
        "count": len(memories),
        "dimension": dimension,
        "index_path": str(index_path),
        "meta_path": str(meta_path),
    }


def search_memory_vectors(
    query_text: str,
    config: dict[str, Any],
    limit: int | None = None,
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    if not str(query_text or "").strip():
        return []
    _model_path, index_path, meta_path = _memory_paths(config)
    index, rows = _load_index_from_paths(index_path, meta_path)
    if not rows:
        return []
    top_k = limit if limit is not None else _safe_int(config.get("memory_embedding_top_k", 8), 8)
    top_k = max(1, min(int(top_k), len(rows)))
    threshold = _safe_float(
        config.get("memory_embedding_min_score", 0.55) if min_score is None else min_score,
        0.55,
    )
    query_embedding = _encode_texts([query_text], config, show_progress=False).astype("float32")
    scores, indices = index.search(query_embedding, top_k)
    results: list[dict[str, Any]] = []
    for score, raw_index in zip(scores[0].tolist(), indices[0].tolist()):
        if raw_index < 0 or raw_index >= len(rows):
            continue
        score = float(score)
        if score < threshold:
            continue
        item = dict(rows[raw_index])
        item["_vector_score"] = round(score, 6)
        item["_vector_rank"] = len(results) + 1
        results.append(item)
    return results
