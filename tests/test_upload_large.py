from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def test_upload_rejects_real_payload_over_10mb():
    c=TestClient(app); s=c.post('/api/submissions',json={'stage':'high','subject':'数学','province':'广东','school_grade':'高二','academic_year_label':'25-26'}).json(); e=c.post(f"/api/submissions/{s['id']}/exams",json={'name':'large'}).json(); u=c.post(f"/api/exams/{e['id']}/upload-sessions").json(); data=b'x'*(10*1024*1024+1); r=c.post(f"/api/upload-sessions/{u['id']}/assets",files={'file':('large.jpg',data,'image/jpeg')}); assert r.status_code==413
