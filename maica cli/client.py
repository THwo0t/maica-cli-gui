# -*- coding: utf-8 -*-
"""OpenAI-compatible Chat Completions client."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Iterator

from text_utils import redact_secret


class OpenAICompatibleClient:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def endpoint(self) -> str:
        api_base = str(self.config.get("api_base") or "").rstrip("/")
        if api_base.endswith("/chat/completions"):
            return api_base
        return api_base + "/chat/completions"

    def _body(self, messages: list[dict[str, str]], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        body = {
            "model": self.config.get("model"),
            "messages": messages,
            "temperature": float(self.config.get("temperature", 0.75)),
            "top_p": float(self.config.get("top_p", 0.95)),
            "max_tokens": int(self.config.get("max_tokens", 900)),
            "frequency_penalty": float(self.config.get("frequency_penalty", 0.12)),
            "presence_penalty": float(self.config.get("presence_penalty", 0.08)),
            "stream": False,
        }
        if overrides:
            body.update(overrides)
        return body

    def _headers(self) -> dict[str, str]:
        api_key = os.environ.get("MAICA_CLI_API_KEY") or str(self.config.get("api_key") or "")
        if not api_key and bool(self.config.get("api_key_required", True)):
            raise RuntimeError("api_key is empty. Edit config.json or set MAICA_CLI_API_KEY.")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        return headers

    def _request(self, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.endpoint(),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        timeout = int(self.config.get("request_timeout", 120))
        attempts = max(1, int(self.config.get("request_retries", 3)))
        api_key = os.environ.get("MAICA_CLI_API_KEY") or str(self.config.get("api_key") or "")
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                # A real API response (4xx/5xx); do not retry — surface it.
                detail = exc.read().decode("utf-8", "replace")
                raise RuntimeError(f"HTTP {exc.code}: {redact_secret(detail, api_key)}") from exc
            except Exception as exc:
                # Transient: SSL EOF, IncompleteRead, timeout, connection reset.
                last_exc = exc
                if attempt < attempts - 1:
                    time.sleep(0.8 * (attempt + 1))
        raise RuntimeError(redact_secret(f"network error after {attempts} attempts: {last_exc}", api_key))

    def chat_with_usage(self, messages: list[dict[str, str]], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        body = self._body(messages, overrides)
        payload = self._request(body)
        return {
            "content": str(payload["choices"][0]["message"]["content"]).strip(),
            "usage": payload.get("usage") if isinstance(payload.get("usage"), dict) else {},
            "model": str(payload.get("model") or body.get("model") or ""),
            "raw": payload,
        }

    def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the raw assistant message (content + any tool_calls) plus usage.

        Used by the agent loop: unlike chat_with_usage this preserves tool_calls
        and lets content be empty when the model is requesting a tool.
        """
        body = self._body(messages, overrides)
        if tools:
            body["tools"] = tools
        payload = self._request(body)
        choice = (payload.get("choices") or [{}])[0]
        return {
            "message": choice.get("message") if isinstance(choice.get("message"), dict) else {},
            "usage": payload.get("usage") if isinstance(payload.get("usage"), dict) else {},
            "model": str(payload.get("model") or body.get("model") or ""),
        }

    def chat(self, messages: list[dict[str, str]], overrides: dict[str, Any] | None = None) -> str:
        return str(self.chat_with_usage(messages, overrides).get("content") or "")

    def chat_stream(self, messages: list[dict[str, str]], overrides: dict[str, Any] | None = None) -> Iterator[str]:
        body = self._body(messages, overrides)
        body["stream"] = True
        request = urllib.request.Request(
            self.endpoint(),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=int(self.config.get("request_timeout", 120))) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = payload.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield str(content)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            api_key = os.environ.get("MAICA_CLI_API_KEY") or str(self.config.get("api_key") or "")
            raise RuntimeError(f"HTTP {exc.code}: {redact_secret(detail, api_key)}") from exc
