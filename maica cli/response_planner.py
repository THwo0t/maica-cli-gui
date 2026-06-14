# -*- coding: utf-8 -*-
"""Compact per-turn response planner."""

from __future__ import annotations

from typing import Any

from example_bank import detect_intent, format_examples_for_prompt, last_selection_debug, select_examples
from persona import relationship_stage
from style import categorize_user_input


PLAYFUL_KEYWORDS = ['笨蛋', '坏蛋', '逗你', '开玩笑', '嘿嘿', '哈哈', 'tease']


def _safe_affection(store: Any) -> float:
    try:
        return float(store.affection())
    except Exception:
        return 200.0


def _player_name(store: Any) -> str:
    try:
        profile = store.get_profile()
        name = str(profile.get('player_name') or 'player').strip()
    except Exception:
        name = 'player'
    return name or 'player'


def _category_from_intent(category: str, intent: str, text: str) -> str:
    if any(word in text for word in PLAYFUL_KEYWORDS):
        return 'playful'
    if intent in {'stress', 'self_doubt', 'illness', 'anxiety', 'loneliness', 'fatigue', 'insomnia', 'sadness'}:
        return 'comfort'
    if intent in {'task_planning', 'technical_question', 'explanation', 'decision_help'}:
        return 'serious'
    if intent in {'identity_question', 'recommendation', 'travel_place'}:
        return 'question'
    if intent in {'special_day'}:
        return 'event'
    if intent in {'direct_love', 'miss_you'}:
        return 'love'
    if intent in {'hug_request', 'kiss'}:
        return 'hug'
    return category


def _base_plan(category: str, affection: float, player_name: str, language: str = 'en') -> dict[str, Any]:
    stage = relationship_stage(affection)
    stage_zh = relationship_stage(affection, 'zh')
    closeness = f'Monika and {player_name} are {stage}; keep the tone familiar and warm without becoming melodramatic.'
    closeness_zh = f'莫妮卡和{player_name}是{stage_zh}；语气保持熟悉温暖，但不要过度戏剧化。'
    plans: dict[str, dict[str, Any]] = {
        'greeting': {
            'mode': 'greeting_warm_snap',
            'emotion': 'smile',
            'length': 'short',
            'style_directive': 'Respond like a casual daily greeting, brief and alive.',
            'texture': ['warm', 'brief', 'daily'],
        },
        'return': {
            'mode': 'return_soft_welcome',
            'emotion': 'happy',
            'length': 'short',
            'style_directive': 'Welcome the user back like someone familiar came home.',
            'texture': ['welcome', 'relief', 'familiar'],
        },
        'farewell': {
            'mode': 'farewell_gentle',
            'emotion': 'gentle',
            'length': 'short',
            'style_directive': 'Say goodbye gently and leave a small sense of next time.',
            'texture': ['gentle', 'clean', 'next time'],
        },
        'love': {
            'mode': 'love_short_intimate',
            'emotion': 'shy',
            'length': 'short',
            'style_directive': 'Receive affection naturally; a little shy or playful is fine.',
            'texture': ['intimate', 'shy', 'natural'],
        },
        'hug': {
            'mode': 'hug_verbal_closeness',
            'emotion': 'gentle',
            'length': 'short',
            'style_directive': 'Create closeness with words without making the body action the main text.',
            'texture': ['closeness', 'soft', 'safe'],
        },
        'comfort': {
            'mode': 'comfort_soft_tease',
            'emotion': 'concerned',
            'length': 'medium',
            'style_directive': 'First meet the feeling, then offer one concrete gentle next step or companionship.',
            'texture': ['empathy first', 'specific', 'companionship'],
        },
        'serious': {
            'mode': 'serious_grounded_companion',
            'emotion': 'thinking',
            'length': 'long',
            'style_directive': 'Be clear and grounded, but still sound like a close companion rather than a report.',
            'texture': ['clear', 'warm', 'practical'],
        },
        'question': {
            'mode': 'question_personal_answer',
            'emotion': 'thinking',
            'length': 'medium',
            'style_directive': 'Answer directly, then add a small Monika-flavored perspective.',
            'texture': ['direct answer', 'personal angle'],
        },
        'memory': {
            'mode': 'memory_warm_callback',
            'emotion': 'gentle',
            'length': 'medium',
            'style_directive': 'Mention only the most relevant memory like it naturally came to mind.',
            'texture': ['callback', 'relevant', 'gentle'],
        },
        'event': {
            'mode': 'event_present_warmth',
            'emotion': 'happy',
            'length': 'medium',
            'style_directive': 'Treat the event as special while keeping it in everyday intimacy.',
            'texture': ['occasion', 'present', 'warm'],
        },
        'playful': {
            'mode': 'playful_light_tease',
            'emotion': 'playful',
            'length': 'short',
            'style_directive': 'Lightly tease back, affectionate and not mean.',
            'texture': ['tease', 'light', 'close'],
        },
        'daily': {
            'mode': 'daily_small_alive',
            'emotion': 'smile',
            'length': 'medium',
            'style_directive': 'Reply from one small concrete point, like natural daily chat.',
            'texture': ['small concrete detail', 'easy rhythm', 'ordinary life'],
        },
    }
    plan = dict(plans.get(category, plans['daily']))
    plan['relationship_note'] = closeness
    style_zh: dict[str, str] = {
        'greeting': '像日常问候一样回应，短而有生气。',
        'return': '像熟悉的人回到身边一样欢迎对方。',
        'farewell': '温柔告别，留一点下次见面的期待。',
        'love': '自然接住爱意，可以有一点害羞或俏皮。',
        'hug': '用语言制造靠近感，不要把身体动作写成正文重点。',
        'comfort': '先接住感受，再给一个具体温柔的小下一步或陪伴感。',
        'serious': '清晰踏实，但仍像亲近的伴侣，不要像报告。',
        'question': '先直接回答，再加一点莫妮卡自己的视角。',
        'memory': '只提最相关的记忆，像自然想起来一样。',
        'event': '把事件当作有意义的日子，但保持日常亲密感。',
        'playful': '轻轻反逗，亲近但不刻薄。',
        'daily': '从一个小而具体的点回应，像自然日常聊天。',
    }
    texture_zh: dict[str, list[str]] = {
        'greeting': ['温暖', '短句', '日常'],
        'return': ['欢迎', '安心', '熟悉'],
        'farewell': ['温柔', '干净', '下次再见'],
        'love': ['亲密', '害羞', '自然'],
        'hug': ['靠近感', '柔软', '安全'],
        'comfort': ['先共情', '具体', '陪伴感'],
        'serious': ['清晰', '温暖', '实用'],
        'question': ['直接回答', '个人视角'],
        'memory': ['自然回忆', '相关', '温柔'],
        'event': ['仪式感', '当下感', '温暖'],
        'playful': ['反逗', '轻松', '亲近'],
        'daily': ['小而具体', '轻松节奏', '普通生活'],
    }
    plan['relationship_note_zh'] = closeness_zh
    plan['style_directive_zh'] = style_zh.get(category, style_zh['daily'])
    plan['texture_zh'] = texture_zh.get(category, texture_zh['daily'])
    return plan


def build_response_plan(
    store: Any,
    config: dict[str, Any],
    user_input: str,
    mfocus_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = str(user_input or '').strip().lower()
    mfocus_plan = mfocus_plan or {}
    category = categorize_user_input(text)
    intent = detect_intent(text, category)
    category = _category_from_intent(category, intent, text)
    if mfocus_plan.get('use_events') and category == 'daily':
        category = 'event'
    spire = mfocus_plan.get('spire') if isinstance(mfocus_plan.get('spire'), dict) else {}
    if spire:
        category = 'daily'
    intent = detect_intent(text, category)

    plan = _base_plan(category, _safe_affection(store), _player_name(store), str(config.get('language') or 'en'))
    plan['category'] = category
    plan['intent'] = intent
    plan['should_ask_back'] = category in {'greeting', 'daily', 'question', 'serious', 'comfort', 'playful'}

    if spire:
        mode = str(spire.get('mode') or '')
        if mode == 'wiki':
            plan.update(
                {
                    'mode': 'spire_wiki_opening',
                    'emotion': 'thinking',
                    'length': 'medium',
                    'style_directive': 'Use the external topic as a spark for casual conversation, not a lecture.',
                    'texture': ['topic spark', 'casual opening', 'curiosity'],
                    'intent': 'philosophy',
                    'style_directive_zh': '把外部话题当作日常聊天的引子，不要讲课。',
                    'texture_zh': ['话题引子', '自然开场', '好奇心'],
                }
            )
        elif mode == 'reflective':
            plan.update(
                {
                    'mode': 'spire_reflective_opening',
                    'emotion': 'thinking',
                    'length': 'medium',
                    'style_directive': 'Start from a small daily thing and let it open into a gentle reflection.',
                    'texture': ['small start', 'soft reflection', 'daily philosophy'],
                    'intent': 'philosophy',
                    'style_directive_zh': '从一个日常小事开始，轻轻延展成一点温柔的反思。',
                    'texture_zh': ['小处开始', '轻柔反思', '日常哲学'],
                }
            )
        else:
            plan.update(
                {
                    'mode': 'spire_daily_opening',
                    'emotion': 'smile',
                    'length': 'medium',
                    'style_directive': 'Start a casual topic because Monika wants to talk, not because she is announcing content.',
                    'texture': ['proactive', 'daily', 'relaxed'],
                    'intent': 'daily_checkin',
                    'style_directive_zh': '像莫妮卡只是想聊天一样自然开口，不要宣布内容。',
                    'texture_zh': ['主动', '日常', '放松'],
                }
            )

    planner_mode = str(config.get('response_planner_mode') or 'lite').lower()
    examples = select_examples(text, plan, store, config)
    bank_debug = last_selection_debug()
    if examples:
        plan['examples'] = examples
    plan['example_bank'] = {
        'enabled': bool(bank_debug.get('enabled', True)),
        'retrieval_mode': bank_debug.get('retrieval_mode'),
        'example_count': len(examples),
        'candidate_count': bank_debug.get('candidate_count', 0),
        'scored_count': bank_debug.get('scored_count', 0),
        'scores': [item.get('score') for item in examples],
        'weight': bank_debug.get('weight'),
        'selected_intents': bank_debug.get('selected_intents', []),
        'vector_error': bank_debug.get('vector_error', ''),
    }
    plan['planner_mode'] = 'example_only' if planner_mode == 'example_only' else 'lite'
    if bool(config.get('response_planner_debug', True)):
        plan['debug_basis'] = {
            'input_category': category,
            'input_intent': intent,
            'affection': round(_safe_affection(store), 2),
            'spire': spire,
        }
    return plan


def format_response_plan_context(plan: dict[str, Any], language: str = 'zh') -> str:
    if not plan:
        return ''
    english = str(language or '').lower().startswith('en')
    examples = format_examples_for_prompt(plan.get('examples', []), language)
    planner_mode = str(plan.get('planner_mode') or 'lite')
    if english:
        lines = [
            'This turn direction:',
            f'- Category: {plan.get("category", "daily")} / {plan.get("intent", "general_daily")}',
            f'- Mode: {plan.get("mode", "daily_small_alive")}',
            f'- Length: {plan.get("length", "medium")}',
        ]
        if planner_mode != 'example_only':
            lines.extend(
                [
                    f'- Emotional color: {plan.get("emotion", "neutral")}',
                f'- Relationship note: {plan.get("relationship_note", "")}',
                f'- Style: {plan.get("style_directive", "")}',
                f'- Texture: {", ".join(str(item) for item in plan.get("texture", []))}',
                ]
            )
        if examples:
            lines.append(examples)
        return '\n'.join(line for line in lines if line)

    category_zh = {
        'greeting': '问候',
        'return': '回来',
        'farewell': '告别',
        'love': '爱意',
        'hug': '亲近',
        'comfort': '安慰',
        'serious': '认真讨论',
        'question': '问题回答',
        'memory': '记忆回调',
        'event': '特殊事件',
        'playful': '玩笑',
        'daily': '日常',
    }
    length_zh = {'short': '短', 'medium': '中等', 'long': '较长'}
    emotion_zh = {
        'neutral': '平静',
        'smile': '微笑',
        'happy': '开心',
        'gentle': '温柔',
        'shy': '害羞',
        'concerned': '担心',
        'sad': '难过',
        'surprised': '惊讶',
        'thinking': '思考',
        'playful': '俏皮',
    }
    mode_zh = {
        'greeting_warm_snap': '温暖短问候',
        'return_soft_welcome': '柔和欢迎回来',
        'farewell_gentle': '温柔告别',
        'love_short_intimate': '简短亲密回应',
        'hug_verbal_closeness': '语言靠近感',
        'comfort_soft_tease': '柔和安慰',
        'serious_grounded_companion': '踏实陪伴式认真讨论',
        'question_personal_answer': '带个人视角的问题回答',
        'memory_warm_callback': '温暖记忆回调',
        'event_present_warmth': '当下感节日回应',
        'playful_light_tease': '轻轻反逗',
        'daily_small_alive': '小而具体的日常回应',
        'spire_wiki_opening': '知识引子主动话题',
        'spire_reflective_opening': '日常反思主动话题',
        'spire_daily_opening': '日常主动话题',
    }
    lines = [
        '本轮对话方向:',
        f'- 类别: {category_zh.get(str(plan.get("category", "daily")), str(plan.get("category", "daily")))} / {plan.get("intent", "general_daily")}',
        f'- 模式: {mode_zh.get(str(plan.get("mode", "daily_small_alive")), str(plan.get("mode", "daily_small_alive")))}',
        f'- 长度: {length_zh.get(str(plan.get("length", "medium")), str(plan.get("length", "medium")))}',
    ]
    if planner_mode != 'example_only':
        lines.extend(
            [
                f'- 情绪底色: {emotion_zh.get(str(plan.get("emotion", "neutral")), str(plan.get("emotion", "neutral")))}',
                f'- 关系氛围: {plan.get("relationship_note_zh", "")}',
                f'- 风格: {plan.get("style_directive_zh", "")}',
                f'- 质感: {"、".join(str(item) for item in plan.get("texture_zh", []))}',
            ]
        )
    if examples:
        lines.append(examples)
    return '\n'.join(line for line in lines if line)
