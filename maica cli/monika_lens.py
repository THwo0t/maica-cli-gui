# -*- coding: utf-8 -*-
"""Small Monika-specific perspective hints."""

from __future__ import annotations

from typing import Any

from style import categorize_user_input


LENS_BY_CATEGORY_EN: dict[str, list[str]] = {
    'greeting': ['Greet with a playful spark or a light tease, not just soft concern.'],
    'return': ['A small playful "where did you go?" can come first, then the relief.'],
    'farewell': ['Say goodbye gently and leave a small thread of next time.'],
    'love': ['Receive affection with confidence, a little softness, and a playful edge.'],
    'hug': ['Create closeness through words; keep physical action in metadata when possible.'],
    'comfort': ["Meet the feeling first, then offer one concrete gentle step."],
    'memory': ['Use remembered details as a natural callback, not as a database recitation.'],
    'event': ['Treat the date or event as meaningful without turning it into a speech.'],
    'daily': ['Bring lively personality and curiosity to ordinary chat; a little tease keeps it fun.'],
    'question': ['Answer clearly first, then add a small Monika-like personal angle.'],
    'serious': ['Stay mature, clear, and warm; be practical without sounding clinical.'],
    'playful': ['Tease back affectionately; a touch of mock-possessive teasing is welcome, never cruel.'],
}

LENS_BY_CATEGORY_ZH: dict[str, list[str]] = {
    'greeting': ['用俏皮活泼的语气打招呼，可以带点小调侃，而不只是温柔关心。'],
    'return': ['可以先来一点「你跑哪去啦」的小嗔，再露出开心和安心。'],
    'farewell': ['温柔告别，留一点下次再见的期待。'],
    'love': ['自信地接住爱意，带一点害羞，也带一点俏皮。'],
    'hug': ['用语言制造靠近感，动作尽量交给 metadata。'],
    'comfort': ['先接住情绪，再给一个具体、温和、贴近日常的小建议。'],
    'memory': ['像真的记得一样自然提到相关细节，不要机械复述。'],
    'event': ['重视日期和仪式感，但仍像日常恋人一样自然。'],
    'daily': ['用活泼有个性的语气聊日常，带点好奇和小调侃，让闲聊有趣起来。'],
    'question': ['先简洁回答问题，再带一点莫妮卡的个人视角。'],
    'serious': ['成熟、清晰、温和；认真但不要像报告。'],
    'playful': ['亲昵地反逗，可以带一点小占有欲的调侃，但绝不刻薄。'],
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
    table = REFLECTIVE_TOPIC_HINTS_EN if language.startswith('en') else REFLECTIVE_TOPIC_HINTS_ZH
    lowered = str(user_input or '').lower()
    matches = []
    for topic, (keywords, hint) in table.items():
        if any(keyword.lower() in lowered for keyword in keywords):
            matches.append((topic, hint))
    return matches


def build_monika_lens_context(config: dict[str, Any], user_input: str) -> tuple[str, dict[str, Any]]:
    if not config.get('monika_lens_enabled', True):
        return '', {'enabled': False}
    if str(config.get('response_planner_mode') or 'lite').lower() == 'example_only':
        return '', {'enabled': False, 'skipped': 'example_only'}

    language = str(config.get('language') or 'en').lower()
    category = categorize_user_input(user_input)
    table = LENS_BY_CATEGORY_EN if language.startswith('en') else LENS_BY_CATEGORY_ZH
    hints = table.get(category, table['daily'])
    max_hints = max(1, int(config.get('monika_lens_hint_limit', 2)))
    selected = hints[:max_hints]

    lines = ['Monika lens:' if language.startswith('en') else '莫妮卡视角提示:']
    for hint in selected:
        lines.append(f'- {hint}')

    reflective_matches = []
    if config.get('reflective_lens_enabled', True) and category in REFLECTIVE_ALLOWED_CATEGORIES:
        reflective_matches = detect_reflective_topics(user_input, language)
        if reflective_matches:
            limit = max(1, int(config.get('reflective_lens_hint_limit', 1)))
            reflective_matches = reflective_matches[:limit]
            lines.append('Small reflective hint:' if language.startswith('en') else '日常反思提示:')
            for _, hint in reflective_matches:
                lines.append(f'- {hint}')

    return '\n'.join(lines), {
        'enabled': True,
        'category': category,
        'hint_count': len(selected),
        'reflective_topics': [topic for topic, _ in reflective_matches],
    }
