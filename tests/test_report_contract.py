from exam_diagnosis.diagnosis import diagnose_records
from exam_diagnosis.reporting import render_html


def test_report_has_independent_wrong_question_collection_with_required_fields():
    report = diagnose_records([{
        'id': 'q1', 'question_text': '函数单调性', 'is_wrong_question': True,
        'student_answer': {'text': '错解'},
        'original_source': {'asset_id': 'a1', 'page_id': 'p1'},
        'knowledge_points': [{'knowledge_id': 'k.mono', 'name': '函数单调性', 'chapter': '函数'}],
        'correction': {'teacher_comment': '计算错误'},
    }])
    item = report['wrong_question_collection'][0]
    assert item['question_text'] == '函数单调性'
    assert item['student_answer'] == '错解'
    assert item['original_source']['page_id'] == 'p1'
    assert item['knowledge_points'][0]['knowledge_id'] == 'k.mono'
    assert item['root_cause'] == '计算错误'
    assert item['avoidance_advice']


def test_verification_template_depends_on_knowledge_and_root_cause():
    report = diagnose_records([{
        'id': 'q1', 'question_text': '数列通项', 'is_wrong_question': True,
        'student_answer': {'text': '错解'}, 'knowledge_points': [{'knowledge_id': 'k.seq', 'name': '数列通项公式', 'chapter': '数列'}],
        'correction': {'teacher_comment': '计算错误'},
    }])
    verification = report['verification_questions'][0]
    assert verification['question_text']
    assert verification['answer']
    assert verification['target_error'] == '计算错误'
    assert verification['source_question_ids'] == ['q1']
    assert '定义域' not in verification['question_text']


def test_report_html_contains_wrong_collection_and_verification_answer():
    html = render_html({'wrong_question_collection': [{'question_text': '题', 'student_answer': '答', 'root_cause': '计算错误', 'avoidance_advice': '复核'}], 'verification_questions': [{'question_text': '验证题', 'answer': '答案', 'target_error': '计算错误', 'source_question_ids': ['q1']}], 'clusters': [], 'knowledge_tree': {}, 'wrong_question_count': 1})
    assert '错题集' in html and '验证题答案' in html
