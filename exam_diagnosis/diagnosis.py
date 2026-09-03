from __future__ import annotations
from collections import Counter, defaultdict
from typing import Any
import json

CAUSE_CATEGORIES = ('条件遗漏', '计算错误', '概念不清', '审题错误', '书写不规范', '待人工确认')


def _cause(comment: str) -> str:
    if any(word in comment for word in ('条件', '漏', '忽略')):
        return '条件遗漏'
    if any(word in comment for word in ('算错', '计算', '运算')):
        return '计算错误'
    if any(word in comment for word in ('概念', '定义')):
        return '概念不清'
    return '待人工确认'


def _model_diagnose(client: Any, record: dict[str, Any]) -> dict[str, Any] | None:
    """Ask the analysis-role model for this question's root cause. None on any failure — the
    caller falls back to `_cause`, mirroring the vision parser's never-fake-success rule."""
    system = (
        '你是高中数学错题诊断专家。只根据给定的题目、学生作答和教师批注判断错误根因，不要编造数据中没有的信息。'
        '根因必须从这些类别中选择一个：' + '、'.join(CAUSE_CATEGORIES) + '。'
        '严格只返回 JSON，不要输出其它文字：'
        '{"root_cause": 类别之一, "explanation": 针对这道题的具体错误分析（不超过80字）, '
        '"avoidance_advice": 面向这道题的具体规避建议（不超过60字）}'
    )
    user = json.dumps({
        'question_text': record.get('question_text', ''),
        'student_answer': record.get('student_answer', {}).get('text', ''),
        'teacher_comment': record.get('correction', {}).get('teacher_comment', ''),
    }, ensure_ascii=False)
    result = client.complete_json(system, user)
    if not isinstance(result, dict):
        return None
    root_cause, explanation, advice = result.get('root_cause'), result.get('explanation'), result.get('avoidance_advice')
    if root_cause not in CAUSE_CATEGORIES or not isinstance(explanation, str) or not isinstance(advice, str) or not advice.strip():
        return None
    return {'root_cause': root_cause, 'explanation': explanation, 'avoidance_advice': advice, 'model_generated': True}


def _model_generate_verification(client: Any, cluster: dict[str, Any]) -> dict[str, Any] | None:
    """Ask the verify-role model for one real variant question targeting this error cluster,
    with a fully worked reference answer — replacing the fixed template sentence this function's
    caller otherwise falls back to. None on any failure, same never-fake-success rule as every
    other model integration here: a generated question is only ever used if it's real."""
    system = (
        '你是高中数学教师，要针对学生的一类错误专题出一道变式练习题，帮助巩固这个知识点、避免同类错误。'
        '题目必须是一道完整、可解的数学题，难度和考察点要跟给出的错因专题匹配，不能与原错题一字不差；'
        '给出完整、正确的参考答案，写清关键步骤；不要编造材料里没有的信息。'
        '严格只返回 JSON，不要输出其它文字：{"question_text": 完整题干, "answer": 完整参考答案（含关键步骤）}'
    )
    user = json.dumps({
        'knowledge_id': cluster['knowledge_id'],
        'root_cause': cluster['root_cause'],
        'source_questions': [
            {'question_text': ex.get('question_text', ''), 'student_answer': ex.get('student_answer', '')}
            for ex in cluster.get('question_examples', [])
        ],
    }, ensure_ascii=False)
    result = client.complete_json(system, user)
    if not isinstance(result, dict):
        return None
    question_text, answer = result.get('question_text'), result.get('answer')
    if not isinstance(question_text, str) or not question_text.strip() or not isinstance(answer, str) or not answer.strip():
        return None
    return {'question_text': question_text.strip(), 'answer': answer.strip()}


def _resolve_cause(client: Any, record: dict[str, Any], cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rid = record['id']
    if rid in cache:
        return cache[rid]
    diagnosis = _model_diagnose(client, record) if client is not None else None
    if diagnosis is None:
        diagnosis = {'root_cause': _cause(record.get('correction', {}).get('teacher_comment', '')), 'explanation': '', 'model_generated': False}
    cache[rid] = diagnosis
    return diagnosis


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


def diagnose_records(records: list[dict[str, Any]], client: Any = None, verify_client: Any = None) -> dict[str, Any]:
    """`client`, when given, is a llm.ChatClient bound to the 'analysis' role: it upgrades
    root-cause classification from keyword matching to real model reasoning, per wrong question,
    with results cached by record id. `verify_client`, bound to the 'verify' role, upgrades each
    error cluster's verification question from a fixed template sentence to a real generated
    variant problem with a worked answer. Both left as None (default), behavior is byte-for-byte
    the same as the rule-based baseline — every existing caller keeps working without them."""
    wrong = [record for record in records if record.get('is_wrong_question')]
    diag_cache: dict[str, dict[str, Any]] = {}
    causes = Counter()
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if not record.get('is_wrong_question'):
            continue
        cause = _resolve_cause(client, record, diag_cache)['root_cause']
        points = record.get('knowledge_points', [])
        point = points[0] if points else {'knowledge_id': 'unmapped', 'name': '待映射', 'chapter': '待映射'}
        causes[cause] += 1
        groups[(point['knowledge_id'], cause)].append(record)
    clusters = []
    for (knowledge_id, cause), items in groups.items():
        model_advice = next((diag_cache[item['id']]['avoidance_advice'] for item in items if diag_cache[item['id']]['model_generated']), None)
        clusters.append({
            'cluster_id': f'{knowledge_id}:{cause}', 'knowledge_id': knowledge_id,
            'root_cause': cause, 'question_ids': [item['id'] for item in items],
            'occurrence_count': len(items), 'priority': len(items) * 10,
 'avoidance_advice': model_advice or f'做题时先检查{cause}，并在草稿末尾进行条件复核。',
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
    verification = []
    for cluster in clusters:
        generated = _model_generate_verification(verify_client, cluster) if verify_client is not None else None
        if generated is None:
            generated = {
                'question_text': f"针对知识点 {cluster['knowledge_id']}，完成一道变式题并说明关键步骤。",
                'answer': f"围绕 {cluster['knowledge_id']} 的定义、条件和计算步骤逐项作答。",
            }
        verification.append({
            'knowledge_id': cluster['knowledge_id'], 'question_text': generated['question_text'],
            'answer': generated['answer'], 'target_error': cluster['root_cause'],
            'source_question_ids': cluster['question_ids'],
        })
    wrong_collection = []
    for record in wrong:
        diag = _resolve_cause(client, record, diag_cache)
        cause = diag['root_cause']
        advice = diag['avoidance_advice'] if diag['model_generated'] else f'做题后复核{cause}。'
        wrong_collection.append({'question_id': record['id'], 'question_text': record.get('question_text', ''), 'student_answer': record.get('student_answer', {}).get('text', ''), 'original_source': record.get('original_source'), 'knowledge_points': record.get('knowledge_points', []), 'root_cause': cause, 'avoidance_advice': advice, 'explanation': diag.get('explanation', '')})
    return {'root_cause_counts': dict(causes), 'clusters': clusters, 'knowledge_tree': dict(tree), 'review_order': review_order, 'verification_questions': verification, 'wrong_question_collection': wrong_collection}
