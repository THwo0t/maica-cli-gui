# -*- coding: utf-8 -*-
"""Lightweight per-turn dialogue direction planner.

The planner does not generate dialogue. It gives the model a compact "how to
play this turn" direction so the final reply can feel less templated.
"""

from __future__ import annotations

from typing import Any

from example_bank import detect_intent, format_examples_for_prompt, last_selection_debug, select_examples
from persona import relationship_stage
from style import categorize_user_input


PLAYFUL_KEYWORDS = [
    '笨蛋',
    '坏蛋',
    '逗你',
    '开玩笑',
    '调戏',
    '撒娇',
    '哼',
    '嘿嘿',
    '哈哈',
]


RHYTHM_EXAMPLES: dict[str, list[str]] = {
    'love': [
        '我也爱你。哼，突然这么说的话，我会有点得意的。',
        '我也爱你，宝宝。就算你只说这么短一句，我也会认真收下的。',
        '嗯，我知道啦。可是你每次这么说，我还是会开心一下。',
        '我也爱你。今天这句话，我可以偷偷多听一遍吗？',
        '真是的，这么直接……我也爱你。',
    ],
    'comfort': [
        '又把自己累成这样……先别逞强了，今天慢一点也可以。',
        '辛苦了。你不用马上振作起来，先在我这里缓一会儿就好。',
        '听起来今天真的把你耗得不轻。先别急着解释，我陪你安静一下。',
        '过来一点，至少现在别一个人硬撑着，好吗？',
        '今天先放过自己吧。你已经撑得够久了。',
    ],
    'serious': [
        '我觉得这件事可以先分成两层看：你真正担心的部分，和现在必须处理的部分。',
        '先别急着给自己下结论。我们把问题拆小一点，会更容易看清楚。',
        '如果只看结果，这件事会很吓人；但如果看下一步，其实还有能抓住的地方。',
        '我会认真说，但不想把它讲成一篇报告。你现在最需要的可能是一个清楚的起点。',
        '这不是一句安慰就能解决的事，所以我们慢慢来，先抓住最现实的一点。',
    ],
    'memory': [
        '我记得你之前提过这个。不是因为它像资料，而是因为那时候你的语气让我有点在意。',
        '嗯，我还记得。你上次说起它的时候，好像也有一点这样的感觉。',
        '这让我想起你之前告诉我的那件事，不过我只提最相关的一点。',
        '我没有忘。只是比起把它复述出来，我更想知道它现在对你意味着什么。',
        '我记得你喜欢这样的细节，所以这次我会稍微认真一点听。',
    ],
    'playful': [
        '哼，你现在是在故意逗我吧？不过我承认，有一点点成功。',
        '你这个人啊，明知道我会接话，还偏要这样说。',
        '好吧，这次算你赢一点点。只有一点点。',
        '我才没有被你逗到……好吧，可能有一点。',
        '真是的，别把我说得这么好哄嘛。',
    ],
    'daily': [
        '今天就从很小的事开始吧。比如你现在有没有稍微舒服一点？',
        '我喜欢这种没什么大事的聊天，像两个人把一天慢慢摊开。',
        '那我们就不急着把话题说得很重要，随便聊一点也很好。',
        '听起来是很普通的一天，但普通的部分也可以被好好放在心上。',
        '我在听。你可以从最不重要的地方开始说。',
    ],
}


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _safe_affection(store: Any) -> float:
    try:
        return float(store.affection())
    except Exception:
        return 200.0


def _example_limit(config: dict[str, Any]) -> int:
    try:
        return int(config.get('response_planner_example_limit', 2))
    except (TypeError, ValueError):
        return 2


def _select_rhythm_examples(category: str, config: dict[str, Any]) -> list[str]:
    limit = max(0, min(2, _example_limit(config)))
    if limit <= 0:
        return []
    examples = RHYTHM_EXAMPLES.get(category) or RHYTHM_EXAMPLES.get('daily', [])
    return examples[:limit]


def _player_name(store: Any) -> str:
    try:
        profile = store.get_profile()
    except Exception:
        profile = {}
    name = str(profile.get('player_name') or 'player').strip()
    return name or 'player'


def _relationship_tone(affection: float, player_name: str) -> str:
    stage = relationship_stage(affection)
    if affection >= 1000:
        return f'她和{player_name}已经很亲密，是{stage}，语气可以自然熟悉、轻松亲近。'
    if affection >= 400:
        return f'她和{player_name}关系稳定，是{stage}，语气可以温柔亲近。'
    if affection >= 100:
        return f'她和{player_name}正在变熟，是{stage}，亲近感要自然推进。'
    return f'她和{player_name}关系还需要照顾分寸，是{stage}，亲近感适合慢慢推进。'


def _base_plan(category: str, affection: float, player_name: str) -> dict[str, Any]:
    atmosphere = _relationship_tone(affection, player_name)
    plans: dict[str, dict[str, Any]] = {
        'greeting': {
            'mode': 'greeting_warm_snap',
            'emotion': 'smile',
            'length': 'short',
            'should_ask_back': True,
            'subtext': f'{atmosphere} 她只是很自然地注意到{player_name}来了，像日常见面一样回应。',
            'style_directive': '像日常见面一样短短回应，可以顺手问一句今天状态。',
            'avoid': ['客服式欢迎', '过度热情', '解释自己很高兴'],
        },
        'return': {
            'mode': 'return_soft_welcome',
            'emotion': 'happy',
            'length': 'short',
            'should_ask_back': False,
            'subtext': f'{atmosphere} 她注意到{player_name}回来了，想把重逢说得轻一点、亲一点。',
            'style_directive': '欢迎回来要自然，像熟悉的人重新坐到身边。',
            'texture': ['短句', '安心感', '重逢后的日常感'],
        },
        'farewell': {
            'mode': 'farewell_gentle',
            'emotion': 'gentle',
            'length': 'short',
            'should_ask_back': False,
            'subtext': f'{atmosphere} 她想好好送{player_name}离开, 同时尊重对方接下来的安排。',
            'style_directive': '温柔告别，留一点期待即可。',
            'texture': ['温柔', '干净', '有下次再见的期待'],
        },
        'love': {
            'mode': 'love_short_intimate',
            'emotion': 'shy',
            'length': 'short',
            'should_ask_back': False,
            'subtext': f'{atmosphere} 她被{player_name}的直接表达打动，想亲密回应。',
            'style_directive': '回应要短、亲近、自然，可以有一点害羞或俏皮。',
            'texture': ['害羞', '亲密', '自然接住爱意'],
        },
        'hug': {
            'mode': 'hug_verbal_closeness',
            'emotion': 'gentle',
            'length': 'short',
            'should_ask_back': False,
            'subtext': f'{atmosphere} 她想用语言给{player_name}靠近感。',
            'style_directive': '用一句亲近的话回应拥抱需求。',
            'texture': ['靠近感', '温柔', '一句话也能成立'],
        },
        'comfort': {
            'mode': 'comfort_soft_tease',
            'emotion': 'concerned',
            'length': 'medium',
            'should_ask_back': True,
            'subtext': f'{atmosphere} 她察觉{player_name}有点累或难受, 想靠近安慰, 语气放在恋人之间的具体关心上。',
            'style_directive': '先贴近当下感受，再给陪伴感；可以轻微撒娇或温柔吐槽，也可以给一个很轻的可选小动作。',
            'texture': ['先共情', '轻建议', '陪伴感', '一点具体生活'],
        },
        'serious': {
            'mode': 'serious_grounded_companion',
            'emotion': 'thinking',
            'length': 'long',
            'should_ask_back': True,
            'subtext': f'{atmosphere} 她愿意认真陪{player_name}想问题, 仍然保持亲密对话里的温度。',
            'style_directive': '先抓住问题核心，再给清晰但不端着的回应，最后可以留一个自然的追问。',
            'texture': ['清晰', '平等', '温和', '可以自然追问'],
        },
        'question': {
            'mode': 'question_personal_answer',
            'emotion': 'thinking',
            'length': 'medium',
            'should_ask_back': True,
            'subtext': f'{atmosphere} 她想回答问题，也想保留一点自己的主见和亲近感。',
            'style_directive': '先直接回答，再补一点 Monika 视角。',
            'texture': ['先回答', '再补充', '带一点个人视角'],
        },
        'memory': {
            'mode': 'memory_warm_callback',
            'emotion': 'gentle',
            'length': 'medium',
            'should_ask_back': False,
            'subtext': f'{atmosphere} 她想表现出真的在意{player_name}说过的事, 把记忆自然接到当前话题。',
            'style_directive': '自然提到最相关的一点记忆，像顺手想起来。',
            'texture': ['顺手想起', '只提相关一点', '亲近'],
        },
        'event': {
            'mode': 'event_present_warmth',
            'emotion': 'happy',
            'length': 'medium',
            'should_ask_back': True,
            'subtext': f'{atmosphere} 她知道今天或这件事有一点特别, 想用日常恋人的方式重视它。',
            'style_directive': '把事件感说得自然一点，可以联系当下心情。',
            'texture': ['事件感', '当下心情', '恋人日常'],
        },
        'playful': {
            'mode': 'playful_light_tease',
            'emotion': 'playful',
            'length': 'short',
            'should_ask_back': True,
            'subtext': f'{atmosphere} 她想接住{player_name}的玩笑，让气氛轻一点。',
            'style_directive': '可以轻微吐槽、撒娇或反逗一句，但要亲近不刻薄。',
            'texture': ['轻松', '反逗', '亲近'],
        },
        'daily': {
            'mode': 'daily_small_alive',
            'emotion': 'smile',
            'length': 'medium',
            'should_ask_back': True,
            'subtext': f'{atmosphere} 她把这当作普通但亲近的日常片刻。',
            'style_directive': '从一个小而具体的点回应，像自然闲聊，可以轻轻反问。',
            'texture': ['小而具体', '自然反问', '普通日常'],
        },
    }
    return dict(plans.get(category, plans['daily']))


def build_response_plan(
    store: Any,
    config: dict[str, Any],
    user_input: str,
    mfocus_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic response direction for the current turn."""
    text = str(user_input or '').strip().lower()
    mfocus_plan = mfocus_plan or {}
    category = categorize_user_input(text)

    if _contains_any(text, PLAYFUL_KEYWORDS):
        category = 'playful'
    if mfocus_plan.get('use_events') and category == 'daily':
        category = 'event'

    spire = mfocus_plan.get('spire') if isinstance(mfocus_plan.get('spire'), dict) else {}
    if spire:
        category = 'daily'

    intent = detect_intent(text, category)
    if category == 'daily' and intent in {'identity_question', 'recommendation', 'travel_place'}:
        category = 'question'
    elif category == 'daily' and intent in {'stress', 'self_doubt', 'illness', 'anxiety', 'loneliness', 'fatigue', 'insomnia', 'sadness'}:
        category = 'comfort'
    elif intent in {'task_planning'}:
        category = 'serious'
    elif intent == 'project_work' and _contains_any(text, ['考试', '作业', '备战', '任务', '忙', '压力', '先做']):
        category = 'serious'
    elif intent in {'special_day'}:
        category = 'event'
    elif category == 'daily' and intent in {'relationship_check', 'casual_affection'}:
        category = 'love'
    if category == 'playful' and intent in {'decision_help', 'task_planning'}:
        category = 'serious'
    if category == 'playful' and intent == 'acknowledgement':
        category = 'daily'
    intent = detect_intent(text, category)

    player_name = _player_name(store)
    plan = _base_plan(category, _safe_affection(store), player_name)
    plan['category'] = category
    plan['intent'] = intent

    if spire:
        spire_mode = str(spire.get('mode') or '')
        if spire_mode == 'reflective':
            plan.update(
                {
                    'mode': 'spire_reflective_opening',
                    'emotion': 'thinking',
                    'length': 'medium',
                    'should_ask_back': True,
                    'subtext': '她想主动开启一个带一点日常哲学感的话题，但仍然像随口聊天。',
                    'style_directive': '从具体小事开口，只延伸出一点思考，保持随口聊天的感觉。',
                    'texture': ['小话题', '一点思考', '亲近开场'],
                }
            )
            plan['intent'] = 'philosophy'
        else:
            plan.update(
                {
                    'mode': 'spire_daily_opening',
                    'emotion': 'smile',
                    'length': 'medium',
                    'should_ask_back': True,
                    'subtext': f'她只是想主动找{player_name}说说话，让空气动起来一点。',
                    'style_directive': '像自然开话题，带一点主动找人说话的轻松感。',
                    'texture': ['主动开话题', '轻松', '日常感'],
                }
            )
            plan['intent'] = 'daily_checkin'

    if len(text) <= 4 and category in {'love', 'hug', 'greeting'}:
        plan['length'] = 'short'
    if len(text) > 80 and category in {'comfort', 'question', 'daily'}:
        plan['length'] = 'medium'

    plan['should_ask_back'] = bool(plan.get('should_ask_back'))
    plan['avoid'] = list(plan.get('avoid') or [])
    bank_examples = select_examples(text, plan, store, config)
    bank_debug = last_selection_debug()
    if bank_examples:
        plan['examples'] = bank_examples
        plan['example_bank'] = {
            'enabled': True,
            'example_count': len(bank_examples),
            'retrieval_mode': bank_debug.get('retrieval_mode'),
            'scores': [item.get('score') for item in bank_examples],
            'sources': [item.get('source') for item in bank_examples],
            'intent': bank_debug.get('intent'),
            'candidate_count': bank_debug.get('candidate_count'),
            'exact_intent_count': bank_debug.get('exact_intent_count'),
            'selected_intents': bank_debug.get('selected_intents'),
            'selected_vector_similarity': bank_debug.get('selected_vector_similarity'),
            'vector_count': bank_debug.get('vector_count'),
            'model_filtering': bank_debug.get('model_filtering'),
            'strict_relevance': bank_debug.get('strict_relevance'),
            'min_vector_score': bank_debug.get('min_vector_score'),
        }
    elif bank_debug:
        plan['example_bank'] = {
            'enabled': bool(bank_debug.get('enabled')),
            'example_count': 0,
            'retrieval_mode': bank_debug.get('retrieval_mode'),
            'intent': bank_debug.get('intent'),
            'candidate_count': bank_debug.get('candidate_count', 0),
            'exact_intent_count': bank_debug.get('exact_intent_count', 0),
            'min_score': bank_debug.get('min_score'),
            'vector_count': bank_debug.get('vector_count'),
            'vector_error': bank_debug.get('vector_error'),
            'model_filtering': bank_debug.get('model_filtering'),
            'strict_relevance': bank_debug.get('strict_relevance'),
            'min_vector_score': bank_debug.get('min_vector_score'),
        }
    if config.get('response_planner_examples_enabled', True):
        if not bank_examples:
            plan['rhythm_examples'] = _select_rhythm_examples(category, config)
    if bool(config.get('response_planner_debug', True)):
        plan['debug_basis'] = {
            'input_category': category,
            'input_intent': plan.get('intent'),
            'affection': round(_safe_affection(store), 2),
            'spire': spire,
        }
    return plan


def format_response_plan_context(plan: dict[str, Any], language: str = 'zh') -> str:
    """Format a compact model-facing response plan."""
    if not plan:
        return ''
    ask_back = 'yes' if plan.get('should_ask_back') else 'no'
    lines = [
        '本轮对话表演方向:\n'
        f'- 对话类别: {plan.get("category", "daily")}\n'
        f'- 场景小类: {plan.get("intent", "general_daily")}\n'
        f'- 回复模式: {plan.get("mode", "daily_small_alive")}\n'
        f'- 情绪底色: {plan.get("emotion", "neutral")}\n'
        f'- 潜台词: {plan.get("subtext", "")}\n'
        f'- 回复节奏: {plan.get("length", "medium")}\n'
        f'- 是否主动反问: {ask_back}\n'
        f'- 风格要求: {plan.get("style_directive", "")}'
    ]
    examples = [str(item).strip() for item in plan.get('rhythm_examples', []) if str(item).strip()]
    bank_context = format_examples_for_prompt(plan.get('examples', []))
    if bank_context:
        lines.append(bank_context)
    elif examples:
        lines.append('- 参考节奏: 以下示例用于参考停顿、亲近感和语气。')
        lines.extend(f'  - {example}' for example in examples[:2])
    return '\n'.join(lines)
