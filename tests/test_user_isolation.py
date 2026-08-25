from fastapi.testclient import TestClient
from exam_diagnosis.app import LOCAL_USER_ID, app

ALICE = {'X-User-Id': 'alice'}
BOB = {'X-User-Id': 'bob'}
PAYLOAD = {'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}


def test_submission_records_the_owner_and_defaults_to_the_local_user():
    client = TestClient(app)
    assert client.post('/api/submissions', json=PAYLOAD).json()['user_id'] == LOCAL_USER_ID
    assert client.post('/api/submissions', json=PAYLOAD, headers=ALICE).json()['user_id'] == 'alice'


def test_listing_submissions_only_returns_the_callers_own():
    client = TestClient(app)
    client.post('/api/submissions', json=PAYLOAD, headers=ALICE)
    client.post('/api/submissions', json=PAYLOAD, headers=BOB)
    assert [item['user_id'] for item in client.get('/api/submissions', headers=ALICE).json()] == ['alice']
    assert client.get('/api/submissions').json() == []


def test_another_user_cannot_read_or_delete_a_submission():
    client = TestClient(app)
    submission = client.post('/api/submissions', json=PAYLOAD, headers=ALICE).json()
    assert client.get(f"/api/submissions/{submission['id']}", headers=BOB).status_code == 404
    assert client.delete(f"/api/submissions/{submission['id']}", headers=BOB).status_code == 404
    assert client.get(f"/api/submissions/{submission['id']}", headers=ALICE).status_code == 200


def test_ownership_is_inherited_by_every_downstream_resource():
    client = TestClient(app)
    submission = client.post('/api/submissions', json=PAYLOAD, headers=ALICE).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '归属'}, headers=ALICE).json()
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions", headers=ALICE).json()
    asset = client.post(f"/api/upload-sessions/{session['id']}/assets", files={'file': ('paper.txt', '数学 第1题 函数定义域'.encode(), 'text/plain')}, headers=ALICE).json()
    job_id = client.post(f"/api/upload-sessions/{session['id']}/finish", headers=ALICE).json()['job_id']
    client.post(f"/api/jobs/{job_id}/run", headers=ALICE)
    page = client.get(f"/api/exams/{exam['id']}/pages", headers=ALICE).json()['pages'][0]
    assert {exam['user_id'], session['user_id'], asset['user_id']} == {'alice'}
    for path in (f"/api/exams/{exam['id']}", f"/api/exams/{exam['id']}/pages", f"/api/jobs/{job_id}"):
        assert client.get(path, headers=BOB).status_code == 404
    assert client.patch(f"/api/pages/{page['id']}", json={'page_order': 2}, headers=BOB).status_code == 404


def test_reports_and_verification_questions_stay_inside_the_owner_boundary():
    client = TestClient(app)
    submission = client.post('/api/submissions', json=PAYLOAD, headers=ALICE).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '归属'}, headers=ALICE).json()
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [{'question_number': '1', 'question_text': '函数定义域', 'is_wrong_question': True, 'original_question_available': True, 'student_answer': '错答'}]}, headers=ALICE)
    run = client.post(f"/api/submissions/{submission['id']}/analysis-runs", json={'exam_ids': [exam['id']]}, headers=ALICE).json()
    client.post(f"/api/analysis-runs/{run['id']}/confirm-start", headers=ALICE)
    client.get(f"/api/analysis-runs/{run['id']}/report", headers=ALICE)
    question = client.get(f"/api/analysis-runs/{run['id']}/verification-questions", headers=ALICE).json()['questions'][0]
    assert question['user_id'] == 'alice'
    assert client.get(f"/api/analysis-runs/{run['id']}/report", headers=BOB).status_code == 404
    assert client.get(f"/api/verification-questions/{question['id']}", headers=BOB).status_code == 404
    assert client.post(f"/api/verification-questions/{question['id']}/attempts", json={'answer': 'x', 'score': 9, 'max_score': 10}, headers=BOB).status_code == 404
    assert client.get(f"/api/reports/{run['id']}/export?format=md", headers=BOB).status_code == 404
