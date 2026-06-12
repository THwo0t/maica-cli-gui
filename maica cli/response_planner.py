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


def _base_plan(category: str, affection: float, player_name: str) -> dict[str, Any]:
    stage = relationship_stage(affection)
    closeness = f'Monika and {player_name} are {stage}; keep the tone familiar and warm without becoming melodramatic.'
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

    plan = _base_plan(category, _safe_affection(store), _player_name(store))
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
        lines.append('Use examples lightly for rhythm and intimacy. Final dialogue must be English.')
        return '\n'.join(line for line in lines if line)

    lines = [
        '本轮对话方向:',
        f'- 类别: {plan.get("category", "daily")} / {plan.get("intent", "general_daily")}',
        f'- 模式: {plan.get("mode", "daily_small_alive")}',
        f'- 长度: {plan.get("length", "medium")}',
    ]
    if planner_mode != 'example_only':
        lines.extend(
            [
                f'- 情绪底色: {plan.get("emotion", "neutral")}',
                f'- 关系氛围: {plan.get("relationship_note", "")}',
                f'- 风格: {plan.get("style_directive", "")}',
                f'- 质感: {", ".join(str(item) for item in plan.get("texture", []))}',
            ]
        )
    if examples:
        lines.append(examples)
    return '\n'.join(line for line in lines if line)
