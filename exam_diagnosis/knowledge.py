from typing import Any


PUBLIC_KNOWLEDGE = [
    {
        'knowledge_id': 'high.math.function.domain',
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'name': '函数定义域', 'chapter': '函数',
        'keywords': ('定义域', '函数的定义域'),
        'confidence': 0.94,
    },
    {
        'knowledge_id': 'high.math.function.monotonicity',
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'name': '函数单调性', 'chapter': '函数',
        'keywords': ('单调性', '单调递增', '单调递减'),
        'confidence': 0.92,
    },
    {
        'knowledge_id': 'high.math.sequence.general_term',
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'name': '数列通项公式', 'chapter': '数列',
        'keywords': ('数列', '通项'),
        'confidence': 0.88,
    },
]


def match_knowledge_points(*, stage: str, subject: str, province: str, question_text: str) -> list[dict[str, Any]]:
    matches = []
    for item in PUBLIC_KNOWLEDGE:
        if item['stage'] != stage or item['subject'] != subject or item['province'] != province:
            continue
        hits = [keyword for keyword in item['keywords'] if keyword in question_text]
        if hits:
            matches.append({
                'knowledge_id': item['knowledge_id'], 'name': item['name'],
                'chapter': item['chapter'], 'confidence': item['confidence'],
                'matched_keywords': hits, 'source': 'public_registry',
            })
    return sorted(matches, key=lambda item: item['confidence'], reverse=True)
