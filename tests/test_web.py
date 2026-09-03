from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def test_report_can_be_exported_as_markdown_and_html():
    response = TestClient(app).get('/api/reports/missing/export?format=md')
    assert response.status_code == 404
