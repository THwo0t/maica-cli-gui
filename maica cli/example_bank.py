# -*- coding: utf-8 -*-
"""Weighted retrieval for dialogue examples used by Response Planner."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from embedding_service_client import search_service_examples
from embedding_index import search_vector_examples


APP_DIR = Path(__file__).resolve().parent
_CACHE: dict[tuple[str, float], list[dict[str, Any]]] = {}
_LAST_SELECTION_DEBUG: dict[str, Any] = {}
NO_FALLBACK_INTENTS = {"acknowledgement", "small_confirmation", "general_daily"}


INTENT_DESCRIPTIONS: dict[str, str] = {
    "morning_greeting": "morning greeting, early-day check-in, warm hello",
    "afternoon_greeting": "afternoon greeting, casual check-in",
    "evening_greeting": "evening greeting, end-of-day check-in",
    "night_farewell": "good night, sleep, gentle farewell",
    "return_home": "player returns, welcome back, reunion",
    "relationship_conflict": "relationship hurt, rejection, conflict, reassurance needed",
    "relationship_check": "asking if Monika loves or values the player",
    "casual_affection": "short affectionate address, pet name, intimate ping",
    "praise_monika": "complimenting Monika appearance or style",
    "direct_love": "direct love confession, saying love you",
    "miss_you": "missing Monika or being missed",
    "kiss": "kiss request or intimate kiss",
    "hug_request": "hug request, wanting closeness",
    "fatigue": "tired, exhausted, needs rest and gentle care",
    "insomnia": "cannot sleep, insomnia, night comfort",
    "loneliness": "lonely, alone, needs concrete companionship",
    "sadness": "sad, hurt, grief, needs emotional comfort",
    "anxiety": "worried, nervous, afraid, needs grounding",
    "illness": "sick, pain, physical discomfort",
    "task_planning": "choosing what to do first, priority planning",
    "project_work": "working on MAICA CLI, coding project, development progress",
    "stress": "busy, pressure, exams, tasks, workload",
    "self_doubt": "feels useless, failure, low confidence",
    "desire_ambiguous": "ambiguous desire, intimate or unclear wanting",
    "hesitation": "hesitating, unsure how to say something",
    "boredom_low_energy": "bored, procrastinating, low energy, slacking off",
    "travel_place": "travel, places, cities, where to go",
    "identity_question": "asking who Monika is or identity",
    "recommendation": "asking for recommendations",
    "appearance_clothes": "clothes, hair, appearance",
    "casual_topic_shift": "change topic, casual topic shift",
    "acknowledgement": "short acknowledgement, okay, mm-hm",
    "small_confirmation": "short agreement, yes, exactly",
    "food_drink": "food, drink, meals, coffee, tea",
    "weather": "weather, rain, cold, hot",
    "music": "music, piano, songs",
    "work_study": "study, work, homework, exams, code",
    "technical_question": "technical question, code, API, database, bug",
    "explanation": "asking what something is or why",
    "decision_help": "asking what to do or how to choose",
    "philosophy": "meaning, existence, time, future, reflective topic",
    "memory_callback": "remembering prior facts or past conversation",
    "playful_tease": "joking, teasing, playful tone",
    "special_day": "special day, birthday, anniversary, holiday",
    "daily_checkin": "daily check-in, how today is going, current state",
}


CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "greeting": "greeting and opening",
    "return": "welcome back",
    "farewell": "farewell and good night",
    "love": "romantic affection",
    "hug": "physical closeness expressed in words",
    "comfort": "emotional support and care",
    "serious": "serious thinking, planning, explanation",
    "question": "answering a question",
    "memory": "using remembered facts naturally",
    "event": "special date or event",
    "playful": "playful teasing",
    "daily": "ordinary daily chat",
}


INTENT_RULES: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ("morning_greeting", ("greeting", "daily"), ("早上好", "早安", "早呀", "早啊", "good morning")),
    ("afternoon_greeting", ("greeting", "daily"), ("下午好", "午安", "good afternoon")),
    ("evening_greeting", ("greeting", "daily"), ("晚上好", "晚好", "good evening")),
    ("night_farewell", ("farewell", "daily"), ("晚安", "睡觉", "去睡", "睡了", "good night")),
    ("return_home", ("return", "greeting", "daily"), ("我回来了", "回来了", "到家", "我们回来了", "买完了")),
    ("relationship_conflict", ("comfort", "serious", "daily", "love"), ("不喜欢你", "讨厌你", "不爱你", "不要你")),
    ("relationship_check", ("love", "daily", "question"), ("你爱我吗", "你真爱我", "爱不爱我", "我重要吗", "在乎我吗")),
    ("casual_affection", ("love", "daily", "hug"), ("亲", "宝宝", "贴贴", "蹭蹭")),
    ("praise_monika", ("love", "daily"), ("你好看", "可爱", "漂亮", "新衣服", "喜欢你的")),
    ("direct_love", ("love",), ("爱你", "我爱你", "喜欢你", "最爱你", "love you")),
    ("miss_you", ("love", "comfort"), ("想你", "我想你", "miss you")),
    ("kiss", ("love",), ("亲亲", "亲一下", "吻", "kiss")),
    ("hug_request", ("hug", "comfort"), ("抱抱", "抱我", "拥抱", "hug")),
    ("fatigue", ("comfort", "daily"), ("好累", "累了", "疲惫", "好困", "很困", "困了", "没精神", "撑不住")),
    ("insomnia", ("comfort",), ("睡不着", "失眠", "熬夜")),
    ("loneliness", ("comfort",), ("孤独", "寂寞", "一个人", "没人陪")),
    ("sadness", ("comfort", "daily"), ("难过", "伤心", "不开心", "哭", "委屈", "糟糕", "家人的死", "去世", "离世")),
    ("anxiety", ("comfort", "serious"), ("焦虑", "害怕", "担心", "紧张", "慌")),
    ("illness", ("comfort",), ("生病", "发烧", "头疼", "胃疼", "不舒服")),
    ("task_planning", ("serious", "question", "daily", "playful"), ("先做什么", "做什么呢", "日程", "计划", "安排", "优先", "任务")),
    ("project_work", ("daily", "serious", "question"), ("cli", "maica", "这个项目", "你的项目", "完善", "版本", "代码项目")),
    ("stress", ("comfort", "serious"), ("压力", "忙", "作业", "考试", "工作", "项目")),
    ("self_doubt", ("comfort", "serious"), ("没用", "失败", "做不到", "讨厌自己", "不够好")),
    ("desire_ambiguous", ("daily", "love", "hug"), ("想做...", "想做…", "想要...", "想要…", "想和你", "想跟你")),
    ("hesitation", ("daily", "question"), ("该怎么说", "不知道", "不知道怎么说", "怎么说呢", "那个")),
    ("boredom_low_energy", ("daily", "comfort"), ("无聊", "什么都不想干", "不想动", "摸鱼", "发呆")),
    ("travel_place", ("question", "daily"), ("去过", "好玩的", "哪里玩", "旅游", "旅行", "北京", "上海", "青海", "青岛")),
    ("identity_question", ("question", "daily"), ("你是谁", "你是什么", "认识我吗", "还认识我")),
    ("recommendation", ("question", "daily"), ("推荐", "有什么好", "吃什么", "看什么", "听什么")),
    ("appearance_clothes", ("daily", "love"), ("衣服", "发型", "头发", "黑发", "长发", "短发", "穿什么")),
    ("casual_topic_shift", ("daily",), ("换个话题", "不聊这个", "随便聊", "聊点别的")),
    ("acknowledgement", ("daily", "comfort", "question", "playful", "love"), ("嗯嗯", "嗯呐", "好的", "好呀", "可以", "行", "没问题", "好啊")),
    ("small_confirmation", ("daily", "comfort", "question", "playful"), ("是啊", "没错", "确实", "也是", "对啊")),
    ("food_drink", ("daily",), ("吃饭", "喝水", "咖啡", "茶", "饿", "午饭", "晚饭")),
    ("weather", ("daily",), ("天气", "下雨", "雨", "阴天", "晴天", "冷", "热")),
    ("music", ("daily", "question"), ("音乐", "钢琴", "歌", "旋律", "听歌")),
    ("work_study", ("daily", "serious"), ("学习", "工作", "考试", "作业", "代码", "项目")),
    ("technical_question", ("question", "serious"), ("代码", "报错", "python", "api", "模型", "函数", "数据库")),
    ("explanation", ("question", "serious"), ("是什么", "为什么", "解释", "原理", "怎么理解")),
    ("decision_help", ("question", "serious"), ("怎么办", "怎么选", "要不要", "该不该")),
    ("philosophy", ("serious", "daily"), ("意义", "人生", "存在", "时间", "未来", "过去", "自由")),
    ("memory_callback", ("memory", "daily"), ("记得", "上次", "以前", "之前", "还记得")),
    ("playful_tease", ("playful", "daily"), ("笨蛋", "坏蛋", "逗你", "开玩笑", "嘿嘿", "哈哈")),
    ("special_day", ("event", "daily"), ("重要日子", "纪念日", "节日", "生日")),
    ("daily_checkin", ("daily", "greeting"), ("今天", "现在", "最近", "怎么样", "过得")),
]


def resolve_app_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (APP_DIR / path).resolve()


def load_dialogue_examples(paths: list[str | Path]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for raw_path in paths:
        path = resolve_app_path(raw_path)
        if not path.exists():
            continue
        mtime = path.stat().st_mtime
        cache_key = (str(path), mtime)
        if cache_key not in _CACHE:
            rows: list[dict[str, Any]] = []
            with path.open("r", encoding="utf-8-sig") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(data, dict):
                        data["_source_path"] = str(path)
                        rows.append(data)
            _CACHE.clear()
            _CACHE[cache_key] = rows
        examples.extend(_CACHE[cache_key])
    return examples


def split_tokens(text: str) -> set[str]:
    text = str(text or "").lower()
    tokens: set[str] = set()
    for part in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            if len(part) == 1:
                tokens.add(part)
            else:
                tokens.update(part[index : index + 2] for index in range(len(part) - 1))
        elif len(part) >= 2:
            tokens.add(part)
    return tokens


def contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def detect_intent(text: str, category: str = "") -> str:
    """Return a narrow scene intent used for example retrieval."""
    category = str(category or "").strip()
    for intent, categories, keywords in INTENT_RULES:
        if category and category not in categories:
            continue
        if contains_any(text, keywords):
            return intent
    for intent, _categories, keywords in INTENT_RULES:
        if contains_any(text, keywords):
            return intent
    return "general_" + (category or "daily")


def detect_example_intent(user: str, notes: str = "", category: str = "") -> str:
    """Infer example intent from the trigger text, using notes only as fallback."""
    intent = detect_intent(user, category)
    if intent.startswith("general_") and notes:
        note_intent = detect_intent(notes, category)
        if not note_intent.startswith("general_"):
            return note_intent
    return intent


def build_retrieval_text(example: dict[str, Any]) -> str:
    """Build a richer semantic text used by lexical search now and embedding later."""
    category = str(example.get("category") or "daily").strip() or "daily"
    user = str(example.get("user") or "").strip()
    notes = str(example.get("notes") or "").strip()
    intent = str(example.get("intent") or "").strip() or detect_example_intent(user, notes, category)
    mode = str(example.get("mode") or "").strip()
    emotion = str(example.get("emotion") or "neutral").strip() or "neutral"
    assistant = str(example.get("assistant") or "").strip()
    source = str(example.get("source") or "").strip()
    parts = [
        f"category: {category}",
        f"category_desc: {CATEGORY_DESCRIPTIONS.get(category, category)}",
        f"intent: {intent}",
        f"intent_desc: {INTENT_DESCRIPTIONS.get(intent, intent)}",
        f"mode: {mode}",
        f"emotion: {emotion}",
        f"example_user: {user}",
    ]
    if notes:
        parts.append(f"notes: {notes}")
    if assistant:
        parts.append(f"assistant_style: {assistant[:180]}")
    if source:
        parts.append(f"source: {source}")
    return "; ".join(part for part in parts if part.strip())


def build_query_retrieval_text(user_input: str, response_plan: dict[str, Any]) -> str:
    category = str(response_plan.get("category") or "daily")
    intent = str(response_plan.get("intent") or detect_intent(user_input, category))
    mode = str(response_plan.get("mode") or "")
    emotion = str(response_plan.get("emotion") or "neutral")
    texture = response_plan.get("texture") or []
    if isinstance(texture, list):
        texture_text = " ".join(str(item) for item in texture)
    else:
        texture_text = str(texture)
    parts = [
        f"category: {category}",
        f"category_desc: {CATEGORY_DESCRIPTIONS.get(category, category)}",
        f"intent: {intent}",
        f"intent_desc: {INTENT_DESCRIPTIONS.get(intent, intent)}",
        f"mode: {mode}",
        f"emotion: {emotion}",
        f"user_input: {user_input}",
    ]
    if texture_text:
        parts.append(f"desired_texture: {texture_text}")
    return "; ".join(part for part in parts if part.strip())


def mode_family(mode: str) -> str:
    mode = str(mode or "").strip()
    if not mode:
        return ""
    aliases = {
        "greeting_warm_snap": "greeting_warm",
        "return_soft_welcome": "return",
        "farewell_gentle": "farewell",
        "love_short_intimate": "love",
        "hug_verbal_closeness": "hug",
        "comfort_soft_tease": "comfort",
        "comfort_warm": "comfort",
        "serious_grounded_companion": "serious",
        "question_personal_answer": "question",
        "memory_warm_callback": "memory",
        "event_present_warmth": "event",
        "playful_light_tease": "playful",
        "daily_small_alive": "daily",
        "daily_warm": "daily",
        "spire_reflective_opening": "spire",
        "spire_daily_opening": "spire",
    }
    if mode in aliases:
        return aliases[mode]
    return mode.split("_", 1)[0]


def token_similarity(left: str, right: str) -> float:
    left_tokens = split_tokens(left)
    right_tokens = split_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = left_tokens & right_tokens
    return len(overlap) / max(1, min(len(left_tokens), len(right_tokens)))


def has_negation(text: str) -> bool:
    return any(word in str(text or "") for word in ("不", "不用", "不要", "别", "讨厌"))


def player_display_name(store: Any) -> str:
    try:
        nicknames = store.get_nicknames()
    except Exception:
        nicknames = []
    if nicknames:
        return str(nicknames[0]).strip() or "player"
    try:
        profile = store.get_profile()
    except Exception:
        profile = {}
    name = str(profile.get("player_name") or "player").strip()
    return name or "player"


def replace_player_placeholder(text: str, store: Any) -> str:
    return str(text or "").replace("{player}", player_display_name(store))


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def is_core_example(example: dict[str, Any]) -> bool:
    source = str(example.get("source") or "").lower()
    source_path = str(example.get("_source_path") or "").lower()
    return source == "core" or "dialogue_examples_core" in source_path


def score_example(example: dict[str, Any], user_input: str, response_plan: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    score = 0.0
    category = str(response_plan.get("category") or "")
    mode = str(response_plan.get("mode") or "")
    emotion = str(response_plan.get("emotion") or "")
    intent = str(response_plan.get("intent") or detect_intent(user_input, category))
    example_user = str(example.get("user") or "")
    example_assistant = str(example.get("assistant") or "")
    example_notes = str(example.get("notes") or "")
    retrieval_text = str(example.get("retrieval_text") or "").strip() or build_retrieval_text(example)
    query_retrieval_text = build_query_retrieval_text(user_input, response_plan)
    example_category = str(example.get("category") or "")
    example_mode = str(example.get("mode") or "")
    example_emotion = str(example.get("emotion") or "")
    example_intent = str(example.get("intent") or "").strip()
    if not example_intent:
        example_intent = detect_example_intent(example_user, example_notes, example_category)

    if str(example.get("category") or "") == category:
        score += 65
    elif example_intent == intent and not intent.startswith("general_"):
        score += 20
    else:
        score -= 45

    if example_intent == intent:
        score += 130
    elif mode_family(example_mode) == mode_family(mode) and mode_family(mode):
        score += 35

    if example_mode == mode:
        score += 35
    elif mode_family(example_mode) == mode_family(mode) and mode_family(mode):
        score += 22

    if emotion and example_emotion == emotion:
        score += 16

    quality = safe_int(example.get("quality"), 0)
    score += quality * 8

    if is_core_example(example):
        score += 35

    user_similarity = token_similarity(user_input, example_user)
    full_similarity = token_similarity(user_input, " ".join([example_user, example_assistant, example_notes]))
    retrieval_similarity = token_similarity(query_retrieval_text, retrieval_text)
    score += user_similarity * 95
    score += full_similarity * 25
    score += retrieval_similarity * 70

    clean_user = str(user_input or "").strip().lower()
    clean_example_user = example_user.strip().lower()
    if clean_user and clean_user == clean_example_user:
        score += 90
    elif clean_user and (clean_user in clean_example_user or clean_example_user in clean_user):
        score += 40

    assistant_len = len(str(example.get("assistant") or ""))
    if assistant_len > 180:
        score -= min(20, (assistant_len - 180) / 8)
    if assistant_len < 6:
        score -= 10

    debug = {
        "intent": intent,
        "example_intent": example_intent,
        "user_similarity": round(user_similarity, 3),
        "full_similarity": round(full_similarity, 3),
        "retrieval_similarity": round(retrieval_similarity, 3),
        "mode_family": mode_family(mode),
        "example_mode_family": mode_family(example_mode),
        "is_core": is_core_example(example),
        "negation_mismatch": bool(has_negation(example_user) and not has_negation(user_input)),
    }
    return round(score, 3), debug


def last_selection_debug() -> dict[str, Any]:
    return dict(_LAST_SELECTION_DEBUG)


def select_examples(
    user_input: str,
    response_plan: dict[str, Any],
    store: Any,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    global _LAST_SELECTION_DEBUG
    _LAST_SELECTION_DEBUG = {"enabled": bool(config.get("example_bank_enabled", True))}
    if not config.get("example_bank_enabled", True):
        return []
    core_paths = config.get("example_bank_core_paths") or []
    paths = config.get("example_bank_paths") or ["data/dialogue_examples_maica_cleaned.jsonl"]
    if not isinstance(core_paths, list):
        core_paths = [core_paths]
    if not isinstance(paths, list):
        paths = [paths]
    paths = [*core_paths, *paths]
    min_quality = safe_int(config.get("example_bank_min_quality", 4), 4)
    limit = max(0, safe_int(config.get("example_bank_limit", 3), 3))
    candidate_limit = max(limit, safe_int(config.get("example_bank_candidate_limit", 40), 40))
    min_score = safe_float(config.get("example_bank_min_score", 120), 120.0)
    max_length = max(1, safe_int(config.get("example_bank_max_assistant_length", 220), 220))
    model_filtering = bool(config.get("example_bank_model_filtering", True))
    strict_relevance = bool(config.get("example_bank_strict_relevance", True))
    min_vector_score = safe_float(
        config.get("example_bank_min_vector_score", config.get("embedding_min_score", 0.55)),
        0.62,
    )
    if limit <= 0:
        return []

    scored: list[tuple[float, int, dict[str, Any]]] = []
    plan_intent = str(response_plan.get("intent") or detect_intent(user_input, str(response_plan.get("category") or "")))
    query_retrieval_text = build_query_retrieval_text(user_input, response_plan)
    retrieval_mode = "lexical"
    vector_error = ""
    vector_count = 0
    source_examples: list[dict[str, Any]] | None = None
    if config.get("embedding_service_enabled", False):
        try:
            vector_limit = max(
                candidate_limit,
                safe_int(config.get("embedding_top_k", 30), 30),
            )
            retrieval_mode = "service"
            source_examples = search_service_examples(
                query_retrieval_text,
                config,
                limit=vector_limit,
                min_score=-1.0 if model_filtering else safe_float(config.get("embedding_min_score", 0.55), 0.55),
            )
            vector_count = len(source_examples)
        except Exception as exc:
            retrieval_mode = "lexical"
            vector_error = f"service: {exc}"
            source_examples = None
    elif config.get("embedding_enabled", False):
        try:
            vector_limit = max(
                candidate_limit,
                safe_int(config.get("embedding_top_k", 30), 30),
            )
            retrieval_mode = "vector"
            source_examples = search_vector_examples(
                query_retrieval_text,
                config,
                limit=vector_limit,
                min_score=-1.0 if model_filtering else safe_float(config.get("embedding_min_score", 0.55), 0.55),
            )
            vector_count = len(source_examples)
        except Exception as exc:
            retrieval_mode = "lexical"
            vector_error = str(exc)
            source_examples = None
    all_examples = source_examples if source_examples is not None else load_dialogue_examples(paths)
    examined = 0
    for index, example in enumerate(all_examples):
        quality = safe_int(example.get("quality"), 0)
        assistant = str(example.get("assistant") or "").strip()
        user = str(example.get("user") or "").strip()
        if quality < min_quality or not assistant or not user:
            continue
        if len(assistant) > max_length:
            continue
        examined += 1
        score, debug = score_example(example, user_input, response_plan)
        vector_score = safe_float(example.get("_vector_score"), 0.0)
        if retrieval_mode in {"vector", "service"} and strict_relevance and not model_filtering and vector_score < min_vector_score:
            continue
        if retrieval_mode in {"vector", "service"}:
            score = round(score + vector_score * 180, 3)
        if score < min_score and not (retrieval_mode == "vector" and model_filtering):
            continue
        row = {
            "category": str(example.get("category") or ""),
            "mode": str(example.get("mode") or ""),
            "intent": str(example.get("intent") or debug["example_intent"]),
            "emotion": str(example.get("emotion") or "neutral"),
            "user": replace_player_placeholder(user, store),
            "assistant": replace_player_placeholder(assistant, store),
            "quality": quality,
            "source": str(example.get("source") or Path(str(example.get("_source_path") or "")).name),
            "notes": replace_player_placeholder(str(example.get("notes") or ""), store),
            "score": score,
            "similarity": debug["user_similarity"],
            "full_similarity": debug["full_similarity"],
            "retrieval_similarity": debug["retrieval_similarity"],
            "vector_similarity": round(vector_score, 6) if vector_score else 0.0,
            "vector_rank": example.get("_vector_rank"),
            "core": debug["is_core"],
            "negation_mismatch": debug["negation_mismatch"],
        }
        scored.append((score, index, row))

    scored.sort(key=lambda item: (-item[0], item[1]))
    raw_exact_intent = [item for item in scored if item[2].get("intent") == plan_intent]
    exact_intent = raw_exact_intent
    if raw_exact_intent:
        exact_intent = [
            item for item in raw_exact_intent
            if item[2].get("core")
            and (
                float(item[2].get("similarity") or 0.0) >= 0.15
                or float(item[2].get("full_similarity") or 0.0) >= 0.10
                or float(item[2].get("retrieval_similarity") or 0.0) >= 0.22
            )
            or (
                not item[2].get("negation_mismatch")
                and (
                    float(item[2].get("similarity") or 0.0) >= 0.2
                    or float(item[2].get("full_similarity") or 0.0) >= 0.12
                    or float(item[2].get("retrieval_similarity") or 0.0) >= 0.40
                )
            )
        ]
    if retrieval_mode in {"vector", "service"} and model_filtering:
        candidates = scored[:candidate_limit]
    elif raw_exact_intent:
        candidates = exact_intent[:candidate_limit]
    elif plan_intent in NO_FALLBACK_INTENTS:
        candidates = []
    else:
        candidates = scored[:candidate_limit]
    selected: list[dict[str, Any]] = []
    seen_assistant: set[str] = set()
    seen_user_intents: set[tuple[str, str]] = set()
    for _score, _index, row in candidates:
        assistant_key = re.sub(r"\s+", "", row["assistant"])[:80]
        user_intent_key = (row["user"], row["intent"])
        if assistant_key in seen_assistant or user_intent_key in seen_user_intents:
            continue
        seen_assistant.add(assistant_key)
        seen_user_intents.add(user_intent_key)
        selected.append(row)
        if len(selected) >= limit:
            break

    _LAST_SELECTION_DEBUG = {
        "enabled": True,
        "retrieval_mode": retrieval_mode,
        "vector_count": vector_count,
        "vector_error": vector_error,
        "intent": plan_intent,
        "examined": examined,
        "candidate_count": len(scored),
        "exact_intent_count": len(exact_intent),
        "candidate_limit": candidate_limit,
        "min_score": min_score,
        "model_filtering": model_filtering,
        "strict_relevance": strict_relevance,
        "min_vector_score": min_vector_score,
        "selected_scores": [item.get("score") for item in selected],
        "selected_intents": [item.get("intent") for item in selected],
        "selected_sources": [item.get("source") for item in selected],
        "selected_retrieval_similarity": [item.get("retrieval_similarity") for item in selected],
        "selected_vector_similarity": [item.get("vector_similarity") for item in selected],
    }
    return selected


def format_examples_for_prompt(examples: list[dict[str, Any]], language: str = "zh") -> str:
    if not examples:
        return ""
    if str(language or "").lower().startswith("en"):
        lines = [
            "Candidate reference examples, up to 3. They are optional, not rules. "
            "First judge whether each example truly fits this turn, then choose 0-3 only as references for pacing, intimacy, and reply structure. "
            "Ignore unrelated examples completely. Do not copy the wording or language."
        ]
        for index, example in enumerate(examples, start=1):
            lines.append(f"Example {index}:")
            lines.append(f"User: {example.get('user', '')}")
            lines.append(f"Assistant style sample: {example.get('assistant', '')}")
            notes = str(example.get("notes") or "").strip()
            if notes:
                notes = notes.replace("避" + "免机械复读", "自然接住关系感")
                lines.append(f"Rhythm note: {notes}")
        return "\n".join(lines)
    lines = [
        "候选参考样例（最多3条）：这些只是候选，不是必须采用的规则。"
        "请你先判断每条是否真的适合本轮对话，再自行选择0-3条作为节奏、亲近感和回复结构参考；"
        "不相关就完全忽略，不要照抄内容。"
    ]
    for index, example in enumerate(examples, start=1):
        lines.append(f"示例 {index}:")
        lines.append(f"用户: {example.get('user', '')}")
        lines.append(f"回复: {example.get('assistant', '')}")
        notes = str(example.get("notes") or "").strip()
        if notes:
            notes = notes.replace("避" + "免机械复读", "自然接住关系感")
            lines.append(f"节奏说明: {notes}")
    return "\n".join(lines)
