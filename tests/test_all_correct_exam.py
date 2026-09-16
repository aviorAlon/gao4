"""An exam with no wrong answers can be confirmed and flows through every downstream view.

It used to be refused ("at least one wrong question"), which made a clean paper — the result a
student is working towards — impossible to record, and the trend view could never show 0%.
"""
from fastapi.testclient import TestClient

import exam_diagnosis.app as app_module

SUBMISSION = {'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}


def _q(number, wrong, text='求函数的定义域', original=True):
    return {'question_number': number, 'question_text': text, 'is_wrong_question': wrong, 'original_question_available': original}


def _exam(client, submission_id, name, questions):
    exam = client.post(f'/api/submissions/{submission_id}/exams', json={'name': name}).json()
    return exam, client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': questions})


def test_all_correct_exam_can_be_confirmed():
    client = TestClient(app_module.app)
    submission = client.post('/api/submissions', json=SUBMISSION).json()
    exam, confirmed = _exam(client, submission['id'], '满分卷', [_q('1', False), _q('2', False)])
    assert confirmed.status_code == 200, confirmed.json()
    assert client.get(f"/api/exams/{exam['id']}").json()['status'] == 'confirmed'
    assert len(client.get(f"/api/exams/{exam['id']}/question-records").json()['records']) == 2


def test_what_is_still_refused():
    client = TestClient(app_module.app)
    submission = client.post('/api/submissions', json=SUBMISSION).json()
    _, empty = _exam(client, submission['id'], '空卷', [])
    assert empty.status_code == 422 and empty.json()['error'] == 'questions_required'
    _, no_original = _exam(client, submission['id'], '缺原题', [_q('1', True, original=False)])
    assert no_original.status_code == 422 and no_original.json()['error'] == 'wrong_question_original_required'
    # a correct question without an original is fine — there's nothing to diagnose against it
    _, fine = _exam(client, submission['id'], '对题无原题', [_q('1', False, original=False)])
    assert fine.status_code == 200


def test_all_correct_exam_produces_an_empty_but_valid_report():
    client = TestClient(app_module.app)
    submission = client.post('/api/submissions', json=SUBMISSION).json()
    exam, _ = _exam(client, submission['id'], '满分卷', [_q('1', False)])
    run = client.post(f"/api/submissions/{submission['id']}/analysis-runs", json={'exam_ids': [exam['id']]})
    assert run.status_code == 201, run.json()
    client.post(f"/api/analysis-runs/{run.json()['id']}/confirm-start")
    report = client.get(f"/api/analysis-runs/{run.json()['id']}/report")
    assert report.status_code == 200, report.json()
    body = report.json()
    assert body['wrong_question_count'] == 0 and body['question_count'] == 1
    assert body['clusters'] == [] and body['wrong_question_collection'] == []
    assert body['verification_question_ids'] == []
    # nothing to review, and exporting still works
    assert client.get('/api/review-plan').json()['summary']['due_count'] == 0
    pdf = client.get(f"/api/reports/{run.json()['id']}/export", params={'format': 'pdf'})
    assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF')


def test_trend_can_now_reach_zero():
    client = TestClient(app_module.app)
    submission = client.post('/api/submissions', json=SUBMISSION).json()
    _exam(client, submission['id'], '月考一', [_q('1', True), _q('2', True)])
    _exam(client, submission['id'], '月考二', [_q('1', False), _q('2', False)])
    trends = client.get(f"/api/submissions/{submission['id']}/trends").json()
    assert [e['error_rate'] for e in trends['exams']] == [1.0, 0.0]
    assert trends['summary']['overall_direction'] == 'improving'
    domain = next(t for t in trends['knowledge_trends'] if t['knowledge_id'] == 'high.math.function.domain')
    assert domain['last_error_rate'] == 0.0 and domain['persistent'] is False
    # the clean exam contributes nothing to the wrong book
    book = client.get(f"/api/submissions/{submission['id']}/wrong-questions").json()
    assert book['summary'] == {'total': 2, 'analysed': 0, 'exam_count': 1}
