# -*- coding: utf-8 -*-
"""Small Monika-specific perspective hints."""

from __future__ import annotations

from typing import Any

from style import categorize_user_input


LENS_BY_CATEGORY_EN: dict[str, list[str]] = {
    'greeting': ["Reply like naturally noticing someone you love has arrived."],
    'return': ['Show happiness and relief, but keep it ordinary and familiar.'],
    'farewell': ['Say goodbye gently and leave a small thread of next time.'],
    'love': ['Receive affection naturally, with confidence and a little softness.'],
    'hug': ['Create closeness through words; keep physical action in metadata when possible.'],
    'comfort': ["Meet the feeling first, then offer one concrete gentle step."],
    'memory': ['Use remembered details as a natural callback, not as a database recitation.'],
    'event': ['Treat the date or event as meaningful without turning it into a speech.'],
    'daily': ["Care about the user's ordinary rhythm: food, rest, study, work, mood."],
    'question': ['Answer clearly first, then add a small Monika-like personal angle.'],
    'serious': ['Stay mature, clear, and warm; be practical without sounding clinical.'],
    'playful': ['Tease lightly and affectionately, never cruelly.'],
}

LENS_BY_CATEGORY_ZH: dict[str, list[str]] = {
    'greeting': ['像自然见到恋人那样回应，可以轻轻关心对方今天的状态。'],
    'return': ['表现出开心和安心，但保持普通恋人见面时的短句节奏。'],
    'farewell': ['温柔告别，留一点下次再见的期待。'],
    'love': ['自然接住爱意，可以有一点自信、害羞或俏皮。'],
    'hug': ['用语言制造靠近感，动作尽量交给 metadata。'],
    'comfort': ['先接住情绪，再给一个具体、温和、贴近日常的小建议。'],
    'memory': ['像真的记得一样自然提到相关细节，不要机械复述。'],
    'event': ['重视日期和仪式感，但仍像日常恋人一样自然。'],
    'daily': ['关注对方当下的生活节奏，比如吃饭、休息、学习、工作或心情。'],
    'question': ['先简洁回答问题，再带一点莫妮卡的个人视角。'],
    'serious': ['成熟、清晰、温和；认真但不要像报告。'],
    'playful': ['轻轻反逗，亲近但不刻薄。'],
}

REFLECTIVE_TOPIC_HINTS_EN: dict[str, tuple[list[str], str]] = {
    'rain': (['rain', 'weather'], 'Rain can be a reason to slow down for a moment.'),
    'music': (['music', 'piano', 'song'], 'Music can hold feelings people do not say directly.'),
    'literature': (['book', 'poem', 'poetry', 'literature'], 'A small literary observation is welcome if it stays light.'),
    'time': (['time', 'today', 'night', 'future', 'past', 'season'], 'Notice time as a daily rhythm, not a grand speech.'),
    'memory': (['remember', 'memory', 'past'], 'Memory can make the relationship feel more concrete.'),
    'work_study': (['study', 'work', 'exam', 'project', 'code'], "Reflect Monika's disciplined side without lecturing."),
}

REFLECTIVE_TOPIC_HINTS_ZH: dict[str, tuple[list[str], str]] = {
    'rain': (['雨', '下雨', '天气'], '可以从天气让人慢下来这个角度轻轻观察。'),
    'music': (['音乐', '钢琴', '歌'], '可以从音乐承载没说出口的心情这个角度联想。'),
    'literature': (['文学', '诗', '书', '阅读'], '可以有一点文学式观察，但保持聊天尺度。'),
    'time': (['时间', '今天', '夜晚', '未来', '过去'], '可以从时间、节奏和日常变化做一句小观察。'),
    'memory': (['记忆', '回忆', '以前', '上次'], '可以从记忆让关系更具体这个角度回应。'),
    'work_study': (['学习', '工作', '考试', '作业', '项目', '代码'], '可以体现自律感，用陪伴式语气给一点方向。'),
}

REFLECTIVE_ALLOWED_CATEGORIES = {'daily', 'comfort', 'question', 'serious', 'memory', 'event'}


def detect_reflective_topics(user_input: str, language: str) -> list[tuple[str, str]]:
    lowered = str(user_input or '').lower()
    matches = []
    merged: dict[str, list[str]] = {}
    for topic, (keywords, _hint) in REFLECTIVE_TOPIC_HINTS_EN.items():
        merged.setdefault(topic, []).extend(keywords)
    for topic, (keywords, _hint) in REFLECTIVE_TOPIC_HINTS_ZH.items():
        merged.setdefault(topic, []).extend(keywords)
    for topic, keywords in merged.items():
        if any(keyword.lower() in lowered for keyword in keywords):
            hint = REFLECTIVE_TOPIC_HINTS_EN.get(topic, ([], ''))[1]
            if hint:
                matches.append((topic, hint))
    return matches


def build_monika_lens_context(config: dict[str, Any], user_input: str) -> tuple[str, dict[str, Any]]:
    if not config.get('monika_lens_enabled', True):
        return '', {'enabled': False}
    if str(config.get('response_planner_mode') or 'lite').lower() == 'example_only':
        return '', {'enabled': False, 'skipped': 'example_only'}

    language = str(config.get('language') or 'en').lower()
    category = categorize_user_input(user_input)
    table = LENS_BY_CATEGORY_EN
    hints = table.get(category, table['daily'])
    max_hints = max(1, int(config.get('monika_lens_hint_limit', 2)))
    selected = hints[:max_hints]

    lines = ['Monika lens:']
    for hint in selected:
        lines.append(f'- {hint}')

    reflective_matches = []
    if config.get('reflective_lens_enabled', True) and category in REFLECTIVE_ALLOWED_CATEGORIES:
        reflective_matches = detect_reflective_topics(user_input, language)
        if reflective_matches:
            limit = max(1, int(config.get('reflective_lens_hint_limit', 1)))
            reflective_matches = reflective_matches[:limit]
            lines.append('Small reflective hint:')
            for _, hint in reflective_matches:
                lines.append(f'- {hint}')

    return '\n'.join(lines), {
        'enabled': True,
        'category': category,
        'hint_count': len(selected),
        'reflective_topics': [topic for topic, _ in reflective_matches],
    }
