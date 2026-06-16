# -*- coding: utf-8 -*-
"""Screen vision: capture the active window and describe it with a vision model.

Privacy notes: this is off by default, captures only the *active window* (not
the whole screen where the platform allows), and the image is sent to a vision
model in the cloud. Use only with explicit opt-in.

Capture is platform-aware and dependency-free (OS-native tools only):
- Linux/KDE Wayland: Spectacle background active-window mode (GNOME/grim fallbacks).
- macOS: AppleScript front-window bounds + ``screencapture -R`` (needs Accessibility
  + Screen Recording permission; falls back to full screen).
- Windows: a PowerShell + Win32 ``GetForegroundWindow`` + ``CopyFromScreen`` capture.
Unknown platforms return None so the tool reports "not available yet".
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


def _run(cmd: list[str], timeout: int = 12) -> None:
    subprocess.run(cmd, timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def _capture_linux(out: Path) -> bool:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    if "KDE" in desktop and shutil.which("spectacle"):
        _run(["spectacle", "-a", "-b", "-n", "-o", str(out)])  # active window, background
    elif shutil.which("gnome-screenshot"):
        _run(["gnome-screenshot", "-w", "-f", str(out)])  # active window
    elif shutil.which("grim"):
        _run(["grim", str(out)])  # wlroots: whole output (no active-window granularity)
    else:
        return False
    return out.exists()


def _capture_macos(out: Path) -> bool:
    # Front window bounds via AppleScript (needs Accessibility permission), then
    # screencapture of that region (needs Screen Recording permission). Falls
    # back to the full screen if the bounds can't be read.
    script = (
        'tell application "System Events" to tell '
        '(first application process whose frontmost is true) to get {position, size} of front window'
    )
    rect: list[int] = []
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=8)
        rect = [int(float(part)) for part in result.stdout.replace(",", " ").split()]
    except Exception:
        rect = []
    if len(rect) == 4 and rect[2] > 0 and rect[3] > 0:
        _run(["screencapture", "-R", f"{rect[0]},{rect[1]},{rect[2]},{rect[3]}", "-o", "-x", str(out)])
    else:
        _run(["screencapture", "-o", "-x", str(out)])
    return out.exists()


_WINDOWS_CAPTURE_PS = r"""
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @"
using System;using System.Runtime.InteropServices;
public class Win {
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
 public struct RECT { public int L; public int T; public int R; public int B; }
}
"@
$h = [Win]::GetForegroundWindow()
$r = New-Object Win+RECT
[void][Win]::GetWindowRect($h, [ref]$r)
$w = $r.R - $r.L; $ht = $r.B - $r.T
if ($w -le 0 -or $ht -le 0) { exit 1 }
$bmp = New-Object System.Drawing.Bitmap $w, $ht
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.L, $r.T, 0, 0, $bmp.Size)
$bmp.Save("__OUT__", [System.Drawing.Imaging.ImageFormat]::Png)
"""


def _capture_windows(out: Path) -> bool:
    script = _WINDOWS_CAPTURE_PS.replace("__OUT__", str(out).replace("\\", "\\\\"))
    ps1 = Path(tempfile.gettempdir()) / f"maica_vision_{os.getpid()}.ps1"
    try:
        ps1.write_text(script, encoding="utf-8")
        _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)])
    finally:
        try:
            ps1.unlink()
        except Exception:
            pass
    return out.exists()


def capture_active_window(out_path: str | Path | None = None) -> Path | None:
    out = Path(out_path) if out_path else Path(tempfile.gettempdir()) / f"maica_vision_{os.getpid()}.png"
    try:
        if sys.platform.startswith("linux"):
            ok = _capture_linux(out)
        elif sys.platform == "darwin":
            ok = _capture_macos(out)
        elif sys.platform == "win32":
            ok = _capture_windows(out)
        else:
            ok = False
    except Exception:
        ok = False
    return out if (ok and out.exists() and out.stat().st_size > 0) else None


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
