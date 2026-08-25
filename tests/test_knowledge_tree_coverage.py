from exam_diagnosis.diagnosis import diagnose_records


def test_knowledge_tree_counts_all_mapped_questions_and_wrong_subset():
    result=diagnose_records([
      {'id':'q1','is_wrong_question':True,'knowledge_points':[{'knowledge_id':'k','name':'定义域','chapter':'函数'}]},
      {'id':'q2','is_wrong_question':False,'knowledge_points':[{'knowledge_id':'k','name':'定义域','chapter':'函数'}]},
    ])
    node=result['knowledge_tree']['函数']['定义域']
    assert (node['question_count'], node['wrong_count']) == (2, 1)
