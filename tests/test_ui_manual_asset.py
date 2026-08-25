from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def test_exam_creation_refreshes_exam_selection_surface_and_assets_are_available_for_manual_link():
    c=TestClient(app); s=c.post('/api/submissions',json={'stage':'high','subject':'数学','province':'广东','school_grade':'高二','academic_year_label':'25-26'}).json(); e=c.post(f"/api/submissions/{s['id']}/exams",json={'name':'manual'}).json(); u=c.post(f"/api/exams/{e['id']}/upload-sessions").json(); a=c.post(f"/api/upload-sessions/{u['id']}/assets",files={'file':('photo.jpg',b'photo','image/jpeg')}).json()
    response=c.get(f"/api/exams/{e['id']}/assets")
    assert response.status_code == 200
    assert response.json()['assets'][0]['id'] == a['id']
