from __future__ import annotations
from html import escape
import json
from typing import Any

PRIORITY_LABELS = {'high': '高', 'medium': '中', 'low': '低', 'none': '暂无错题'}
SEVERITY_LABELS = {'warning': '需注意', 'info': '提示'}

WITHHELD_ANSWER = '（未通过复核，答案暂不展示）'

def text(value: Any) -> str:
    return '' if value is None else str(value)


def answer_or_withheld(question: dict[str, Any]) -> str:
    if question.get('answer_withheld'):
        return WITHHELD_ANSWER + '：' + '；'.join(question.get('withheld_reason', []))
    return text(question.get('answer'))


def generate_report_summary(client: Any, report: dict[str, Any]) -> str | None:
    """Ask the report-writing model role for a short plain-language overview of `report`.

    Only reads already-computed structured fields — never invents numbers. Returns None on any
    failure (unconfigured client, network error, malformed response); callers should treat that
    exactly like an absent `summary` and render nothing, the same way an unreviewed verification
    answer is withheld rather than guessed.
    """
    system = (
        '你是学习报告撰写助手。根据结构化的错题诊断数据，写一段面向学生和家长的概述，'
        '客观陈述错题数量、主要错因和复习重点，不要编造数据中没有出现的信息，不超过150字。'
        '严格只返回 JSON，不要输出其它文字：{"summary": string}'
    )
    payload = {
        'wrong_question_count': report.get('wrong_question_count'),
        'root_cause_counts': report.get('root_cause_counts'),
        'top_review_priorities': [
            {'chapter': item.get('chapter'), 'name': item.get('name'), 'review_priority': item.get('review_priority')}
            for item in (report.get('review_order') or [])[:5]
        ],
        'quality_flags': [flag.get('message') for flag in (report.get('quality_flags') or [])],
    }
    result = client.complete_json(system, json.dumps(payload, ensure_ascii=False))
    if not isinstance(result, dict):
        return None
    summary = result.get('summary')
    return summary.strip() if isinstance(summary, str) and summary.strip() else None


def render_markdown(report: dict[str, Any]) -> str:
    lines = ['# Exam Diagnosis 报告', '', f"- 错题数：{report.get('wrong_question_count', 0)}"]
    versions = report.get('exam_versions')
    if versions:
        rendered = '；'.join('{} v{}'.format(text(item.get('name')), item.get('version')) for item in versions)
        lines.append(f'- 分析依据的考试解析版本：{rendered}')
    lines.append('')
    summary = report.get('summary')
    if summary:
        lines.extend(['## 概述', summary, ''])
    flags = report.get('quality_flags')
    if flags:
        lines.append('## 结论可信度提示')
        for flag in flags:
            lines.append(f"- [{SEVERITY_LABELS.get(flag.get('severity'), flag.get('severity', ''))}] {flag.get('message', '')}")
        lines.append('')
    scope = report.get('scope_coverage')
    if scope:
        lines.extend([
            '## 课程范围与资料覆盖范围',
            f"- 课程范围知识点：{scope.get('curriculum_point_count', 0)}",
            f"- 本次资料考查到：{scope.get('assessed_point_count', 0)}（覆盖率 {scope.get('coverage_ratio', 0)}）",
            f"- 本次资料未考查：{scope.get('not_assessed_point_count', 0)}",
            f"- 未考查章节：{'、'.join(scope.get('not_assessed_chapters', [])) or '无'}",
            f"- {scope.get('note', '')}",
            '',
        ])
    lines.append('## 错因专题')
    for cluster in report.get('clusters', []):
        lines.append(f"- {cluster.get('root_cause', '待人工确认')}（优先级 {cluster.get('priority', 0)}）：来源题目 {', '.join(cluster.get('question_ids', []))}；规避建议：{cluster.get('avoidance_advice', '')}")
        for evidence in cluster.get('evidence', []):
            lines.append(f"  - 原题证据：{evidence.get('original_source', {})}")
        for example in cluster.get('question_examples', []):
            lines.append(f"  - 题干：{example.get('question_text', '')}；学生答案：{example.get('student_answer', '')}")
    lines.extend(['', '## 知识树'])
    for chapter, points in report.get('knowledge_tree', {}).items():
        lines.append(f'### {chapter}')
        for name, stats in points.items():
            lines.append(f"- {name}：错题 {stats.get('wrong_count', 0)}/{stats.get('question_count', 0)}；错误率 {stats.get('error_rate', 0)}；复习优先级 {PRIORITY_LABELS.get(stats.get('review_priority'), '未评估')}")
    review_order = report.get('review_order')
    if review_order:
        lines.extend(['', '## 复习优先顺序'])
        for index, item in enumerate(review_order, start=1):
            lines.append(f"{index}. {item.get('chapter', '')} / {item.get('name', '')}：优先级 {PRIORITY_LABELS.get(item.get('review_priority'), '未评估')}（错题 {item.get('wrong_count', 0)}/{item.get('question_count', 0)}）")
    lines.extend(['', '## 错题集'])
    for item in report.get('wrong_question_collection', []):
        lines.append(f"- 题干：{item.get('question_text', '')}；学生答案：{item.get('student_answer', '')}；错因：{item.get('root_cause', '')}；规避建议：{item.get('avoidance_advice', '')}；原题证据：{item.get('original_source', {})}")
    lines.extend(['', '## 验证题'])
    for question in report.get('verification_questions', []):
        lines.append(f"- 题目：{question.get('question_text', question.get('prompt', ''))}；答案要点：{answer_or_withheld(question)}")
    return '\n'.join(lines) + '\n'


def render_html(report: dict[str, Any]) -> str:
    lines = [f'<h1>{escape("Exam Diagnosis 报告")}</h1>', f'<p>错题数：{report.get("wrong_question_count", 0)}</p>']
    versions = report.get('exam_versions')
    if versions:
        rendered = '；'.join('{} v{}'.format(text(item.get('name')), item.get('version')) for item in versions)
        lines.append(f'<p>分析依据的考试解析版本：{escape(rendered)}</p>')
    summary = report.get('summary')
    if summary:
        lines.append(f'<section><h2>概述</h2><p>{escape(summary)}</p></section>')
    flags = report.get('quality_flags')
    if flags:
        lines.append('<section><h2>结论可信度提示</h2><ul>')
        for flag in flags:
            lines.append(f'<li>[{escape(SEVERITY_LABELS.get(flag.get("severity"), text(flag.get("severity"))))}] {escape(text(flag.get("message")))}</li>')
        lines.append('</ul></section>')
    scope = report.get('scope_coverage')
    if scope:
        lines.append('<section><h2>课程范围与资料覆盖范围</h2><ul>')
        lines.append(f'<li>课程范围知识点：{scope.get("curriculum_point_count", 0)}</li>')
        lines.append(f'<li>本次资料考查到：{scope.get("assessed_point_count", 0)}（覆盖率 {scope.get("coverage_ratio", 0)}）</li>')
        lines.append(f'<li>本次资料未考查：{scope.get("not_assessed_point_count", 0)}</li>')
        lines.append(f'<li>未考查章节：{escape("、".join(scope.get("not_assessed_chapters", [])) or "无")}</li>')
        lines.append(f'<li>{escape(text(scope.get("note")))}</li>')
        lines.append('</ul></section>')
    lines.append('<section><h2>错因专题</h2><ul>')
    for cluster in report.get('clusters', []):
        lines.append(f'<li>{escape(cluster.get("root_cause", "待人工确认"))}（优先级 {cluster.get("priority", 0)}）：{escape(", ".join(cluster.get("question_ids", [])))}</li>')
        for evidence in cluster.get('evidence', []):
            lines.append(f'<li>原题证据：{escape(str(evidence.get("original_source", {})))}</li>')
    lines.extend(['</ul></section>', '<section><h2>知识树</h2>'])
    for chapter, points in report.get('knowledge_tree', {}).items():
        lines.append(f'<h3>{escape(chapter)}</h3><ul>')
        for name, stats in points.items():
            lines.append(f'<li>{escape(name)}：错题 {stats.get("wrong_count", 0)}/{stats.get("question_count", 0)}；错误率 {stats.get("error_rate", 0)}；复习优先级 {escape(PRIORITY_LABELS.get(stats.get("review_priority"), "未评估"))}</li>')
        lines.append('</ul>')
    lines.append('</section>')
    review_order = report.get('review_order')
    if review_order:
        lines.append('<section><h2>复习优先顺序</h2><ol>')
        for item in review_order:
            lines.append(f'<li>{escape(text(item.get("chapter")))} / {escape(text(item.get("name")))}：优先级 {escape(PRIORITY_LABELS.get(item.get("review_priority"), "未评估"))}（错题 {item.get("wrong_count", 0)}/{item.get("question_count", 0)}）</li>')
        lines.append('</ol></section>')
    lines.append('<section><h2>错题集</h2><ul>')
    for item in report.get('wrong_question_collection', []):
        lines.append(f'<li>题干：{escape(text(item.get("question_text")))}；学生答案：{escape(text(item.get("student_answer")))}；错因：{escape(text(item.get("root_cause")))}；规避建议：{escape(text(item.get("avoidance_advice")))}</li>')
    lines.append('</ul></section><section><h2>验证题</h2><ul>')
    for question in report.get('verification_questions', []):
        lines.append(f'<li>验证题：{escape(text(question.get("question_text", question.get("prompt"))))}；验证题答案：{escape(answer_or_withheld(question))}</li>')
    lines.append('</ul></section>')
    return '<!doctype html><html><head><meta charset="utf-8"><title>Exam Diagnosis</title></head><body>' + ''.join(lines) + '</body></html>'
