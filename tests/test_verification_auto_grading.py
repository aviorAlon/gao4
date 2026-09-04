"""Tests for the `grade` model role: auto-marking a verification attempt against its reference answer."""
import exam_diagnosis.app as app_module
from exam_diagnosis.diagnosis import model_grade_attempt
from fastapi.testclient import TestClient


class FakeClient:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def complete_json(self, system, user):
        self.calls.append((system, user))
        return self.answer


def test_model_grade_returns_ratio_and_feedback():
    client = FakeClient({'score_ratio': 0.5, 'feedback': '定义域条件写对了，但最后区间端点取反了'})
    graded = model_grade_attempt(client, 'q', 'ref', 'student')
    assert graded == {'score_ratio': 0.5, 'feedback': '定义域条件写对了，但最后区间端点取反了'}
    assert len(client.calls) == 1


def test_model_grade_returns_none_on_failure_or_malformed():
    assert model_grade_attempt(FakeClient(None), 'q', 'ref', 's') is None
    assert model_grade_attempt(FakeClient({'score_ratio': 1.5, 'feedback': 'x'}), 'q', 'ref', 's') is None  # out of range
    assert model_grade_attempt(FakeClient({'score_ratio': 0.5}), 'q', 'ref', 's') is None  # missing feedback
    assert model_grade_attempt(FakeClient({'score_ratio': True, 'feedback': 'x'}), 'q', 'ref', 's') is None  # bool is not a score


def _approved_verification_question(client):
    """Drive the real pipeline to a verification question whose reference answer is approved."""
    submission = client.post('/api/submissions', json={'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [{'question_number': '1', 'question_text': '函数定义域', 'is_wrong_question': True, 'original_question_available': True}]})
    run = client.post(f"/api/submissions/{submission['id']}/analysis-runs", json={'exam_ids': [exam['id']]}).json()
    client.post(f"/api/analysis-runs/{run['id']}/confirm-start")
    report = client.get(f"/api/analysis-runs/{run['id']}/report").json()
    qid = report['verification_question_ids'][0]
    client.post(f'/api/verification-questions/{qid}/review', json={'approved': True, 'answer': '定义域为 x≠2'})
    return qid


def test_attempt_is_auto_graded_when_role_available_and_answer_reviewed(monkeypatch):
    client = TestClient(app_module.app)
    qid = _approved_verification_question(client)
    monkeypatch.setattr(app_module, 'guarded_client_from_env', lambda role, user_id, **ctx: object())
    monkeypatch.setattr(app_module, 'model_grade_attempt', lambda c, q, ref, s: {'score_ratio': 0.9, 'feedback': '过程完整'})
    # student self-reports a failing score; the model's verdict should win
    attempt = client.post(f'/api/verification-questions/{qid}/attempts', json={'answer': 'x≠2', 'score': 2, 'max_score': 10}).json()
    assert attempt['graded_by'] == 'model'
    assert attempt['auto_graded_score_ratio'] == 0.9
    assert attempt['auto_graded_feedback'] == '过程完整'
    assert attempt['passed'] is True
    assert attempt['score'] == 2  # self-reported score is kept for the record, not overwritten


def test_attempt_keeps_self_reported_score_when_model_unavailable(monkeypatch):
    client = TestClient(app_module.app)
    qid = _approved_verification_question(client)
    monkeypatch.setattr(app_module, 'guarded_client_from_env', lambda role, user_id, **ctx: None)
    attempt = client.post(f'/api/verification-questions/{qid}/attempts', json={'answer': 'x≠2', 'score': 9, 'max_score': 10}).json()
    assert attempt['graded_by'] == 'self'
    assert 'auto_graded_score_ratio' not in attempt
    assert attempt['passed'] is True


def test_attempt_keeps_self_reported_score_when_model_returns_none(monkeypatch):
    client = TestClient(app_module.app)
    qid = _approved_verification_question(client)
    monkeypatch.setattr(app_module, 'guarded_client_from_env', lambda role, user_id, **ctx: object())
    monkeypatch.setattr(app_module, 'model_grade_attempt', lambda c, q, ref, s: None)
    attempt = client.post(f'/api/verification-questions/{qid}/attempts', json={'answer': 'x≠2', 'score': 9, 'max_score': 10}).json()
    assert attempt['graded_by'] == 'self'
    assert attempt['passed'] is True


def test_unreviewed_reference_answer_is_never_used_for_grading(monkeypatch):
    client = TestClient(app_module.app)
    qid = _approved_verification_question(client)
    # revoke the review: the stored answer is still there, but it's no longer vetted
    client.post(f'/api/verification-questions/{qid}/review', json={'approved': False})
    called = []
    monkeypatch.setattr(app_module, 'guarded_client_from_env', lambda role, user_id, **ctx: called.append(role) or object())
    monkeypatch.setattr(app_module, 'model_grade_attempt', lambda c, q, ref, s: {'score_ratio': 1.0, 'feedback': 'x'})
    attempt = client.post(f'/api/verification-questions/{qid}/attempts', json={'answer': 'x', 'score': 1, 'max_score': 10}).json()
    assert called == []  # the grade role must not even be consulted
    assert attempt['graded_by'] == 'self'
    assert attempt['passed'] is False
