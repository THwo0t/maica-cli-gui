# -*- coding: utf-8 -*-
"""Wikipedia topic fetcher for /spire.

This mirrors MAICA MSpire's idea: gather a small external topic summary first,
then let Monika start a conversation from her own angle.
"""

from __future__ import annotations

import json
import random
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_WIKI_TOPICS_ZH = [
    "钢琴",
    "诗歌",
    "咖啡",
    "猫",
    "星空",
    "时间",
    "雨",
    "文学",
    "习惯",
    "音乐",
    "樱花",
    "日记",
]


def _read_json(url: str, timeout: float) -> dict[str, Any] | list[Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "maica-cli/0.7.1 local companion prototype"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def _wiki_host(language: str) -> str:
    language = (language or "zh").lower()
    if language.startswith("en"):
        return "en.wikipedia.org"
    return "zh.wikipedia.org"


def _search_title(query: str, language: str, timeout: float) -> str:
    host = _wiki_host(language)
    params = urllib.parse.urlencode(
        {
            "action": "opensearch",
            "search": query,
            "limit": 1,
            "namespace": 0,
            "format": "json",
        }
    )
    data = _read_json(f"https://{host}/w/api.php?{params}", timeout)
    if isinstance(data, list) and len(data) >= 2 and data[1]:
        return str(data[1][0])
    return query


def fetch_wikipedia_topic(
    query: str = "",
    language: str = "zh",
    timeout: float = 6.0,
    rng: Any | None = None,
) -> dict[str, str] | None:
    rng = rng or random
    query = str(query or "").strip()
    if not query:
        query = rng.choice(DEFAULT_WIKI_TOPICS_ZH)

    host = _wiki_host(language)
    title = _search_title(query, language, timeout)
    encoded_title = urllib.parse.quote(title.replace(" ", "_"), safe="")
    data = _read_json(f"https://{host}/api/rest_v1/page/summary/{encoded_title}", timeout)
    if not isinstance(data, dict):
        return None
    extract = str(data.get("extract") or "").strip()
    if not extract:
        return None
    return {
        "title": str(data.get("title") or title),
        "summary": extract,
        "url": str(data.get("content_urls", {}).get("desktop", {}).get("page") or ""),
        "source": "wikipedia",
    }
