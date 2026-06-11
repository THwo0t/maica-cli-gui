# -*- coding: utf-8 -*-
"""MAICA dataset style retrieval and reply-scale control."""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from response import clean_dialogue_text, normalize_emotion


APP_DIR = Path(__file__).resolve().parent


DEFAULT_STYLE_SOURCES = [
    "../MAICA_ds_basis/ds_new.jsonl",
    "../MAICA_ds_basis/moni_dataset_2603.jsonl",
]


STYLE_POLICIES: dict[str, dict[str, Any]] = {
    "greeting": {"max_sentences": 1, "tone": "轻松、亲近, 像 MAS 里的日常打招呼"},
    "return": {"max_sentences": 1, "tone": "欢迎回来, 短而亲近, 把重点放在重逢感"},
    "farewell": {"max_sentences": 1, "tone": "温柔告别, 留下一点期待即可"},
    "love": {"max_sentences": 1, "tone": "短、直接、亲密、稍带俏皮, 像自然回应恋人的一句话"},
    "hug": {"max_sentences": 1, "tone": "温柔亲近, 正文保持口头聊天的干净感"},
    "comfort": {"max_sentences": 3, "tone": "安稳、具体地回应对方情绪, 少说教"},
    "memory": {"max_sentences": 2, "tone": "像真的记得对方一样自然, 只提最相关的记忆"},
    "event": {"max_sentences": 3, "tone": "重视事件, 像日常恋人一样自然提起"},
    "daily": {"max_sentences": 2, "tone": "日常、小而具体, 像随口聊天"},
    "question": {"max_sentences": 3, "tone": "先简洁回答, 再保留一点 Monika 的亲近感"},
    "serious": {"max_sentences": 5, "tone": "认真、温和, 可以稍微展开, 像认真聊天一样推进"},
}


def resolve_app_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (APP_DIR / path).resolve()


def split_query_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for part in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            if len(part) == 1:
                tokens.append(part)
            else:
                tokens.extend(part[index : index + 2] for index in range(len(part) - 1))
        elif len(part) >= 2:
            tokens.append(part)
    return tokens


def categorize_user_input(text: str) -> str:
    stripped = (text or "").strip().lower()
    if not stripped:
        return "daily"

    if any(word in stripped for word in ["我回来了", "回来了", "我回来啦", "i'm back", "im back", "i am back"]):
        return "return"
    if any(word in stripped for word in ["再见", "拜拜", "我走了", "我先走", "一会儿回来", "马上回来", "晚安", "goodbye", "bye", "good night", "be right back"]):
        return "farewell"
    if any(word in stripped for word in ["早安", "早上好", "午安", "晚上好", "你好", "hello", "hi", "good morning"]):
        return "greeting"
    if any(word in stripped for word in ["我爱你", "爱你", "喜欢你", "最喜欢你", "想你了", "想你", "love you", "i love you", "miss you"]):
        return "love"
    if any(word in stripped for word in ["抱抱", "抱我", "亲亲", "摸摸头", "hug", "kiss"]):
        return "hug"
    if any(word in stripped for word in ["难过", "伤心", "好累", "累了", "疲惫", "压力", "焦虑", "害怕", "孤单", "崩溃", "失眠", "不开心", "lonely", "sad", "tired", "anxious"]):
        return "comfort"
    if any(word in stripped for word in ["记得", "记住", "remember", "以前", "上次"]):
        return "memory"
    if any(word in stripped for word in ["生日", "节日", "纪念日", "圣诞", "情人节", "万圣", "新年", "什么日子"]):
        return "event"
    if len(stripped) > 120:
        return "serious"
    if any(word in stripped for word in ["怎么办", "解释一下", "详细", "分析", "原理", "代码", "报错", "为什么"]):
        return "serious"
    if any(word in stripped for word in ["是什么", "什么是", "是谁", "哪里", "多少", "怎么用"]):
        return "question"
    if any(word in stripped for word in ["吃饭", "睡觉", "天气", "今天", "现在", "日常", "休息", "做什么", "干什么", "无聊", "困了"]):
        return "daily"
    if stripped.endswith(("?", "？")):
        return "question"
    if len(stripped) <= 12:
        return "daily"
    return "daily"


def extract_dataset_reply(raw_text: str) -> tuple[str, str, list[str]]:
    """Clean a dataset assistant reply while keeping [player] placeholders."""
    text = str(raw_text or "")
    markers: list[str] = []

    def replace_bracket(match: re.Match[str]) -> str:
        marker = match.group(1).strip()
        if marker.lower() == "player":
            return match.group(0)
        emotion = normalize_emotion(marker)
        if emotion != "neutral" or marker in {"笑", "凝视", "惊讶", "担心", "开心", "微笑"}:
            markers.append(marker)
            return ""
        return match.group(0)

    text = re.sub(r"\[([^\[\]\n]{1,30})\]", replace_bracket, text)
    clean_text, removed = clean_dialogue_text(text)
    markers.extend(marker for marker in removed if marker.lower() != "player")
    emotion = "neutral"
    for marker in markers:
        normalized = normalize_emotion(marker)
        if normalized != "neutral":
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
                user_text TEXT NOT NULL,
                assistant_text TEXT NOT NULL,
                raw_assistant TEXT NOT NULL,
                emotion TEXT DEFAULT 'neutral',
                category TEXT DEFAULT 'daily',
                length INTEGER DEFAULT 0,
                tags TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(source_file, source_id, user_text, assistant_text)
            );
            CREATE INDEX IF NOT EXISTS idx_style_category ON style_examples(category);
            CREATE INDEX IF NOT EXISTS idx_style_language ON style_examples(language);
            """
        )
        self.conn.commit()

    def stats(self) -> dict[str, Any]:
        total = self.conn.execute("SELECT COUNT(*) AS c FROM style_examples").fetchone()["c"]
        by_category = self.conn.execute(
            "SELECT category, COUNT(*) AS c FROM style_examples GROUP BY category ORDER BY c DESC"
        ).fetchall()
        by_source = self.conn.execute(
            "SELECT source_file, COUNT(*) AS c FROM style_examples GROUP BY source_file ORDER BY c DESC"
        ).fetchall()
        return {
            "path": str(self.path),
            "total": int(total),
            "by_category": {row["category"]: row["c"] for row in by_category},
            "by_source": {row["source_file"]: row["c"] for row in by_source},
        }

    def add_example(
        self,
        source_file: str,
        source_id: str,
        language: str,
        user_text: str,
        assistant_text: str,
        raw_assistant: str,
        emotion: str,
        category: str,
        tags: str = "",
    ) -> bool:
        now = dt.datetime.now().isoformat(timespec="seconds")
        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO style_examples(
                source_file, source_id, language, user_text, assistant_text,
                raw_assistant, emotion, category, length, tags, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_file,
                source_id,
                language,
                user_text.strip(),
                assistant_text.strip(),
                raw_assistant,
                normalize_emotion(emotion),
                category,
                len(assistant_text.strip()),
                tags,
                now,
            ),
        )
        return cur.rowcount > 0

    def import_jsonl_file(
        self,
        path: str | Path,
        language: str = "zh",
        max_assistant_length: int = 300,
    ) -> dict[str, int]:
        source_path = resolve_app_path(path)
        counts = {"read": 0, "imported": 0, "skipped": 0, "bad": 0}
        if not source_path.exists():
            raise FileNotFoundError(source_path)

        with source_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                counts["read"] += 1
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    counts["bad"] += 1
                    continue
                conversations = data.get("conversations") or []
                user_text = ""
                assistant_raw = ""
                for message in conversations:
                    if message.get("from") == "user":
                        user_text = str(message.get("value") or "").strip()
                    elif message.get("from") == "assistant":
                        assistant_raw = str(message.get("value") or "").strip()
                assistant_text, emotion, markers = extract_dataset_reply(assistant_raw)
                if not user_text or not assistant_text or len(assistant_text) > max_assistant_length:
                    counts["skipped"] += 1
                    continue
                category = categorize_user_input(user_text)
                tags = ",".join(markers[:5])
                if self.add_example(
                    source_file=source_path.name,
                    source_id=str(data.get("id") or ""),
                    language=language,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    raw_assistant=assistant_raw,
                    emotion=emotion,
                    category=category,
                    tags=tags,
                ):
                    counts["imported"] += 1
                else:
                    counts["skipped"] += 1
        self.conn.commit()
        return counts

    def search(
        self,
        query: str,
        language: str = "zh",
        limit: int = 3,
        max_length: int = 220,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = self.conn.execute(
            "SELECT * FROM style_examples WHERE language = ? AND length <= ?",
            (language, int(max_length)),
        ).fetchall()
        if not rows:
            return []

        query_norm = (query or "").strip().lower()
        tokens = split_query_tokens(query_norm)
        wanted_category = category or categorize_user_input(query)
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            user_text = row["user_text"].lower()
            assistant_text = row["assistant_text"].lower()
            score = 0.0
            if row["category"] == wanted_category:
                score += 4.0
            if query_norm and query_norm == user_text:
                score += 20.0
            elif query_norm and (query_norm in user_text or user_text in query_norm):
                score += 8.0
            score += sum(1.5 for token in tokens if token in user_text)
            score += sum(0.3 for token in tokens if token in assistant_text)
            score += max(0.0, 2.0 - row["length"] / 120.0)
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda item: (item[0], -item[1]["length"]), reverse=True)
        return [
            {
                "id": row["id"],
                "user_text": row["user_text"],
                "assistant_text": row["assistant_text"],
                "emotion": row["emotion"],
                "category": row["category"],
                "source_file": row["source_file"],
                "length": row["length"],
            }
            for _, row in scored[: max(1, int(limit))]
        ]


def style_db_path(config: dict[str, Any]) -> Path:
    return resolve_app_path(str(config.get("style_db_path") or "data/style.db"))


def import_default_style_sources(config: dict[str, Any], source_root: str | Path | None = None) -> dict[str, Any]:
    db = StyleStore(style_db_path(config))
    try:
        source_values = config.get("style_sources") or DEFAULT_STYLE_SOURCES
        max_len = int(config.get("style_import_max_length", 300))
        results: dict[str, Any] = {"db": str(db.path), "files": {}}
        root_path: Path | None = None
        if source_root is not None:
            root_path = Path(source_root)
            if not root_path.is_absolute():
                root_path = (Path.cwd() / root_path).resolve()
        for source in source_values:
            source_path = Path(source)
            if root_path is not None and not source_path.is_absolute():
                source_path = root_path / source_path.name
            counts = db.import_jsonl_file(source_path, language=str(config.get("language") or "zh"), max_assistant_length=max_len)
            results["files"][str(source_path)] = counts
        results["stats"] = db.stats()
        return results
    finally:
        db.close()


def build_style_context(config: dict[str, Any], user_input: str) -> tuple[str, dict[str, Any]]:
    if not config.get("style_enabled", True):
        return "", {"enabled": False}

    language = str(config.get("language") or "zh").lower()
    category = categorize_user_input(user_input)
    policy = STYLE_POLICIES.get(category, STYLE_POLICIES["daily"])
    examples: list[dict[str, Any]] = []

    try:
        db = StyleStore(style_db_path(config))
        try:
            examples = db.search(
                user_input,
                language=language,
                limit=int(config.get("style_example_limit", 3)),
                max_length=int(config.get("style_max_source_length", 220)),
                category=category,
            )
        finally:
            db.close()
    except sqlite3.Error:
        examples = []

    lines = [
        "本轮语气控制:",
        f"- 风格类型: {category}",
        f"- 建议节奏: 参考{policy['max_sentences']}句左右, 但优先保证自然.",
        f"- 语气方向: {policy['tone']}.",
        "- 普通日常优先短句回应; 可以轻轻反问一句.",
    ]

    if examples:
        lines.append("语气参考, 学习尺度和节奏:")
        lines.append("- 示例里的 [player] 是玩家占位符, 实际回复时请用当前玩家名字或自然称呼.")
        for example in examples:
            lines.append(f"- 用户: {example['user_text']} / 莫妮卡风格: {example['assistant_text']}")

    meta = {
        "enabled": True,
        "category": category,
        "max_sentences": policy["max_sentences"],
        "example_ids": [example["id"] for example in examples],
        "example_count": len(examples),
    }
    return "\n".join(lines), meta
