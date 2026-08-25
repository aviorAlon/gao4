from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def test_parse_page_exposes_subject_mismatch_and_low_confidence():
    c=TestClient(app); s=c.post('/api/submissions',json={'stage':'high','subject':'数学','province':'广东','school_grade':'高二','academic_year_label':'25-26'}).json(); e=c.post(f"/api/submissions/{s['id']}/exams",json={'name':'quality'}).json(); u=c.post(f"/api/exams/{e['id']}/upload-sessions").json(); c.post(f"/api/upload-sessions/{u['id']}/assets",files={'file':('paper.txt','物理 第1题'.encode(),'text/plain')}); jid=c.post(f"/api/upload-sessions/{u['id']}/finish").json()['job_id']; c.post(f'/api/jobs/{jid}/run'); pages=c.get(f'/api/exams/{e["id"]}/pages').json()['pages']; assert pages[0]['subject_mismatch'] is True; assert pages[0]['ocr_confidence'] < 0.8
