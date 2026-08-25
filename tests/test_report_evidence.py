from exam_diagnosis.diagnosis import diagnose_records


def test_cluster_contains_original_asset_page_evidence_and_report_answer():
    result=diagnose_records([{'id':'q1','is_wrong_question':True,'question_text':'题干','student_answer':{'text':'答案'},'original_source':{'asset_id':'a1','page_id':'p1'},'knowledge_points':[{'knowledge_id':'k','name':'知识','chapter':'章'}]}])
    cluster=result['clusters'][0]
    assert cluster['evidence'][0]['original_source']['page_id']=='p1'
    assert result['verification_questions'][0]['answer']
