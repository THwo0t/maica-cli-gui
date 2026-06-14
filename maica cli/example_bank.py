# -*- coding: utf-8 -*-
"""Low-weight dialogue example retrieval for Response Planner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from embedding_index import search_vector_examples
from embedding_service_client import search_service_examples
from language_runtime import target_language
from text_utils import contains_cjk, cjk_ratio, redact_secret, split_query_tokens


APP_DIR = Path(__file__).resolve().parent
_CACHE: dict[tuple[str, float], list[dict[str, Any]]] = {}
_LAST_SELECTION_DEBUG: dict[str, Any] = {}


INTENT_DESCRIPTIONS: dict[str, str] = {
    'morning_greeting': 'morning greeting and early-day warmth',
    'night_farewell': 'good night and sleep',
    'return_home': 'welcome back',
    'direct_love': 'direct love confession',
    'miss_you': 'missing Monika or being missed',
    'kiss': 'kiss or intimate affection',
    'hug_request': 'hug or closeness request',
    'fatigue': 'tired, exhausted, needs rest',
    'insomnia': 'cannot sleep',
    'loneliness': 'lonely or alone',
    'sadness': 'sad or hurt',
    'anxiety': 'worried or nervous',
    'illness': 'physical discomfort',
    'stress': 'busy, pressure, exams, workload',
    'task_planning': 'choosing what to do first',
    'project_work': 'coding or MAICA project work',
    'self_doubt': 'low confidence',
    'desire_ambiguous': 'ambiguous desire or ellipsis',
    'hesitation': 'not sure how to say it',
    'boredom_low_energy': 'bored or low energy',
    'travel_place': 'travel or place',
    'identity_question': 'identity or self-awareness',
    'recommendation': 'asking for suggestions',
    'appearance_clothes': 'appearance, clothes, hair',
    'food_drink': 'food or drink',
    'weather': 'weather',
    'music': 'music or piano',
    'work_study': 'study, work, homework, exam, code',
    'technical_question': 'technical question or bug',
    'explanation': 'asking what or why',
    'decision_help': 'asking how to choose',
    'philosophy': 'meaning, existence, time, future',
    'memory_callback': 'remembering prior facts',
    'playful_tease': 'joking or teasing',
    'special_day': 'birthday or holiday',
    'daily_checkin': 'how today is going',
}

CATEGORY_DESCRIPTIONS: dict[str, str] = {
    'greeting': 'greeting and opening',
    'return': 'welcome back',
    'farewell': 'farewell and good night',
    'love': 'romantic affection',
    'hug': 'physical closeness expressed in words',
    'comfort': 'emotional support',
    'serious': 'serious planning or explanation',
    'question': 'answering a question',
    'memory': 'using remembered facts',
    'event': 'special date or event',
    'playful': 'playful teasing',
    'daily': 'ordinary daily chat',
}

INTENT_RULES: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ('morning_greeting', ('greeting', 'daily'), ('早上好', '早安', 'good morning')),
    ('night_farewell', ('farewell', 'daily'), ('晚安', '睡觉', '睡了', 'good night')),
    ('return_home', ('return', 'greeting', 'daily'), ('我回来了', '回来了', "i'm back", 'im back')),
    ('direct_love', ('love',), ('爱你', '我爱你', '喜欢你', 'love you', 'i love you')),
    ('miss_you', ('love', 'comfort'), ('想你', 'miss you')),
    ('kiss', ('love', 'hug'), ('亲亲', 'kiss')),
    ('hug_request', ('hug', 'comfort'), ('抱抱', '抱我', 'hug')),
    ('fatigue', ('comfort', 'daily'), ('好累', '累了', '疲惫', '困了', '撑不住', 'tired', 'exhausted')),
    ('insomnia', ('comfort',), ('睡不着', '失眠', '熬夜')),
    ('loneliness', ('comfort',), ('孤独', '寂寞', '一个人', '没人陪', 'lonely')),
    ('sadness', ('comfort',), ('难过', '伤心', '不开心', '哭', '糟糕', 'sad')),
    ('anxiety', ('comfort', 'serious'), ('焦虑', '害怕', '担心', '紧张', 'anxious', 'worry')),
    ('illness', ('comfort',), ('生病', '发烧', '头疼', '胃疼', '不舒服')),
    ('stress', ('comfort', 'serious', 'daily'), ('压力', '忙', '作业', '考试', '期末', '工作', '任务')),
    ('task_planning', ('serious', 'question', 'daily'), ('先做什么', '计划', '安排', '优先', '日程')),
    ('project_work', ('daily', 'serious'), ('maica', 'cli', '项目', '代码项目', '版本')),
    ('self_doubt', ('comfort', 'serious'), ('没用', '失败', '做不到', '讨厌自己')),
    ('desire_ambiguous', ('daily', 'love', 'hug'), ('想做...', '想要...', '想做…', '想要…')),
    ('hesitation', ('daily', 'question'), ('该怎么说', '不知道怎么说', '那个', '嗯嗯', '唔')),
    ('boredom_low_energy', ('daily', 'comfort'), ('无聊', '不想动', '摸鱼', '发呆')),
    ('travel_place', ('question', 'daily'), ('旅行', '旅游', '哪里玩', '城市')),
    ('identity_question', ('question', 'daily'), ('你是谁', '你是什么', '认识我吗')),
    ('recommendation', ('question', 'daily'), ('推荐', '有什么好', '吃什么', '看什么', '听什么')),
    ('appearance_clothes', ('daily', 'love'), ('衣服', '发型', '头发', '穿什么')),
    ('food_drink', ('daily',), ('吃饭', '喝水', '咖啡', '茶', '饮料')),
    ('weather', ('daily',), ('天气', '下雨', '雪', '阴天', '晴天', '冷', '热')),
    ('music', ('daily', 'question'), ('音乐', '钢琴', '歌', '旋律')),
    ('work_study', ('daily', 'serious'), ('学习', '工作', '作业', '考试', '代码')),
    ('technical_question', ('question', 'serious'), ('代码', '报错', 'python', 'api', '模型', '函数', '数据库')),
    ('explanation', ('question', 'serious'), ('是什么', '为什么', '解释', '原理')),
    ('decision_help', ('question', 'serious'), ('怎么办', '怎么选', '要不要', '该不该')),
    ('philosophy', ('serious', 'daily'), ('意义', '人生', '存在', '时间', '未来', '过去', '自由')),
    ('memory_callback', ('memory', 'daily'), ('记得', '上次', '以前', '之前', '还记得')),
    ('playful_tease', ('playful', 'daily'), ('笨蛋', '坏蛋', '逗你', '开玩笑', '嘿嘿', '哈哈')),
    ('special_day', ('event', 'daily'), ('节日', '生日', '纪念日', '圣诞', '情人节')),
    ('daily_checkin', ('daily', 'greeting'), ('今天', '现在', '最近', '怎么样', '过得')),
]


def resolve_app_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (APP_DIR / path).resolve()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _example_matches_target_language(user: str, assistant: str, language: str) -> bool:
    combined = f'{user}\n{assistant}'
    if str(language or '').lower().startswith('en'):
        return not contains_cjk(combined)
    # Chinese mode should use Chinese examples. English terms, names, and
    # acronyms inside otherwise-Chinese examples are fine.
    return cjk_ratio(combined) > 0.08


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
            with path.open('r', encoding='utf-8-sig') as handle:
                for line_number, line in enumerate(handle, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(data, dict):
                        data['_source_path'] = str(path)
                        data['_source_line'] = line_number
                        rows.append(data)
            for key in [key for key in _CACHE if key[0] == str(path)]:
                _CACHE.pop(key, None)
            _CACHE[cache_key] = rows
        examples.extend(_CACHE[cache_key])
    return examples


def contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = str(text or '').lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def detect_intent(text: str, category: str = '') -> str:
    category = str(category or '').strip()
    for intent, categories, keywords in INTENT_RULES:
        if category and category not in categories:
            continue
        if contains_any(text, keywords):
            return intent
    for intent, _categories, keywords in INTENT_RULES:
        if contains_any(text, keywords):
            return intent
    return 'general_' + (category or 'daily')


def detect_example_intent(user: str, notes: str = '', category: str = '') -> str:
    intent = detect_intent(user, category)
    if intent.startswith('general_') and notes:
        note_intent = detect_intent(notes, category)
        if not note_intent.startswith('general_'):
            return note_intent
    return intent


def build_retrieval_text(example: dict[str, Any]) -> str:
    category = str(example.get('category') or 'daily').strip() or 'daily'
    user = str(example.get('user') or '').strip()
    notes = str(example.get('notes') or '').strip()
    intent = str(example.get('intent') or '').strip() or detect_example_intent(user, notes, category)
    mode = str(example.get('mode') or '').strip()
    emotion = str(example.get('emotion') or 'neutral').strip() or 'neutral'
    assistant = str(example.get('assistant') or '').strip()
    source = str(example.get('source') or '').strip()
    parts = [
        f'category: {category}',
        f'category_desc: {CATEGORY_DESCRIPTIONS.get(category, category)}',
        f'intent: {intent}',
        f'intent_desc: {INTENT_DESCRIPTIONS.get(intent, intent)}',
        f'mode: {mode}',
        f'emotion: {emotion}',
        f'example_user: {user}',
    ]
    if notes:
        parts.append(f'notes: {notes}')
    if assistant:
        parts.append(f'assistant_style: {assistant[:180]}')
    if source:
        parts.append(f'source: {source}')
    return '; '.join(part for part in parts if part.strip())


def build_query_retrieval_text(user_input: str, response_plan: dict[str, Any]) -> str:
    category = str(response_plan.get('category') or 'daily')
    intent = str(response_plan.get('intent') or detect_intent(user_input, category))
    mode = str(response_plan.get('mode') or '')
    emotion = str(response_plan.get('emotion') or 'neutral')
    texture = response_plan.get('texture') or []
    texture_text = ' '.join(str(item) for item in texture) if isinstance(texture, list) else str(texture)
    parts = [
        f'category: {category}',
        f'intent: {intent}',
        f'intent_desc: {INTENT_DESCRIPTIONS.get(intent, intent)}',
        f'mode: {mode}',
        f'emotion: {emotion}',
        f'user_input: {user_input}',
    ]
    if texture_text:
        parts.append(f'desired_texture: {texture_text}')
    return '; '.join(part for part in parts if part.strip())


def mode_family(mode: str) -> str:
    mode = str(mode or '').strip()
    if not mode:
        return ''
    for prefix in ('greeting', 'return', 'farewell', 'love', 'hug', 'comfort', 'serious', 'question', 'memory', 'event', 'playful', 'daily', 'spire'):
        if mode.startswith(prefix):
            return prefix
    return mode.split('_', 1)[0]


def token_similarity(left: str, right: str) -> float:
    left_tokens = set(split_query_tokens(left))
    right_tokens = set(split_query_tokens(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))


def is_core_example(example: dict[str, Any]) -> bool:
    source = str(example.get('source') or '').lower()
    source_path = str(example.get('_source_path') or '').lower()
    return source == 'core' or 'dialogue_examples_core' in source_path


def player_display_name(store: Any) -> str:
    try:
        nicknames = store.get_nicknames()
        if nicknames:
            return str(nicknames[0]).strip() or 'player'
        profile = store.get_profile()
    except Exception:
        profile = {}
    return str(profile.get('player_name') or 'player').strip() or 'player'


def replace_player_placeholder(text: str, store: Any) -> str:
    return str(text or '').replace('{player}', player_display_name(store)).replace('[player]', player_display_name(store))


def score_example(example: dict[str, Any], user_input: str, response_plan: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    category = str(response_plan.get('category') or '')
    mode = str(response_plan.get('mode') or '')
    emotion = str(response_plan.get('emotion') or '')
    intent = str(response_plan.get('intent') or detect_intent(user_input, category))
    example_user = str(example.get('user') or '')
    example_assistant = str(example.get('assistant') or '')
    example_notes = str(example.get('notes') or '')
    example_category = str(example.get('category') or '')
    example_mode = str(example.get('mode') or '')
    example_emotion = str(example.get('emotion') or '')
    example_intent = str(example.get('intent') or '').strip() or detect_example_intent(example_user, example_notes, example_category)

    score = 0.0
    if example_category == category:
        score += 45
    elif example_intent == intent and not intent.startswith('general_'):
        score += 15
    else:
        score -= 40
    if example_intent == intent and not intent.startswith('general_'):
        score += 80
    elif mode_family(example_mode) == mode_family(mode) and mode_family(mode):
        score += 22
    if example_mode == mode:
        score += 20
    if emotion and example_emotion == emotion:
        score += 8
    quality = _safe_int(example.get('quality'), 0)
    score += quality * 8
    if is_core_example(example):
        score += 35

    user_similarity = token_similarity(user_input, example_user)
    full_similarity = token_similarity(user_input, ' '.join([example_user, example_assistant, example_notes]))
    retrieval_similarity = token_similarity(build_query_retrieval_text(user_input, response_plan), str(example.get('retrieval_text') or build_retrieval_text(example)))
    score += user_similarity * 40
    score += full_similarity * 12
    score += retrieval_similarity * 35

    clean_user = str(user_input or '').strip().lower()
    clean_example_user = example_user.strip().lower()
    exact_match = bool(clean_user and clean_user == clean_example_user)
    if exact_match:
        score += 12
    elif clean_user and (clean_user in clean_example_user or clean_example_user in clean_user):
        score += 10

    assistant_len = len(example_assistant)
    if assistant_len > 220:
        score -= min(35, (assistant_len - 220) / 6)
    if assistant_len < 6:
        score -= 12

    debug = {
        'intent': intent,
        'example_intent': example_intent,
        'user_similarity': round(user_similarity, 3),
        'full_similarity': round(full_similarity, 3),
        'retrieval_similarity': round(retrieval_similarity, 3),
        'exact_match': exact_match,
        'is_core': is_core_example(example),
    }
    return round(score, 3), debug


def _paths_for_language(config: dict[str, Any], key: str, fallback_key: str, language: str) -> list[Any]:
    mapped = config.get(key)
    if isinstance(mapped, dict):
        values = mapped.get(target_language(language))
        if values is not None:
            return _as_list(values)
    return _as_list(config.get(fallback_key))


def _example_paths(config: dict[str, Any]) -> list[Any]:
    language = target_language(config)
    core = _paths_for_language(config, 'example_bank_core_paths_by_language', 'example_bank_core_paths', language)
    extra = _paths_for_language(config, 'example_bank_paths_by_language', 'example_bank_paths', language)
    return [*core, *extra]


def _vector_candidates(query_text: str, config: dict[str, Any], limit: int, min_score: float) -> tuple[list[dict[str, Any]], str, str]:
    if config.get('embedding_service_enabled', False):
        try:
            return search_service_examples(query_text, config, limit, min_score), 'service', ''
        except Exception as exc:
            return [], 'service', redact_secret(str(exc), config.get('api_key', ''))
    if config.get('embedding_enabled', False):
        try:
            return search_vector_examples(query_text, config, limit, min_score), 'vector', ''
        except Exception as exc:
            return [], 'vector', redact_secret(str(exc), config.get('api_key', ''))
    return [], 'lexical', ''


def select_examples(user_input: str, response_plan: dict[str, Any], store: Any, config: dict[str, Any]) -> list[dict[str, Any]]:
    global _LAST_SELECTION_DEBUG
    if not config.get('example_bank_enabled', True):
        _LAST_SELECTION_DEBUG = {'enabled': False}
        return []

    limit = max(0, min(5, _safe_int(config.get('example_bank_limit'), 3)))
    min_quality = _safe_int(config.get('example_bank_min_quality'), 4)
    min_score = _safe_float(config.get('example_bank_min_score'), 150.0)
    weight = max(0.0, min(2.0, _safe_float(config.get('example_bank_weight'), 0.65)))
    max_len = _safe_int(config.get('example_bank_max_assistant_length'), 220)
    query_text = build_query_retrieval_text(user_input, response_plan)
    vector_limit = max(limit * 4, _safe_int(config.get('embedding_top_k'), 30))
    vector_min = _safe_float(config.get('example_bank_min_vector_score'), 0.62)

    vector_rows, retrieval_mode, vector_error = _vector_candidates(query_text, config, vector_limit, vector_min)
    if vector_rows:
        candidates = vector_rows
    else:
        candidates = load_dialogue_examples(_example_paths(config))
        retrieval_mode = 'lexical' if retrieval_mode != 'service' else 'service_fallback_lexical'

    selected_language = target_language(config)
    language_filtered_count = 0
    scored: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        user = str(item.get('user') or '').strip()
        assistant = str(item.get('assistant') or '').strip()
        if not user or not assistant:
            continue
        item_language = str(item.get('language') or '').lower()
        if item_language and target_language(item_language) != selected_language:
            language_filtered_count += 1
            continue
        if not _example_matches_target_language(user, assistant, selected_language):
            language_filtered_count += 1
            continue
        if _safe_int(item.get('quality'), 0) < min_quality:
            continue
        if len(assistant) > max_len:
            continue
        item = dict(item)
        item['retrieval_text'] = str(item.get('retrieval_text') or '').strip() or build_retrieval_text(item)
        raw_score, debug = score_example(item, user_input, response_plan)
        score = raw_score * weight
        debug['raw_score'] = raw_score
        debug['weight'] = weight
        vector_score = item.get('_vector_score')
        if vector_score is not None:
            score += float(vector_score) * 80
            debug['vector_similarity'] = round(float(vector_score), 3)
        scored.append((score, item, debug))

    scored.sort(key=lambda row: (row[0], is_core_example(row[1])), reverse=True)
    selected: list[dict[str, Any]] = []
    exact_used = False
    for score, item, debug in scored:
        if score < min_score:
            continue
        if debug.get('exact_match'):
            if exact_used:
                continue
            exact_used = True
        item = dict(item)
        item['assistant'] = replace_player_placeholder(str(item.get('assistant') or ''), store)
        item['user'] = replace_player_placeholder(str(item.get('user') or ''), store)
        item['score'] = round(score, 3)
        item.update({key: value for key, value in debug.items() if key not in item})
        selected.append(item)
        if len(selected) >= limit:
            break

    _LAST_SELECTION_DEBUG = {
        'enabled': True,
        'retrieval_mode': retrieval_mode,
        'intent': response_plan.get('intent'),
        'candidate_count': len(candidates),
        'scored_count': len(scored),
        'example_count': len(selected),
        'min_score': min_score,
        'weight': weight,
        'vector_error': vector_error,
        'selected_intents': [item.get('example_intent') or item.get('intent') for item in selected],
        'selected_scores': [item.get('score') for item in selected],
        'target_language': selected_language,
        'source_paths': [str(resolve_app_path(path)) for path in _example_paths(config)],
        'language_filtered_count': language_filtered_count,
    }
    return selected


def last_selection_debug() -> dict[str, Any]:
    return dict(_LAST_SELECTION_DEBUG)


def format_examples_for_prompt(examples: list[dict[str, Any]], language: str = 'zh') -> str:
    if not examples:
        return ''
    english = str(language or '').lower().startswith('en')
    lines = (
        ["Low-weight rhythm references. Keep the reply natural and in Monika's own voice."]
        if english
        else ['低权重节奏参考。保持回复自然，用莫妮卡自己的语气。']
    )
    for index, item in enumerate(examples[:5], start=1):
        user = str(item.get('user') or '').strip()
        assistant = str(item.get('assistant') or '').strip()
        notes = str(item.get('notes') or '').strip()
        lines.append(f'Example {index}:' if english else f'样例 {index}:')
        lines.append(f'User: {user}' if english else f'用户: {user}')
        lines.append(f'Monika: {assistant}' if english else f'莫妮卡: {assistant}')
        if notes:
            lines.append(f'Notes: {notes}' if english else f'说明: {notes}')
    return '\n'.join(lines)
