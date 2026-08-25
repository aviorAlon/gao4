"""Independent review stage: checks answers, textbook mapping, clusters and verification questions."""
from __future__ import annotations

from typing import Any

APPROVED = 'approved'
NEEDS_REVIEW = 'needs_review'

FINDING_MESSAGES = {
    'answer_missing': '参考答案为空，不能直接发给学生',
    'knowledge_unmapped': '知识点不在所选公共知识库分区内，教材映射无法核对',
    'evidence_missing': '没有引用任何来源题目，违反证据规则',
    'textbook_chapter_missing': '知识点缺少教材章节，教材映射不可核对',
}


def review_verification_question(question: dict[str, Any], known_points: dict[str, dict]) -> list[str]:
    findings = []
    if not str(question.get('answer') or '').strip():
        findings.append('answer_missing')
    point = known_points.get(question.get('knowledge_id'))
    if point is None:
        findings.append('knowledge_unmapped')
    elif not point.get('textbook_chapter'):
        findings.append('textbook_chapter_missing')
    if not question.get('source_question_ids'):
        findings.append('evidence_missing')
    return findings


def review_report(report: dict[str, Any], partition) -> dict[str, Any]:
    """Stamps every verification question with a review verdict and returns the run-level summary."""
    known_points = {point['knowledge_id']: point for point in partition.points}
    findings = []
    for cluster in report.get('clusters', []):
        if cluster['knowledge_id'] not in known_points:
            findings.append({'code': 'cluster_knowledge_unmapped', 'target': cluster['cluster_id'], 'message': f"错因专题 {cluster['cluster_id']} 的知识点不在公共知识库分区内"})
        if not cluster.get('question_ids'):
            findings.append({'code': 'cluster_evidence_missing', 'target': cluster['cluster_id'], 'message': f"错因专题 {cluster['cluster_id']} 没有引用来源题目"})
    for question in report.get('verification_questions', []):
        problems = review_verification_question(question, known_points)
        question['review_status'] = NEEDS_REVIEW if problems else APPROVED
        question['review_findings'] = problems
        for problem in problems:
            findings.append({'code': problem, 'target': question.get('knowledge_id'), 'message': f"验证题（{question.get('knowledge_id')}）{FINDING_MESSAGES[problem]}"})
    pending = [item for item in report.get('verification_questions', []) if item['review_status'] == NEEDS_REVIEW]
    return {
        'status': NEEDS_REVIEW if findings else APPROVED,
        'checked': {'clusters': len(report.get('clusters', [])), 'verification_questions': len(report.get('verification_questions', []))},
        'pending_verification_questions': len(pending),
        'findings': findings,
    }


def withhold_unreviewed_answer(question: dict[str, Any]) -> dict[str, Any]:
    """An unreviewed reference answer must never reach the learner as if it were verified."""
    if question.get('review_status', APPROVED) == APPROVED:
        return {**question, 'answer_withheld': False}
    return {
        **question, 'answer': None, 'answer_withheld': True,
        'withheld_reason': [FINDING_MESSAGES.get(code, code) for code in question.get('review_findings', [])],
    }
