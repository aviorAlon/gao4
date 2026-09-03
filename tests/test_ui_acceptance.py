from exam_diagnosis.reporting import render_html


def test_report_html_uses_semantic_sections_and_escapes_values():
    html = render_html({'wrong_question_count': 1, 'clusters': [{'root_cause': '<bad>', 'priority': 3, 'question_ids': ['q1'], 'avoidance_advice': 'check'}], 'knowledge_tree': {}, 'verification_questions': []})
    assert '<section' in html
    assert '<h2>错因专题</h2>' in html
    assert '<h2>知识树</h2>' in html
    assert '<h2>验证题</h2>' in html
    assert '&lt;bad&gt;' in html
    assert '<li>' in html
