from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def test_ui_contains_multi_exam_analysis_and_quality_fields():
    html=TestClient(app).get('/').text
    assert 'data-analysis-exam' in html
    assert 'original-source' in html
    assert '置信度' in html
    assert '科目不匹配' in html
    assert 'export-md' in html and 'export-html' in html
