# -*- coding: utf-8 -*-
"""MAICA dataset style retrieval and lightweight reply-scale control."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from response import clean_dialogue_text, normalize_emotion
from text_utils import contains_cjk, split_query_tokens


APP_DIR = Path(__file__).resolve().parent

DEFAULT_STYLE_SOURCES = [
    '../MAICA_ds_basis/ds_new.jsonl',
    '../MAICA_ds_basis/moni_dataset_2603.jsonl',
]

STYLE_POLICIES: dict[str, dict[str, Any]] = {
    'greeting': {'max_sentences': 1, 'tone': 'short, warm, like a daily greeting'},
    'return': {'max_sentences': 1, 'tone': 'welcome back, familiar and close'},
    'farewell': {'max_sentences': 1, 'tone': 'gentle farewell with a little expectation'},
    'love': {'max_sentences': 2, 'tone': 'intimate, natural, not overly poetic'},
    'hug': {'max_sentences': 2, 'tone': 'verbal closeness and comfort'},
    'comfort': {'max_sentences': 4, 'tone': 'specific emotional support, not lecturing'},
    'memory': {'max_sentences': 3, 'tone': 'remember naturally, only the relevant detail'},
    'event': {'max_sentences': 4, 'tone': 'special but still daily and intimate'},
    'daily': {'max_sentences': 3, 'tone': 'ordinary, concrete, relaxed daily chat'},
    'question': {'max_sentences': 4, 'tone': 'answer clearly while staying close'},
    'serious': {'max_sentences': 6, 'tone': 'clear, grounded, warm, not report-like'},
    'playful': {'max_sentences': 3, 'tone': 'light teasing, affectionate, not mean'},
}


def resolve_app_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (APP_DIR / path).resolve()


def style_db_path(config: dict[str, Any]) -> Path:
    return resolve_app_path(config.get('style_db_path') or 'data/style.db')


def categorize_user_input(text: str) -> str:
    stripped = str(text or '').strip().lower()
    if not stripped:
        return 'daily'

    rules: list[tuple[str, tuple[str, ...]]] = [
        ('return', ('我回来了', '回来了', "i'm back", 'im back', 'i am back')),
        ('farewell', ('再见', '拜拜', '我走了', '我先走', '一会儿回来', '马上回来', '晚安', 'goodbye', 'bye', 'good night', 'be right back')),
        ('greeting', ('早安', '早上好', '午安', '晚上好', '你好', 'hello', 'hi', 'good morning')),
        ('love', ('我爱你', '爱你', '喜欢你', '最喜欢你', '想你了', '想你', 'love you', 'i love you', 'miss you')),
        ('hug', ('抱抱', '抱我', '亲亲', '摸摸头', 'hug', 'kiss')),
        ('comfort', ('难过', '伤心', '好累', '累了', '疲惫', '压力', '焦虑', '害怕', '孤单', '崩溃', '失眠', '不开心', 'lonely', 'sad', 'tired', 'anxious')),
        ('memory', ('记得', '记住', 'remember', '以前', '上次')),
        ('event', ('生日', '节日', '纪念日', '圣诞', '情人节', '万圣', '新年', '什么日子')),
        ('playful', ('笨蛋', '坏蛋', '逗你', '开玩笑', '嘿嘿', '哈哈', 'tease')),
    ]
    for category, keywords in rules:
        if any(word in stripped for word in keywords):
            return category

    if len(stripped) > 120:
        return 'serious'
    if any(word in stripped for word in ('怎么办', '解释一下', '详细', '分析', '原理', '代码', '报错', '为什么', 'how do', 'why')):
        return 'serious'
    if any(word in stripped for word in ('是什么', '什么是', '是谁', '哪里', '多少', '怎么用', '?')):
        return 'question'
    if any(word in stripped for word in ('吃饭', '睡觉', '天气', '今天', '现在', '日常', '休息', '做什么', '干什么', '无聊', '困了')):
        return 'daily'
    if stripped.endswith(('?', '？')):
        return 'question'
    return 'daily'


def extract_dataset_reply(raw_text: str) -> tuple[str, str, list[str]]:
    """Clean a dataset assistant reply while keeping [player] placeholders."""
    text = str(raw_text or '')
    markers: list[str] = []

    def replace_bracket(match: re.Match[str]) -> str:
        marker = match.group(1).strip()
        if marker.lower() == 'player':
            return match.group(0)
        emotion = normalize_emotion(marker)
        if emotion != 'neutral' or marker in {'笑', '凝视', '惊讶', '担心', '开心', '微笑'}:
            markers.append(marker)
            return ''
        return match.group(0)

    text = re.sub(r'\[([^\[\]\n]{1,30})\]', replace_bracket, text)
    clean_text, removed = clean_dialogue_text(text)
    markers.extend(marker for marker in removed if marker.lower() != 'player')
    emotion = 'neutral'
    for marker in markers:
        normalized = normalize_emotion(marker)
        if normalized != 'neutral':
            emotion = normalized
            break
    return clean_text.strip(), emotion, markers


class StyleStore:
    def __init__(self, path: str | Path):
        self.path = resolve_app_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.ensure_schema()

    def close(self) -> None:
        self.conn.close()

    def ensure_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS style_examples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file TEXT NOT NULL,
                source_id TEXT DEFAULT '',
                language TEXT DEFAULT 'zh',
                category TEXT DEFAULT 'daily',
                emotion TEXT DEFAULT 'neutral',
                user_text TEXT DEFAULT '',
                assistant_text TEXT NOT NULL,
                tags TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_style_category ON style_examples(category);
            CREATE INDEX IF NOT EXISTS idx_style_emotion ON style_examples(emotion);
            """
        )
        self.conn.commit()

    def import_jsonl_file(self, path: str | Path, language: str = 'zh', max_assistant_length: int = 300) -> dict[str, int]:
        source_path = Path(path)
        counts = {'read': 0, 'imported': 0, 'skipped': 0, 'bad': 0}
        if not source_path.exists():
            return counts
        with source_path.open('r', encoding='utf-8-sig') as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                counts['read'] += 1
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    counts['bad'] += 1
                    continue
                if not isinstance(item, dict):
                    counts['bad'] += 1
                    continue
                user_text, assistant_text = _extract_pair(item)
                assistant_text, emotion, markers = extract_dataset_reply(assistant_text)
                user_text = str(user_text or '').strip()
                if not assistant_text or len(assistant_text) > max_assistant_length:
                    counts['skipped'] += 1
                    continue
                category = categorize_user_input(user_text)
                source_id = str(item.get('id') or item.get('source_id') or line_number)
                self.conn.execute(
                    'INSERT INTO style_examples(source_file, source_id, language, category, emotion, user_text, assistant_text, tags) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                    (str(source_path), source_id, language, category, emotion, user_text, assistant_text, ','.join(markers)),
                )
                counts['imported'] += 1
        self.conn.commit()
        return counts

    def search(self, query: str, category: str = '', limit: int = 3) -> list[sqlite3.Row]:
        query_norm = str(query or '').strip().lower()
        wanted_category = category or categorize_user_input(query)
        tokens = split_query_tokens(query_norm)
        rows = self.conn.execute(
            'SELECT * FROM style_examples WHERE category = ? ORDER BY id DESC LIMIT 300',
            (wanted_category,),
        ).fetchall()
        scored = []
        for row in rows:
            user_text = str(row['user_text'] or '').lower()
            assistant_text = str(row['assistant_text'] or '').lower()
            score = 1.0
            score += sum(1.5 for token in tokens if token in user_text)
            score += sum(0.3 for token in tokens if token in assistant_text)
            scored.append((score, row))
        scored.sort(key=lambda item: (item[0], item[1]['id']), reverse=True)
        return [row for _, row in scored[:limit]]

    def export_jsonl(self, path: str | Path) -> dict[str, int]:
        rows = self.conn.execute('SELECT * FROM style_examples ORDER BY id ASC').fetchall()
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('w', encoding='utf-8') as handle:
            for row in rows:
                handle.write(json.dumps(dict(row), ensure_ascii=False) + '\n')
        return {'exported': len(rows)}


def _extract_pair(item: dict[str, Any]) -> tuple[str, str]:
    if isinstance(item.get('messages'), list):
        user = ''
        assistant = ''
        for message in item['messages']:
            if not isinstance(message, dict):
                continue
            role = str(message.get('role') or '')
            content = str(message.get('content') or '')
            if role == 'user':
                user = content
            elif role == 'assistant':
                assistant = content
        return user, assistant
    return str(item.get('user') or item.get('input') or ''), str(item.get('assistant') or item.get('output') or item.get('reply') or '')


def import_default_style_sources(config: dict[str, Any], source_root: str | Path | None = None) -> dict[str, Any]:
    db = StyleStore(style_db_path(config))
    sources = config.get('style_sources') or DEFAULT_STYLE_SOURCES
    max_len = int(config.get('style_import_max_length', 300))
    result: dict[str, Any] = {'sources': []}
    try:
        for source in sources:
            source_path = Path(source)
            if source_root is not None and not source_path.is_absolute():
                source_path = Path(source_root) / source_path.name
            elif not source_path.is_absolute():
                source_path = resolve_app_path(source_path)
            counts = db.import_jsonl_file(source_path, language=str(config.get('language') or 'zh'), max_assistant_length=max_len)
            result['sources'].append({'path': str(source_path), **counts})
    finally:
        db.close()
    return result


def build_style_context(config: dict[str, Any], user_input: str) -> tuple[str, dict[str, Any]]:
    if not config.get('style_enabled', True):
        return '', {'enabled': False}
    category = categorize_user_input(user_input)
    policy = STYLE_POLICIES.get(category, STYLE_POLICIES['daily'])
    english = str(config.get('language') or 'en').lower().startswith('en')
    meta: dict[str, Any] = {
        'enabled': True,
        'category': category,
        'max_sentences': policy['max_sentences'],
        'example_ids': [],
        'example_count': 0,
    }
    lines = [
        'Style reference:',
        f'- Category: {category}',
        f'- Tone: {policy["tone"]}',
        f'- Suggested length: about {policy["max_sentences"]} sentence(s), unless the user clearly needs more.',
    ]
    try:
        db = StyleStore(style_db_path(config))
        examples = db.search(user_input, category, int(config.get('style_example_limit', 3)))
        db.close()
    except Exception:
        examples = []
    if english:
        # CJK examples cannot demonstrate English wording; skip them instead
        # of emitting empty placeholder lines.
        examples = [
            row
            for row in examples
            if not (contains_cjk(str(row['user_text'])) or contains_cjk(str(row['assistant_text'])))
        ]
    if examples:
        lines.append('- Dataset rhythm examples, for pacing only:')
        for row in examples:
            meta['example_ids'].append(int(row['id']))
            lines.append(f'  User: {row["user_text"]}')
            lines.append(f'  Monika: {row["assistant_text"]}')
        meta['example_count'] = len(examples)
    return '\n'.join(lines), meta
