from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def test_ui_exposes_verification_answer_submission_and_mastery_status():
    html=TestClient(app).get('/').text
    for marker in ('verification-list','verification-answer','submit-verification','mastery-status','duration_seconds','/attempts'):
        assert marker in html
