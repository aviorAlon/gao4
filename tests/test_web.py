from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def test_home_page_is_usable_by_regular_user():
    response = TestClient(app).get('/')
    assert response.status_code == 200
    assert '创建诊断任务' in response.text
    assert 'exam-diagnosis' in response.text
    assert 'fetch(' in response.text
    assert '运行解析' in response.text
    assert '确认并开始分析' in response.text


def test_report_can_be_exported_as_markdown_and_html():
    response = TestClient(app).get('/api/reports/missing/export?format=md')
    assert response.status_code == 404
