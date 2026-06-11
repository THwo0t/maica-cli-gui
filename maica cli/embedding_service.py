#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Out-of-process FAISS retrieval service for MAICA GUI.

This process owns sentence-transformers/torch imports. The GUI talks to it over
localhost HTTP, so Qt can stay alive even if vector dependencies misbehave.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from config_defaults import DEFAULT_CONFIG
from config_io import load_json
from embedding_index import (
    check_memory_vector_ready,
    check_vector_ready,
    search_memory_vectors,
    search_vector_examples,
)
from store import Store


APP_DIR = Path(__file__).resolve().parent


def read_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length).decode("utf-8", errors="replace") if length else "{}"
    data = json.loads(raw or "{}")
    if not isinstance(data, dict):
        raise ValueError("request JSON must be an object")
    return data


class EmbeddingService(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], config_path: Path, db_path: Path) -> None:
        super().__init__(server_address, Handler)
        self.config_path = config_path
        self.db_path = db_path

    def load_config(self) -> dict[str, Any]:
        return load_json(self.config_path, DEFAULT_CONFIG)


class Handler(BaseHTTPRequestHandler):
    server: EmbeddingService

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_json({"ok": False, "error": "not found"}, 404)
            return
        try:
            config = self.server.load_config()
            store = Store(self.server.db_path)
            try:
                memory_report = check_memory_vector_ready(store, config)
            finally:
                store.close()
            self.send_json(
                {
                    "ok": True,
                    "service": "maica_embedding_service",
                    "vector": check_vector_ready(config),
                    "memory_vector": memory_report,
                }
            )
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 500)

    def do_POST(self) -> None:
        try:
            if self.path == "/search_examples":
                self.handle_search_examples()
            elif self.path == "/search_memories":
                self.handle_search_memories()
            else:
                self.send_json({"ok": False, "error": "not found"}, 404)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 500)

    def handle_search_examples(self) -> None:
        payload = read_body(self)
        config = self.server.load_config()
        query = str(payload.get("query") or "")
        limit = int(payload.get("limit") or config.get("embedding_top_k") or 30)
        min_score = float(payload.get("min_score") if payload.get("min_score") is not None else config.get("embedding_min_score", 0.55))
        results = search_vector_examples(query, config, limit=limit, min_score=min_score)
        self.send_json({"ok": True, "results": results})

    def handle_search_memories(self) -> None:
        payload = read_body(self)
        config = self.server.load_config()
        query = str(payload.get("query") or "")
        limit = int(payload.get("limit") or config.get("memory_embedding_top_k") or 8)
        min_score = float(payload.get("min_score") if payload.get("min_score") is not None else config.get("memory_embedding_min_score", 0.55))
        results = search_memory_vectors(query, config, limit=limit, min_score=min_score)
        self.send_json({"ok": True, "results": results})


def main() -> int:
    parser = argparse.ArgumentParser(description="MAICA embedding retrieval service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--config", default=str(APP_DIR / "config.json"))
    parser.add_argument("--db", default=str(APP_DIR / "maica_cli.db"))
    args = parser.parse_args()

    server = EmbeddingService(
        (args.host, args.port),
        Path(args.config).resolve(),
        Path(args.db).resolve(),
    )
    print(f"[embedding-service] listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
