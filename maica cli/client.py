# -*- coding: utf-8 -*-
"""OpenAI-compatible Chat Completions client."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


class OpenAICompatibleClient:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def endpoint(self) -> str:
        api_base = str(self.config.get("api_base") or "").rstrip("/")
        if api_base.endswith("/chat/completions"):
            return api_base
        return api_base + "/chat/completions"

    def chat(self, messages: list[dict[str, str]], overrides: dict[str, Any] | None = None) -> str:
        api_key = os.environ.get("MAICA_CLI_API_KEY") or str(self.config.get("api_key") or "")
        if not api_key and bool(self.config.get("api_key_required", True)):
            raise RuntimeError("api_key is empty. Edit config.json or set MAICA_CLI_API_KEY.")

        body = {
            "model": self.config.get("model"),
            "messages": messages,
            "temperature": float(self.config.get("temperature", 0.22)),
            "top_p": float(self.config.get("top_p", 0.7)),
            "max_tokens": int(self.config.get("max_tokens", 900)),
            "frequency_penalty": float(self.config.get("frequency_penalty", 0.44)),
            "presence_penalty": float(self.config.get("presence_penalty", 0.34)),
            "stream": False,
        }
        if overrides:
            body.update(overrides)

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = "Bearer " + api_key

        request = urllib.request.Request(
            self.endpoint(),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=int(self.config.get("request_timeout", 120))) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc

        payload = json.loads(raw)
        return str(payload["choices"][0]["message"]["content"]).strip()
