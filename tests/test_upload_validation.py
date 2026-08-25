from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def setup():
    c=TestClient(app); s=c.post('/api/submissions',json={'stage':'high','subject':'数学','province':'广东','school_grade':'高二','academic_year_label':'25-26'}).json(); e=c.post(f"/api/submissions/{s['id']}/exams",json={'name':'x'}).json(); u=c.post(f"/api/exams/{e['id']}/upload-sessions").json(); return c,u


def test_upload_validates_type_filename_size_and_duplicate():
    client, session=setup(); url=f"/api/upload-sessions/{session['id']}/assets"
    assert client.post(url,files={'file':('bad.exe',b'x','application/octet-stream')}).status_code == 415
    assert client.post(url,files={'file':('',b'x','image/jpeg')}).status_code == 422
    response=client.post(url,files={'file':('paper.jpg',b'image','image/jpeg')})
    assert response.status_code == 201 and response.json()['sha256']
    duplicate=client.post(url,files={'file':('copy.jpg',b'image','image/jpeg')})
    assert duplicate.status_code == 409 and duplicate.json()['error'] == 'duplicate_asset'
