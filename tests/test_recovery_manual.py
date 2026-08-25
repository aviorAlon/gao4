from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def test_recovery_lists_submissions_and_exams_and_manual_question_can_link_source():
    client=TestClient(app)
    sub=client.post('/api/submissions',json={'stage':'high','subject':'数学','province':'广东','school_grade':'高二','academic_year_label':'25-26'}).json()
    exam=client.post(f"/api/submissions/{sub['id']}/exams",json={'name':'恢复考试'}).json()
    assert client.get('/api/submissions').status_code == 200
    assert client.get('/api/submissions').json()[0]['id'] == sub['id']
    assert client.get(f"/api/submissions/{sub['id']}/exams").json()[0]['id'] == exam['id']
    session=client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    asset=client.post(f"/api/upload-sessions/{session['id']}/assets",files={'file':('paper.jpg',b'img','image/jpeg')}).json()
    draft=client.patch(f"/api/exams/{exam['id']}/parse-draft",json={'questions':[{'question_number':'手工1','question_text':'人工题','is_wrong_question':True,'original_question_available':True}]}).json()
    linked=client.post(f"/api/exams/{exam['id']}/questions/{draft['questions'][0]['id']}/link-original",json={'asset_id':asset['id']})
    assert linked.status_code == 200
    assert linked.json()['question']['original_source']['asset_id'] == asset['id']
