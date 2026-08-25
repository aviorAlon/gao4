from __future__ import annotations
from collections import Counter, defaultdict
from typing import Any


def _cause(comment: str) -> str:
    if any(word in comment for word in ('条件', '漏', '忽略')):
        return '条件遗漏'
    if any(word in comment for word in ('算错', '计算', '运算')):
        return '计算错误'
    if any(word in comment for word in ('概念', '定义')):
        return '概念不清'
    return '待人工确认'


def review_priority(question_count: int, wrong_count: int) -> str:
    """Volume alone is misleading: a single miss out of one attempt matters more than one out of ten."""
    if not wrong_count:
        return 'none'
    error_rate = wrong_count / question_count
    if wrong_count >= 2 and error_rate >= 0.5:
        return 'high'
    if wrong_count >= 2 or error_rate >= 0.5:
        return 'medium'
    return 'low'


def diagnose_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    wrong = [record for record in records if record.get('is_wrong_question')]
    causes = Counter()
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        cause = _cause(record.get('correction', {}).get('teacher_comment', ''))
        if record.get('is_wrong_question'):
            causes[cause] += 1
        points = record.get('knowledge_points', [])
        point = points[0] if points else {'knowledge_id': 'unmapped', 'name': '待映射', 'chapter': '待映射'}
        if record.get('is_wrong_question'):
            groups[(point['knowledge_id'], cause)].append(record)
    clusters = []
    for (knowledge_id, cause), items in groups.items():
        clusters.append({
            'cluster_id': f'{knowledge_id}:{cause}', 'knowledge_id': knowledge_id,
            'root_cause': cause, 'question_ids': [item['id'] for item in items],
            'occurrence_count': len(items), 'priority': len(items) * 10,
 'avoidance_advice': f'做题时先检查{cause}，并在草稿末尾进行条件复核。',
 'question_examples': [{'question_id': item['id'], 'question_text': item.get('question_text', ''), 'student_answer': item.get('student_answer', {}).get('text', '')} for item in items],
 'evidence': [{'question_id': item['id'], 'original_source': item.get('original_source')} for item in items],
        })
    clusters.sort(key=lambda item: (-item['priority'], item['cluster_id']))
    tree: dict[str, dict[str, dict[str, int]]] = defaultdict(dict)
    for record in records:
        for point in record.get('knowledge_points', []):
            chapter = point['chapter']
            node = tree[chapter].setdefault(point['name'], {'question_count': 0, 'wrong_count': 0, 'knowledge_id': point['knowledge_id']})
            node['question_count'] += 1
            node['wrong_count'] += int(record.get('is_wrong_question', False))
    review_order = []
    for chapter, points in tree.items():
        for name, node in points.items():
            node['error_rate'] = round(node['wrong_count'] / node['question_count'], 4)
            node['review_priority'] = review_priority(node['question_count'], node['wrong_count'])
            node['priority_score'] = round(node['wrong_count'] * node['error_rate'], 4)
            review_order.append({'chapter': chapter, 'name': name, **node})
    review_order.sort(key=lambda item: (-item['priority_score'], -item['wrong_count'], item['chapter'], item['name']))
    verification = [
        {'knowledge_id': cluster['knowledge_id'], 'question_text': f"针对知识点 {cluster['knowledge_id']}，完成一道变式题并说明关键步骤。", 'answer': f"围绕 {cluster['knowledge_id']} 的定义、条件和计算步骤逐项作答。", 'target_error': cluster['root_cause'], 'source_question_ids': cluster['question_ids']}
        for cluster in clusters
    ]
    wrong_collection = []
    for record in wrong:
        cause = _cause(record.get('correction', {}).get('teacher_comment', ''))
        wrong_collection.append({'question_id': record['id'], 'question_text': record.get('question_text', ''), 'student_answer': record.get('student_answer', {}).get('text', ''), 'original_source': record.get('original_source'), 'knowledge_points': record.get('knowledge_points', []), 'root_cause': cause, 'avoidance_advice': f'做题后复核{cause}。'})
    return {'root_cause_counts': dict(causes), 'clusters': clusters, 'knowledge_tree': dict(tree), 'review_order': review_order, 'verification_questions': verification, 'wrong_question_collection': wrong_collection}
