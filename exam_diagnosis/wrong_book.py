"""The wrong-question book: every mistake a student has made, in one filterable, printable place.

Wrong questions already appear inside each report, but a report is a snapshot of one analysis.
What a student actually opens day to day is the accumulated book — "show me every 定义域 question
I've got wrong", "print the ones from the last two exams so I can redo them".

Built from confirmed question records (the durable source), enriched with root cause / explanation
/ avoidance advice from whatever reports covered those questions. A question that was never
analysed still appears — with its diagnosis fields empty rather than omitted, so the book is a
complete record of mistakes and not just of the ones that happened to be analysed.
"""
from __future__ import annotations

from typing import Any, Iterable

from .export import Block
from .trends import exam_sort_key

BOOK_TITLE = '错题本'
# The practice edition deliberately withholds the student's own wrong answer and the diagnosis:
# seeing last time's mistake while redoing the question is how you re-learn the mistake.
PRACTICE_NOTE = '练习版只保留题干，作答区留空——重做时看不到上次的错误答案和结论，才练得出东西。'
REVIEW_NOTE = '复习版包含上次的作答、错因和规避建议。'


def diagnosis_index(reports: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """question_id -> its diagnosis, latest report wins (a re-run supersedes an earlier verdict)."""
    index: dict[str, dict[str, Any]] = {}
    for report in reports:
        for item in report.get('wrong_question_collection') or []:
            question_id = item.get('question_id')
            if not question_id:
                continue
            index[question_id] = {
                'root_cause': item.get('root_cause') or '',
                'explanation': item.get('explanation') or '',
                'avoidance_advice': item.get('avoidance_advice') or '',
                'analysis_run_id': report.get('analysis_run_id'),
            }
    return index


def build_wrong_book(
    exams: Iterable[dict[str, Any]],
    records_by_exam: dict[str, list[dict[str, Any]]],
    reports: Iterable[dict[str, Any]] = (),
    *,
    knowledge_id: str | None = None,
    exam_id: str | None = None,
    root_cause: str | None = None,
) -> dict[str, Any]:
    """Newest exam first — the book opens on the most recent mistakes, which is what gets redone."""
    diagnoses = diagnosis_index(reports)
    ordered = sorted(exams, key=exam_sort_key, reverse=True)
    items: list[dict[str, Any]] = []
    for exam in ordered:
        if exam_id and exam['id'] != exam_id:
            continue
        for record in sorted(records_by_exam.get(exam['id'], []), key=lambda r: _number_key(r.get('question_number'))):
            if not record.get('is_wrong_question'):
                continue
            points = record.get('knowledge_points') or []
            if knowledge_id and not any(point.get('knowledge_id') == knowledge_id for point in points):
                continue
            diagnosis = diagnoses.get(record['id'], {})
            if root_cause and diagnosis.get('root_cause') != root_cause:
                continue
            items.append({
                'question_id': record['id'],
                'exam_id': exam['id'], 'exam_name': exam.get('name', ''), 'exam_date': exam.get('exam_date'),
                'question_number': record.get('question_number', ''),
                'question_text': record.get('question_text', ''),
                'student_answer': (record.get('student_answer') or {}).get('text', ''),
                'teacher_comment': (record.get('correction') or {}).get('teacher_comment', ''),
                'score': record.get('score') or {},
                'original_source': record.get('original_source'),
                'knowledge_points': [
                    {'knowledge_id': p.get('knowledge_id'), 'name': p.get('name'), 'chapter': p.get('chapter')}
                    for p in points
                ],
                'root_cause': diagnosis.get('root_cause', ''),
                'explanation': diagnosis.get('explanation', ''),
                'avoidance_advice': diagnosis.get('avoidance_advice', ''),
                'analysis_run_id': diagnosis.get('analysis_run_id'),
                'analysed': bool(diagnosis),
            })

    return {
        'items': items,
        'filters': {'knowledge_id': knowledge_id, 'exam_id': exam_id, 'root_cause': root_cause},
        'facets': _facets(exams, records_by_exam, diagnoses),
        'summary': {
            'total': len(items),
            'analysed': sum(1 for item in items if item['analysed']),
            'exam_count': len({item['exam_id'] for item in items}),
        },
    }


def _number_key(value: Any) -> tuple:
    """Sort 2 before 10 while still tolerating labels like '12(1)' or '附加题'."""
    text = str(value or '')
    digits = ''
    for char in text:
        if char.isdigit():
            digits += char
        elif digits:
            break
    return (0, int(digits), text) if digits else (1, 0, text)


def _facets(exams, records_by_exam, diagnoses) -> dict[str, list[dict[str, Any]]]:
    """The values actually present in this book, so the UI offers filters that can return something."""
    knowledge: dict[str, dict[str, Any]] = {}
    causes: dict[str, int] = {}
    exam_facets = []
    for exam in sorted(exams, key=exam_sort_key, reverse=True):
        wrong = [r for r in records_by_exam.get(exam['id'], []) if r.get('is_wrong_question')]
        if wrong:
            exam_facets.append({'exam_id': exam['id'], 'name': exam.get('name', ''), 'count': len(wrong)})
        for record in wrong:
            for point in record.get('knowledge_points') or []:
                entry = knowledge.setdefault(point.get('knowledge_id'), {'knowledge_id': point.get('knowledge_id'), 'name': point.get('name'), 'chapter': point.get('chapter'), 'count': 0})
                entry['count'] += 1
            cause = diagnoses.get(record['id'], {}).get('root_cause')
            if cause:
                causes[cause] = causes.get(cause, 0) + 1
    return {
        'exams': exam_facets,
        'knowledge_points': sorted(knowledge.values(), key=lambda item: -item['count']),
        'root_causes': [{'root_cause': cause, 'count': count} for cause, count in sorted(causes.items(), key=lambda kv: -kv[1])],
    }


# --- printable editions ---------------------------------------------------------------------------


def wrong_book_blocks(book: dict[str, Any], *, practice: bool = False, title: str = BOOK_TITLE) -> list[Block]:
    """Same block vocabulary as export.report_blocks, so PDF and Word rendering are shared."""
    blocks: list[Block] = [('heading', 1, title)]
    blocks.append(('paragraph', f"共 {book['summary']['total']} 道错题，来自 {book['summary']['exam_count']} 场考试"))
    blocks.append(('paragraph', PRACTICE_NOTE if practice else REVIEW_NOTE))
    current_exam = None
    for item in book['items']:
        if item['exam_id'] != current_exam:
            current_exam = item['exam_id']
            label = item['exam_name'] or '未命名考试'
            if item['exam_date']:
                label = f"{label}（{item['exam_date']}）"
            blocks.append(('heading', 2, label))
        blocks.append(('heading', 3, f"第 {item['question_number']} 题"))
        blocks.append(('paragraph', item['question_text'] or '（题干未记录）'))
        knowledge = '、'.join(point['name'] for point in item['knowledge_points'] if point.get('name'))
        if practice:
            # A blank line to write on, and nothing that would give the answer away.
            if knowledge:
                blocks.append(('paragraph', f'知识点：{knowledge}'))
            blocks.append(('paragraph', '解答：'))
            blocks.append(('paragraph', '　' * 20))
            blocks.append(('paragraph', '　' * 20))
        else:
            details = []
            if knowledge:
                details.append(f'知识点：{knowledge}')
            if item['student_answer']:
                details.append(f"上次作答：{item['student_answer']}")
            if item['root_cause']:
                details.append(f"错因：{item['root_cause']}")
            if item['explanation']:
                details.append(f"分析：{item['explanation']}")
            if item['avoidance_advice']:
                details.append(f"规避建议：{item['avoidance_advice']}")
            if item['teacher_comment']:
                details.append(f"老师批注：{item['teacher_comment']}")
            if not item['analysed']:
                details.append('这道题还没有经过分析，只有原始记录')
            blocks.append(('bullets', details, 1))
    return blocks
