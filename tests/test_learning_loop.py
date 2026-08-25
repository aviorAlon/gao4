from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def make_report(client):
    s=client.post('/api/submissions',json={'stage':'high','subject':'数学','province':'广东','school_grade':'高二','academic_year_label':'25-26'}).json(); e=client.post(f"/api/submissions/{s['id']}/exams",json={'name':'闭环'}).json(); q={'question_number':'1','question_text':'函数定义域','is_wrong_question':True,'original_question_available':True,'student_answer':'错答'}; client.post(f"/api/exams/{e['id']}/confirm",json={'questions':[q]}); run=client.post(f"/api/submissions/{s['id']}/analysis-runs",json={'exam_ids':[e['id']]}).json(); client.post(f"/api/analysis-runs/{run['id']}/confirm-start"); client.get(f"/api/analysis-runs/{run['id']}/report"); return s,run


def test_verification_questions_persist_and_can_be_queried_and_submitted():
    c=TestClient(app); submission,run=make_report(c)
    listing=c.get(f"/api/analysis-runs/{run['id']}/verification-questions")
    assert listing.status_code==200 and listing.json()['questions']
    question=listing.json()['questions'][0]
    assert question['id'] and question['analysis_run_id']==run['id']
    attempt=c.post(f"/api/verification-questions/{question['id']}/attempts",json={'answer':'我的答案','score':8,'max_score':10,'duration_seconds':75})
    assert attempt.status_code==201
    data=attempt.json(); assert data['passed'] is True and data['duration_seconds']==75
    refreshed=c.get(f"/api/verification-questions/{question['id']}").json()
    assert len(refreshed['attempts'])==1
