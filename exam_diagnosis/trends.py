"""Longitudinal view across the exams of one submission.

A single report answers "what went wrong this time". This module answers the question a parent
or teacher actually asks next — "is it getting better?" — by lining the exams up in time order
and following each knowledge point across them.

Built from confirmed question records, not from analysis reports: records exist as soon as an
exam is confirmed, so a trend appears without paying for an analysis run. Root-cause counts are
folded in from whatever reports do exist, and exams without one are marked `analysed: false`
rather than being silently counted as having no root causes.

The honesty problem here is comparability. Two exams are not a controlled experiment: they have
different difficulty, different coverage, and a knowledge point may be assessed by one question
in one and five in the next. So every trend carries the raw counts it was computed from, a
direction is only claimed when at least two exams actually assessed the point, and
`MIN_QUESTIONS_FOR_DIRECTION` keeps a single lucky question from being read as progress.
"""
from __future__ import annotations

from typing import Any, Iterable

# How far an error rate has to move before it's called a change rather than noise.
DIRECTION_THRESHOLD = 0.2
# A knowledge point needs at least this many questions on each side before a direction is claimed.
MIN_QUESTIONS_FOR_DIRECTION = 1
# Assessed in at least this many exams (and still wrong in the latest) to count as persistent.
PERSISTENT_MIN_EXAMS = 2

COMPARABILITY_NOTE = (
    '不同考试的难度、题量和覆盖范围不同，错误率的变化只能作为参考信号，不能当作严格的进步测量；'
    '每条趋势都附带了原始题数，请结合题数判断可信度。'
)


def exam_sort_key(exam: dict[str, Any]) -> tuple:
    """Chronological order: exam_date when the user supplied one, otherwise the sequence number
    they were created in. Exams without a date sort after dated ones of the same sequence, so a
    half-filled submission still lines up sensibly."""
    date = (exam.get('exam_date') or '').strip()
    return (0 if date else 1, date, exam.get('sequence_no') or 0, exam.get('id') or '')


def _rate(wrong: int, total: int) -> float:
    return round(wrong / total, 4) if total else 0.0


def _direction(first: dict[str, Any], last: dict[str, Any]) -> tuple[str, float | None]:
    """'improving' / 'worsening' / 'stable', or 'insufficient' when the data can't support a claim."""
    if first is last or first['question_count'] < MIN_QUESTIONS_FOR_DIRECTION or last['question_count'] < MIN_QUESTIONS_FOR_DIRECTION:
        return 'insufficient', None
    delta = round(last['error_rate'] - first['error_rate'], 4)
    if delta <= -DIRECTION_THRESHOLD:
        return 'improving', delta
    if delta >= DIRECTION_THRESHOLD:
        return 'worsening', delta
    return 'stable', delta


def build_trends(
    exams: Iterable[dict[str, Any]],
    records_by_exam: dict[str, list[dict[str, Any]]],
    reports: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """`records_by_exam` holds the confirmed question records of each exam; `reports` are any
    generated analysis reports (used only to attribute root causes to the exams they covered)."""
    ordered = sorted(exams, key=exam_sort_key)

    # Which exams each report covered, so root causes land on the right point in time.
    causes_by_exam: dict[str, dict[str, int]] = {}
    analysed: set[str] = set()
    for report in reports:
        exam_ids = [item.get('exam_id') for item in report.get('exam_versions') or [] if item.get('exam_id')]
        analysed.update(exam_ids)
        by_question = {item.get('question_id'): item.get('root_cause') for item in report.get('wrong_question_collection') or []}
        for exam_id in exam_ids:
            bucket = causes_by_exam.setdefault(exam_id, {})
            for record in records_by_exam.get(exam_id, []):
                cause = by_question.get(record['id'])
                if cause:
                    bucket[cause] = bucket.get(cause, 0) + 1

    exam_points: list[dict[str, Any]] = []
    # knowledge_id -> {meta, points: {exam_id: {...}}}
    knowledge: dict[str, dict[str, Any]] = {}
    for exam in ordered:
        records = records_by_exam.get(exam['id'], [])
        wrong = sum(1 for record in records if record.get('is_wrong_question'))
        exam_points.append({
            'exam_id': exam['id'], 'name': exam.get('name', ''),
            'sequence_no': exam.get('sequence_no'), 'exam_date': exam.get('exam_date'),
            'exam_type': exam.get('exam_type'), 'version': exam.get('version', 1),
            'question_count': len(records), 'wrong_count': wrong, 'error_rate': _rate(wrong, len(records)),
            'root_cause_counts': causes_by_exam.get(exam['id'], {}),
            'analysed': exam['id'] in analysed,
        })
        for record in records:
            for point in record.get('knowledge_points') or []:
                entry = knowledge.setdefault(point['knowledge_id'], {
                    'knowledge_id': point['knowledge_id'], 'name': point.get('name', ''),
                    'chapter': point.get('chapter', ''), 'points': {},
                })
                bucket = entry['points'].setdefault(exam['id'], {'exam_id': exam['id'], 'name': exam.get('name', ''), 'question_count': 0, 'wrong_count': 0})
                bucket['question_count'] += 1
                bucket['wrong_count'] += int(bool(record.get('is_wrong_question')))

    knowledge_trends = []
    for entry in knowledge.values():
        series = [entry['points'][exam['exam_id']] for exam in exam_points if exam['exam_id'] in entry['points']]
        for bucket in series:
            bucket['error_rate'] = _rate(bucket['wrong_count'], bucket['question_count'])
        direction, delta = _direction(series[0], series[-1])
        knowledge_trends.append({
            'knowledge_id': entry['knowledge_id'], 'name': entry['name'], 'chapter': entry['chapter'],
            'points': series, 'exam_count': len(series),
            'total_question_count': sum(item['question_count'] for item in series),
            'total_wrong_count': sum(item['wrong_count'] for item in series),
            'first_error_rate': series[0]['error_rate'], 'last_error_rate': series[-1]['error_rate'],
            'delta': delta, 'direction': direction,
            # Still wrong in the most recent exam that assessed it, after showing up in several —
            # the "old problem that hasn't gone away" list, which is what deserves attention.
            'persistent': len(series) >= PERSISTENT_MIN_EXAMS and series[-1]['wrong_count'] > 0 and sum(1 for item in series if item['wrong_count'] > 0) >= PERSISTENT_MIN_EXAMS,
        })
    # Worst last, most-recent-error first: what to work on now sorts to the top.
    knowledge_trends.sort(key=lambda item: (-item['last_error_rate'], -item['total_wrong_count'], item['knowledge_id']))

    with_questions = [item for item in exam_points if item['question_count']]
    overall_direction, overall_delta = 'insufficient', None
    if len(with_questions) >= 2:
        overall_direction, overall_delta = _direction(with_questions[0], with_questions[-1])

    return {
        'exams': exam_points,
        'knowledge_trends': knowledge_trends,
        'summary': {
            'exam_count': len(exam_points),
            'exams_with_questions': len(with_questions),
            'analysed_exam_count': len(analysed & {item['exam_id'] for item in exam_points}),
            'total_question_count': sum(item['question_count'] for item in exam_points),
            'total_wrong_count': sum(item['wrong_count'] for item in exam_points),
            'first_error_rate': with_questions[0]['error_rate'] if with_questions else None,
            'last_error_rate': with_questions[-1]['error_rate'] if with_questions else None,
            'overall_delta': overall_delta,
            'overall_direction': overall_direction,
            'improving': [item['knowledge_id'] for item in knowledge_trends if item['direction'] == 'improving'],
            'worsening': [item['knowledge_id'] for item in knowledge_trends if item['direction'] == 'worsening'],
            'persistent': [item['knowledge_id'] for item in knowledge_trends if item['persistent']],
            'root_cause_counts': _merge_causes(exam_points),
        },
        'note': COMPARABILITY_NOTE,
    }


def _merge_causes(exam_points: list[dict[str, Any]]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for exam in exam_points:
        for cause, count in exam['root_cause_counts'].items():
            merged[cause] = merged.get(cause, 0) + count
    return merged
