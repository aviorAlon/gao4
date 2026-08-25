from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def test_ui_contains_multi_exam_analysis_and_quality_fields():
    html=TestClient(app).get('/').text
    assert 'data-analysis-exam' in html
    assert 'original-source' in html
    assert '置信度' in html
    assert '与本任务科目不符' in html
    assert 'export-md' in html and 'export-html' in html


def test_ui_exposes_page_order_material_type_and_score_controls():
    html=TestClient(app).get('/').text
    assert 'page-order' in html and 'material-type' in html and 'page-include' in html
    assert 'score-obtained' in html and 'score-total' in html and 'teacher-comment' in html
    assert 'excluded-pages' in html and '不计入解析统计' in html


def test_ui_exposes_the_answer_review_gate_and_quality_flags():
    html=TestClient(app).get('/').text
    assert 'approve-answer' in html and 'review-answer' in html
    assert '未通过复核，答案暂不展示' in html
    assert 'quality-flags' in html and '结论可信度提示' in html
