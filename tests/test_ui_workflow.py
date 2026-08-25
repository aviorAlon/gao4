from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def test_browser_workflow_api_equivalent_creates_uploads_parses_edits_confirms_and_reports():
    client = TestClient(app)
    sub = client.post('/api/submissions', json={'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}).json()
    exam = client.post(f"/api/submissions/{sub['id']}/exams", json={'name': '演示考试'}).json()
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    client.post(f"/api/upload-sessions/{session['id']}/assets", files={'file': ('math-paper.txt', '数学 第17题 函数定义域'.encode(), 'text/plain')})
    job = client.post(f"/api/upload-sessions/{session['id']}/finish").json()
    assert client.post(f"/api/jobs/{job['job_id']}/run").json()['status'] == 'completed'
    draft = client.get(f"/api/exams/{exam['id']}/parse-draft").json()
    assert draft['questions'][0]['question_number'] == '17'
    edited = client.patch(f"/api/exams/{exam['id']}/parse-draft", json={'questions': [{'question_number': '17', 'question_text': '函数定义域（已编辑）', 'is_wrong_question': True, 'original_question_available': True}]})
    assert edited.status_code == 200
    questions = edited.json()['questions']
    assert questions[0]['original_source']['asset_id']
    assert client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': questions}).status_code == 200
