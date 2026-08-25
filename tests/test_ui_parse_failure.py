from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def test_ui_parse_failure_has_retry_and_manual_empty_draft_path():
    html=TestClient(app).get('/').text
    assert 'retry-run' in html
    assert '解析失败：' in html
    assert "document.querySelector('#draft').innerHTML=''" in html
    assert 'manual-question' in html


def test_ui_job_progress_shows_all_stage_statuses():
    html=TestClient(app).get('/').text
    assert 'job-progress' in html
    assert 'result.stages.map' in html
