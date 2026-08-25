from __future__ import annotations
from html import escape
from typing import Any

def text(value: Any) -> str:
    return '' if value is None else str(value)


def render_markdown(report: dict[str, Any]) -> str:
    lines = ['# Exam Diagnosis 报告', '', f"- 错题数：{report.get('wrong_question_count', 0)}", '', '## 错因专题']
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
            lines.append(f"- {name}：错题 {stats.get('wrong_count', 0)}")
    lines.extend(['', '## 错题集'])
    for item in report.get('wrong_question_collection', []):
        lines.append(f"- 题干：{item.get('question_text', '')}；学生答案：{item.get('student_answer', '')}；错因：{item.get('root_cause', '')}；规避建议：{item.get('avoidance_advice', '')}；原题证据：{item.get('original_source', {})}")
    lines.extend(['', '## 验证题'])
    for question in report.get('verification_questions', []):
        lines.append(f"- 题目：{question.get('question_text', question.get('prompt', ''))}；答案要点：{question.get('answer', '')}")
    return '\n'.join(lines) + '\n'


def render_html(report: dict[str, Any]) -> str:
    lines = [f'<h1>{escape("Exam Diagnosis 报告")}</h1>', f'<p>错题数：{report.get("wrong_question_count", 0)}</p>', '<section><h2>错因专题</h2><ul>']
    for cluster in report.get('clusters', []):
        lines.append(f'<li>{escape(cluster.get("root_cause", "待人工确认"))}（优先级 {cluster.get("priority", 0)}）：{escape(", ".join(cluster.get("question_ids", [])))}</li>')
        for evidence in cluster.get('evidence', []):
            lines.append(f'<li>原题证据：{escape(str(evidence.get("original_source", {})))}</li>')
    lines.extend(['</ul></section>', '<section><h2>知识树</h2>'])
    for chapter, points in report.get('knowledge_tree', {}).items():
        lines.append(f'<h3>{escape(chapter)}</h3><ul>')
        for name, stats in points.items():
            lines.append(f'<li>{escape(name)}：错题 {stats.get("wrong_count", 0)}</li>')
        lines.append('</ul>')
    lines.append('</section><section><h2>错题集</h2><ul>')
    for item in report.get('wrong_question_collection', []):
        lines.append(f'<li>题干：{escape(text(item.get("question_text")))}；学生答案：{escape(text(item.get("student_answer")))}；错因：{escape(text(item.get("root_cause")))}；规避建议：{escape(text(item.get("avoidance_advice")))}</li>')
    lines.append('</ul></section><section><h2>验证题</h2><ul>')
    for question in report.get('verification_questions', []):
        lines.append(f'<li>验证题：{escape(text(question.get("question_text", question.get("prompt"))))}；验证题答案：{escape(text(question.get("answer")))}</li>')
    lines.append('</ul></section>')
    return '<!doctype html><html><head><meta charset="utf-8"><title>Exam Diagnosis</title></head><body>' + ''.join(lines) + '</body></html>'
