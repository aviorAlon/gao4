from exam_diagnosis.reporting import render_markdown, render_html


def test_report_exports_markdown_and_html_with_sources_and_priority():
    report = {'wrong_question_count': 1, 'clusters': [{'root_cause': '条件遗漏', 'priority': 10, 'question_ids': ['q1']}], 'knowledge_tree': {'函数': {'定义域': {'wrong_count': 1}}}}
    markdown = render_markdown(report)
    html = render_html(report)
    assert '条件遗漏' in markdown
    assert 'q1' in markdown
    assert '<html' in html.lower()
    assert '定义域' in html
