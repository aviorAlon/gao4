from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def test_image_job_without_vision_configuration_is_manual_confirmation():
    client = TestClient(app)
    sub = client.post('/api/submissions', json={'stage':'high','subject':'数学','province':'广东','school_grade':'高二','academic_year_label':'25-26'}).json()
    exam = client.post(f"/api/submissions/{sub['id']}/exams", json={'name':'图片演示'}).json()
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    client.post(f"/api/upload-sessions/{session['id']}/assets", files={'file': ('paper.jpg', b'fake-image', 'image/jpeg')})
    job = client.post(f"/api/upload-sessions/{session['id']}/finish").json()['job_id']
    result = client.post(f"/api/jobs/{job}/run").json()
    assert result['status'] == 'completed'
    pages = client.get(f"/api/exams/{exam['id']}/pages").json()['pages']
    assert pages[0]['ocr_status'] == 'manual_confirmation_required'
    assert pages[0]['ocr_text'] == ''
