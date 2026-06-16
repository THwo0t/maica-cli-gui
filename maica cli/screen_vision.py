# -*- coding: utf-8 -*-
"""Screen vision: capture the active window and describe it with a vision model.

Privacy notes: this is off by default, captures only the *active window* (not
the whole screen where the platform allows), and the image is sent to a vision
model in the cloud. Use only with explicit opt-in.

Capture is platform-aware. KDE Wayland (the primary target) uses Spectacle's
background active-window mode; other platforms are best-effort and may return
None until their capture path is wired.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from client import OpenAICompatibleClient
from text_utils import redact_secret

DEFAULT_VISION_MODEL = "qwen/qwen3.6-flash"
DEFAULT_PROMPT = (
    "In one short, casual sentence, describe what the user seems to be doing in this window — "
    "like a girlfriend glancing over your shoulder. Do not quote private text verbatim."
)


def _capture_command(out: Path) -> list[str] | None:
    if sys.platform.startswith("linux"):
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
        if "KDE" in desktop and shutil.which("spectacle"):
            return ["spectacle", "-a", "-b", "-n", "-o", str(out)]  # active window, background
        if shutil.which("gnome-screenshot"):
            return ["gnome-screenshot", "-w", "-f", str(out)]  # active window
        if shutil.which("grim"):
            return ["grim", str(out)]  # wlroots: whole output (no active-window granularity)
        return None
    # macOS / Windows active-window capture without a user click needs more work;
    # wired later. Returns None so the tool reports "not available yet".
    return None


def capture_active_window(out_path: str | Path | None = None) -> Path | None:
    out = Path(out_path) if out_path else Path(tempfile.gettempdir()) / f"maica_vision_{os.getpid()}.png"
    cmd = _capture_command(out)
    if cmd is None:
        return None
    try:
        subprocess.run(cmd, timeout=12, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except Exception:
        return None
    if out.exists() and out.stat().st_size > 0:
        return out
    return None


def vision_provider(config: dict[str, Any]) -> dict[str, Any]:
    """Resolve the vision model endpoint, defaulting to the agent (OpenRouter) provider."""
    return {
        "api_base": config.get("vision_api_base") or config.get("agent_api_base") or config.get("api_base"),
        "api_key": config.get("vision_api_key") or config.get("agent_api_key") or config.get("api_key"),
        "model": config.get("vision_model") or DEFAULT_VISION_MODEL,
    }


def describe_screen(config: dict[str, Any], prompt: str | None = None) -> dict[str, Any]:
    image = capture_active_window()
    if image is None:
        return {"ok": False, "error": "screen capture is not available on this platform/session yet"}
    try:
        data = image.read_bytes()
    finally:
        try:
            image.unlink()
        except Exception:
            pass
    provider = vision_provider(config)
    if not provider.get("api_key"):
        return {"ok": False, "error": "no vision API key configured"}
    b64 = base64.b64encode(data).decode("ascii")
    client = OpenAICompatibleClient({**config, **provider, "streaming_enabled": False})
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt or DEFAULT_PROMPT},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
        ],
    }]
    try:
        out = client.chat_with_usage(messages, overrides={"max_tokens": int(config.get("vision_max_tokens", 300))})
        return {"ok": True, "description": str(out.get("content") or "").strip()}
    except Exception as exc:
        return {"ok": False, "error": redact_secret(str(exc), provider.get("api_key", ""))}
