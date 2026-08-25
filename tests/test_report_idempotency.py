from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def test_report_get_is_idempotent_and_export_survives_refresh():
    c=TestClient(app); s=c.post('/api/submissions',json={'stage':'high','subject':'数学','province':'广东','school_grade':'高二','academic_year_label':'25-26'}).json(); e=c.post(f"/api/submissions/{s['id']}/exams",json={'name':'r'}).json()
    q={'question_number':'1','question_text':'函数定义域','is_wrong_question':True,'original_question_available':True}
    confirmed=c.post(f"/api/exams/{e['id']}/confirm",json={'questions':[q]}).json(); rid=c.post(f"/api/submissions/{s['id']}/analysis-runs",json={'exam_ids':[e['id']]}).json()['id']; c.post(f'/api/analysis-runs/{rid}/confirm-start')
    first=c.get(f'/api/analysis-runs/{rid}/report'); second=c.get(f'/api/analysis-runs/{rid}/report')
    assert first.status_code == second.status_code == 200 and first.json() == second.json()
    assert c.get(f'/api/reports/{rid}/export?format=md').status_code == 200
    assert c.get(f'/api/reports/{rid}/export?format=html').status_code == 200
