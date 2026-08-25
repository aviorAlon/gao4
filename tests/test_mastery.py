from fastapi.testclient import TestClient
from exam_diagnosis.app import app
from tests.test_learning_loop import make_report


def test_mastery_state_progresses_resolves_and_can_regress():
    c=TestClient(app); submission,run=make_report(c); question=c.get(f"/api/analysis-runs/{run['id']}/verification-questions").json()['questions'][0]
    initial=c.get(f"/api/submissions/{submission['id']}/mastery-records").json()['records'][0]
    assert initial['status']=='not_started'
    c.post(f"/api/verification-questions/{question['id']}/attempts",json={'answer':'x','score':5,'max_score':10,'duration_seconds':20})
    assert c.get(f"/api/submissions/{submission['id']}/mastery-records").json()['records'][0]['status']=='in_progress'
    c.post(f"/api/verification-questions/{question['id']}/attempts",json={'answer':'x','score':9,'max_score':10,'duration_seconds':20})
    assert c.get(f"/api/submissions/{submission['id']}/mastery-records").json()['records'][0]['status']=='provisionally_resolved'
    c.post(f"/api/verification-questions/{question['id']}/attempts",json={'answer':'x','score':10,'max_score':10,'duration_seconds':20})
    assert c.get(f"/api/submissions/{submission['id']}/mastery-records").json()['records'][0]['status']=='resolved'
    c.post(f"/api/verification-questions/{question['id']}/attempts",json={'answer':'x','score':2,'max_score':10,'duration_seconds':20})
    record=c.get(f"/api/submissions/{submission['id']}/mastery-records").json()['records'][0]
    assert record['status']=='in_progress'
    assert len(record['attempt_history'])==4
