from fastapi.testclient import TestClient
from exam_diagnosis.app import app

QUESTION = {'question_number': '1', 'question_text': '函数定义域', 'is_wrong_question': True, 'original_question_available': True, 'student_answer': '错答'}


def make_confirmed_exam(client, name='版本'):
    submission = client.post('/api/submissions', json={'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': name}).json()
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [QUESTION]})
    return submission, exam


def report_for(client, submission, exam):
    run = client.post(f"/api/submissions/{submission['id']}/analysis-runs", json={'exam_ids': [exam['id']]}).json()
    client.post(f"/api/analysis-runs/{run['id']}/confirm-start")
    return run, client.get(f"/api/analysis-runs/{run['id']}/report")


def test_run_and_report_snapshot_the_exam_versions_they_analysed():
    client = TestClient(app)
    submission, exam = make_confirmed_exam(client)
    run, report = report_for(client, submission, exam)
    assert run['exam_versions'] == [{'exam_id': exam['id'], 'name': '版本', 'version': 1}]
    assert report.json()['exam_versions'] == run['exam_versions']


def test_reopening_supersedes_old_question_records_instead_of_deleting_them():
    client = TestClient(app)
    submission, exam = make_confirmed_exam(client)
    reopened = client.post(f"/api/exams/{exam['id']}/reopen").json()
    assert reopened['version'] == 2 and reopened['superseded_version'] == 1
    records = client.get(f"/api/exams/{exam['id']}/question-records").json()['records']
    assert [record['status'] for record in records] == ['superseded']
    assert records[0]['superseded_by_version'] == 2


def test_new_report_counts_only_the_current_version_not_both():
    client = TestClient(app)
    submission, exam = make_confirmed_exam(client)
    client.post(f"/api/exams/{exam['id']}/reopen")
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [QUESTION]})
    run, report = report_for(client, submission, exam)
    data = report.json()
    assert data['question_count'] == 1 and data['wrong_question_count'] == 1
    assert data['exam_versions'] == [{'exam_id': exam['id'], 'name': '版本', 'version': 2}]


def test_old_report_survives_a_reopen_and_keeps_its_own_version():
    client = TestClient(app)
    submission, exam = make_confirmed_exam(client)
    first_run, first_report = report_for(client, submission, exam)
    assert first_report.json()['exam_versions'][0]['version'] == 1
    client.post(f"/api/exams/{exam['id']}/reopen")
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [QUESTION]})
    second_run, second_report = report_for(client, submission, exam)
    assert client.get(f"/api/analysis-runs/{first_run['id']}/report").json()['exam_versions'][0]['version'] == 1
    assert second_report.json()['exam_versions'][0]['version'] == 2
    assert first_run['id'] != second_run['id']


def test_exported_report_states_the_exam_version_it_used():
    client = TestClient(app)
    submission, exam = make_confirmed_exam(client, name='月考')
    client.post(f"/api/exams/{exam['id']}/reopen")
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [QUESTION]})
    run, _ = report_for(client, submission, exam)
    assert '- 分析依据的考试解析版本：月考 v2' in client.get(f"/api/reports/{run['id']}/export?format=md").text
    assert '分析依据的考试解析版本：月考 v2' in client.get(f"/api/reports/{run['id']}/export?format=html").text


def test_report_refuses_to_run_when_the_exam_changed_after_the_run_was_created():
    client = TestClient(app)
    submission, exam = make_confirmed_exam(client)
    run = client.post(f"/api/submissions/{submission['id']}/analysis-runs", json={'exam_ids': [exam['id']]}).json()
    client.post(f"/api/analysis-runs/{run['id']}/confirm-start")
    client.post(f"/api/exams/{exam['id']}/reopen")
    response = client.get(f"/api/analysis-runs/{run['id']}/report")
    assert response.status_code == 409
    body = response.json()
    assert body['error'] == 'exam_versions_changed'
    assert body['selected'][0]['version'] == 1 and body['current'][0]['version'] == 2
