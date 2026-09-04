"""Re-parsing an exam's current assets without a new upload session (asset management flows)."""
from fastapi.testclient import TestClient

import exam_diagnosis.app as app_module

SUBMISSION = {'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}


def _exam_with_assets(client, files):
    sub = client.post('/api/submissions', json=SUBMISSION).json()
    exam = client.post(f"/api/submissions/{sub['id']}/exams", json={'name': '月考'}).json()
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    assets = [client.post(f"/api/upload-sessions/{session['id']}/assets", files={'file': f}).json() for f in files]
    job = client.post(f"/api/upload-sessions/{session['id']}/finish").json()['job_id']
    assert client.post(f'/api/jobs/{job}/run').json()['status'] == 'completed'
    return exam, assets


def test_reparse_after_deleting_an_asset_rebuilds_pages_and_draft():
    client = TestClient(app_module.app)
    exam, assets = _exam_with_assets(client, [('a.txt', b'1. first\n2. second', 'text/plain'), ('b.txt', b'3. third', 'text/plain')])
    assert len(client.get(f"/api/exams/{exam['id']}/pages").json()['pages']) == 2
    assert [q['question_number'] for q in client.get(f"/api/exams/{exam['id']}/parse-draft").json()['questions']] == ['1', '2', '3']
    client.delete(f"/api/assets/{assets[1]['id']}")
    started = client.post(f"/api/exams/{exam['id']}/reparse")
    assert started.status_code == 201
    body = started.json()
    assert body['asset_count'] == 1 and body['job']['status'] == 'queued' and body['job']['upload_session_id'] is None
    assert client.get(f"/api/exams/{exam['id']}").json()['status'] == 'parsing'
    assert client.post(f"/api/jobs/{body['job_id']}/run").json()['status'] == 'completed'
    assert client.get(f"/api/exams/{exam['id']}").json()['status'] == 'waiting_confirmation'
    pages = client.get(f"/api/exams/{exam['id']}/pages").json()['pages']
    assert [p['asset_id'] for p in pages] == [assets[0]['id']]
    assert [q['question_number'] for q in client.get(f"/api/exams/{exam['id']}/parse-draft").json()['questions']] == ['1', '2']


def test_reparse_can_be_enqueued_in_the_background():
    client = TestClient(app_module.app)
    exam, _ = _exam_with_assets(client, [('a.txt', b'1. first', 'text/plain')])
    job_id = client.post(f"/api/exams/{exam['id']}/reparse").json()['job_id']
    task_id = client.post(f'/api/jobs/{job_id}/enqueue').json()['task_id']
    assert app_module.TASK_QUEUE.wait(task_id, timeout=10)['status'] == 'completed'
    assert client.get(f"/api/exams/{exam['id']}").json()['status'] == 'waiting_confirmation'


def test_reparse_refuses_confirmed_or_empty_exams():
    client = TestClient(app_module.app)
    exam, assets = _exam_with_assets(client, [('a.txt', b'1. first', 'text/plain')])
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [{'question_number': '1', 'question_text': 'first', 'is_wrong_question': True, 'original_question_available': True}]})
    refused = client.post(f"/api/exams/{exam['id']}/reparse")
    assert refused.status_code == 409 and refused.json()['error'] == 'exam_confirmed'
    client.post(f"/api/exams/{exam['id']}/reopen")
    assert client.post(f"/api/exams/{exam['id']}/reparse").status_code == 201
    client.delete(f"/api/assets/{assets[0]['id']}")
    empty = client.post(f"/api/exams/{exam['id']}/reparse")
    assert empty.status_code == 422 and empty.json()['error'] == 'exam_has_no_assets'
    assert client.post('/api/exams/nope/reparse').status_code == 404
