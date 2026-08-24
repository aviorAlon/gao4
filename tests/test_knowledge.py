from exam_diagnosis.knowledge import match_knowledge_points


def test_matches_high_school_guangdong_math_function_question():
    matches = match_knowledge_points(
        stage='high', subject='数学', province='广东',
        question_text='函数 f(x) 的定义域与单调性',
    )
    assert matches[0]['knowledge_id'] == 'high.math.function.domain'
    assert matches[0]['confidence'] > 0.8
    assert matches[0]['source'] == 'public_registry'


def test_unknown_question_returns_review_required_without_inventing_mapping():
    matches = match_knowledge_points(
        stage='high', subject='数学', province='广东',
        question_text='一个无法识别的特殊题目',
    )
    assert matches == []
