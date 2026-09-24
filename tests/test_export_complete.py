from pathlib import Path
from fastapi.testclient import TestClient
from exam_diagnosis.app import app
from conftest import SHIPPED_KNOWLEDGE_VERSION


def test_export_contains_assets_pages_attempts_and_versions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c=TestClient(app); s=c.post('/api/submissions',json={'stage':'high','subject':'数学','province':'广东','school_grade':'高二','academic_year_label':'25-26'}).json(); e=c.post(f"/api/submissions/{s['id']}/exams",json={'name':'export'}).json(); u=c.post(f"/api/exams/{e['id']}/upload-sessions").json(); c.post(f"/api/upload-sessions/{u['id']}/assets",files={'file':('paper.txt','数学 第1题 函数定义域'.encode(),'text/plain')}); j=c.post(f"/api/upload-sessions/{u['id']}/finish").json()['job_id']; c.post(f'/api/jobs/{j}/run')
    draft=c.get(f"/api/exams/{e['id']}/parse-draft").json()['questions']; draft[0].update({'is_wrong_question':True,'original_question_available':True,'student_answer':'错答'}); c.post(f"/api/exams/{e['id']}/confirm",json={'questions':draft}); run=c.post(f"/api/submissions/{s['id']}/analysis-runs",json={'exam_ids':[e['id']]}).json(); c.post(f"/api/analysis-runs/{run['id']}/confirm-start"); c.get(f"/api/analysis-runs/{run['id']}/report"); v=c.get(f"/api/analysis-runs/{run['id']}/verification-questions").json()['questions'][0]; c.post(f"/api/verification-questions/{v['id']}/attempts",json={'answer':'a','score':9,'max_score':10,'duration_seconds':10})
    data=c.get(f"/api/submissions/{s['id']}/export").json()
    assert data['assets'] and data['pages'] and data['verification_questions'][0]['attempts']
    assert data['versions']['knowledge_base']==SHIPPED_KNOWLEDGE_VERSION and data['versions']['export_schema']
