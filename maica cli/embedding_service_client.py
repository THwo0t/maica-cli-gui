# -*- coding: utf-8 -*-
"""HTTP client for the optional out-of-process embedding service."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def service_base_url(config: dict[str, Any]) -> str:
    host = str(config.get("embedding_service_host") or "127.0.0.1").strip() or "127.0.0.1"
    port = int(config.get("embedding_service_port") or 8766)
    return f"http://{host}:{port}"


def service_timeout(config: dict[str, Any]) -> float:
    try:
        return float(config.get("embedding_service_timeout") or 8)
    except (TypeError, ValueError):
        return 8.0


def post_json(config: dict[str, Any], path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = service_base_url(config) + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=service_timeout(config)) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"embedding service request failed: {exc}") from exc
    result = json.loads(body)
    if not isinstance(result, dict):
        raise RuntimeError("embedding service returned a non-object response")
    if not result.get("ok", False):
        raise RuntimeError(str(result.get("error") or "embedding service returned ok=false"))
    return result


def search_service_examples(
    query_text: str,
    config: dict[str, Any],
    limit: int,
    min_score: float,
) -> list[dict[str, Any]]:
    result = post_json(
        config,
        "/search_examples",
        {"query": query_text, "limit": int(limit), "min_score": float(min_score)},
    )
    rows = result.get("results") or []
    return rows if isinstance(rows, list) else []


def search_service_memories(
    query_text: str,
    config: dict[str, Any],
    limit: int,
    min_score: float,
) -> list[dict[str, Any]]:
    result = post_json(
        config,
        "/search_memories",
        {"query": query_text, "limit": int(limit), "min_score": float(min_score)},
    )
    rows = result.get("results") or []
    return rows if isinstance(rows, list) else []


def build_service_memory_index(config: dict[str, Any]) -> dict[str, Any]:
    result = post_json(config, "/build_memories", {})
    payload = result.get("result")
    return payload if isinstance(payload, dict) else result
