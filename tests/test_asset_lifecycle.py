from pathlib import Path
from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def create_asset(client, content=b'a'):
    s=client.post('/api/submissions',json={'stage':'high','subject':'数学','province':'广东','school_grade':'高二','academic_year_label':'25-26'}).json(); e=client.post(f"/api/submissions/{s['id']}/exams",json={'name':'assets'}).json(); u=client.post(f"/api/exams/{e['id']}/upload-sessions").json(); a=client.post(f"/api/upload-sessions/{u['id']}/assets",files={'file':('paper.jpg',content,'image/jpeg')}).json(); return s,e,u,a


def test_asset_list_update_and_delete_removes_physical_file():
    c=TestClient(app); s,e,u,a=create_asset(c)
    assert c.get(f"/api/exams/{e['id']}/assets").json()['assets'][0]['id']==a['id']
    updated=c.patch(f"/api/assets/{a['id']}",json={'page_order':4,'material_type':'paper'}).json(); assert updated['page_order']==4 and updated['material_type']=='paper'
    path=Path(a['storage_path']); assert path.exists()
    deleted=c.delete(f"/api/assets/{a['id']}"); assert deleted.status_code==200 and not path.exists()
    assert c.get(f"/api/exams/{e['id']}/assets").json()['assets']==[]


def test_submission_delete_only_removes_its_safe_upload_directories(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path); outside=tmp_path/'outside.txt'; outside.write_text('keep')
    c=TestClient(app); s,e,u,a=create_asset(c,b'unique-safe-delete'); path=Path(a['storage_path']); assert path.exists()
    assert c.delete(f"/api/submissions/{s['id']}").status_code==200
    assert not path.exists() and outside.read_text()=='keep'
