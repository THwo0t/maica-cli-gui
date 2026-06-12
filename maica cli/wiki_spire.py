# -*- coding: utf-8 -*-
"""Wikipedia topic fetcher for /spire.

The goal is not to make Monika lecture. It gives her a small external spark so
she can start a casual topic from almost anything.
"""

from __future__ import annotations

import json
import random
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_WIKI_TOPICS_ZH = [
    '钢琴',
    '诗歌',
    '咖啡',
    '猫',
    '星空',
    '时间',
    '雨',
    '文学',
    '习惯',
    '音乐',
    '樱花',
    '日记',
    '图书馆',
    '城市',
    '月亮',
    '海',
    '睡眠',
    '记忆',
    '花园',
    '数学',
    '算法',
    '电影',
    '烹饪',
    '茶',
    '旅行',
]

DEFAULT_WIKI_TOPICS_EN = [
    'piano',
    'poetry',
    'coffee',
    'cat',
    'night sky',
    'time',
    'rain',
    'literature',
    'habit',
    'music',
    'cherry blossom',
    'diary',
    'library',
    'city',
    'moon',
    'sea',
    'sleep',
    'memory',
    'garden',
    'mathematics',
    'algorithm',
    'film',
    'cooking',
    'tea',
    'travel',
]


def _read_json(url: str, timeout: float) -> dict[str, Any] | list[Any]:
    request = urllib.request.Request(
        url,
        headers={'User-Agent': 'maica-cli-gui/0.10 local companion prototype'},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode('utf-8')
    return json.loads(raw)


def _wiki_host(language: str) -> str:
    language = (language or 'zh').lower()
    if language.startswith('en'):
        return 'en.wikipedia.org'
    return 'zh.wikipedia.org'


def random_query(language: str, rng: Any | None = None) -> str:
    rng = rng or random
    pool = DEFAULT_WIKI_TOPICS_EN if str(language or '').lower().startswith('en') else DEFAULT_WIKI_TOPICS_ZH
    return str(rng.choice(pool))


def _search_title(query: str, language: str, timeout: float) -> str:
    host = _wiki_host(language)
    params = urllib.parse.urlencode(
        {
            'action': 'opensearch',
            'search': query,
            'limit': 1,
            'namespace': 0,
            'format': 'json',
        }
    )
    data = _read_json(f'https://{host}/w/api.php?{params}', timeout)
    if isinstance(data, list) and len(data) >= 2 and data[1]:
        return str(data[1][0])
    return query


def _random_title(language: str, timeout: float) -> str | None:
    host = _wiki_host(language)
    params = urllib.parse.urlencode(
        {
            'action': 'query',
            'format': 'json',
            'list': 'random',
            'rnnamespace': 0,
            'rnlimit': 1,
        }
    )
    data = _read_json(f'https://{host}/w/api.php?{params}', timeout)
    if not isinstance(data, dict):
        return None
    rows = data.get('query', {}).get('random', [])
    if rows and isinstance(rows[0], dict):
        return str(rows[0].get('title') or '').strip() or None
    return None


def fetch_wikipedia_topic(
    query: str = '',
    language: str = 'zh',
    timeout: float = 6.0,
    rng: Any | None = None,
    random_page: bool = False,
) -> dict[str, str] | None:
    rng = rng or random
    query = str(query or '').strip()
    if random_page and not query:
        query = _random_title(language, timeout) or ''
    if not query:
        query = random_query(language, rng)

    host = _wiki_host(language)
    title = _search_title(query, language, timeout)
    encoded_title = urllib.parse.quote(title.replace(' ', '_'), safe='')
    data = _read_json(f'https://{host}/api/rest_v1/page/summary/{encoded_title}', timeout)
    if not isinstance(data, dict):
        return None
    extract = str(data.get('extract') or '').strip()
    if not extract:
        return None
    return {
        'title': str(data.get('title') or title),
        'summary': extract,
        'url': str(data.get('content_urls', {}).get('desktop', {}).get('page') or ''),
        'source': 'wikipedia',
    }
