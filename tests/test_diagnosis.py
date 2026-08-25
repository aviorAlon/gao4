from exam_diagnosis.diagnosis import diagnose_records


def test_diagnosis_classifies_root_causes_clusters_repetition_and_priority():
    records = [
        {'id': 'q1', 'question_text': '函数定义域', 'is_wrong_question': True,
         'knowledge_points': [{'knowledge_id': 'k.domain', 'name': '定义域', 'chapter': '函数'}],
         'correction': {'teacher_comment': '漏写分母不为零'}},
        {'id': 'q2', 'question_text': '函数定义域', 'is_wrong_question': True,
         'knowledge_points': [{'knowledge_id': 'k.domain', 'name': '定义域', 'chapter': '函数'}],
         'correction': {'teacher_comment': '漏写分母不为零'}},
        {'id': 'q3', 'question_text': '函数单调性', 'is_wrong_question': True,
         'knowledge_points': [{'knowledge_id': 'k.mono', 'name': '单调性', 'chapter': '函数'}],
         'correction': {'teacher_comment': '计算错误'}},
    ]
    result = diagnose_records(records)
    assert result['root_cause_counts']['条件遗漏'] == 2
    assert result['clusters'][0]['question_ids'] == ['q1', 'q2']
    assert result['clusters'][0]['priority'] > result['clusters'][1]['priority']
    assert result['knowledge_tree']['函数']['定义域']['wrong_count'] == 2
    assert result['verification_questions'][0]['knowledge_id'] == 'k.domain'
