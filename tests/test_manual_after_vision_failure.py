from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def test_manual_question_can_confirm_after_image_parse_failure_using_uploaded_asset():
    c=TestClient(app); s=c.post('/api/submissions',json={'stage':'high','subject':'数学','province':'广东','school_grade':'高二','academic_year_label':'25-26'}).json(); e=c.post(f"/api/submissions/{s['id']}/exams",json={'name':'photo'}).json(); u=c.post(f"/api/exams/{e['id']}/upload-sessions").json(); a=c.post(f"/api/upload-sessions/{u['id']}/assets",files={'file':('photo.jpg',b'photo','image/jpeg')}).json(); j=c.post(f"/api/upload-sessions/{u['id']}/finish").json()['job_id']; c.post(f'/api/jobs/{j}/run')
    draft=c.patch(f"/api/exams/{e['id']}/parse-draft",json={'questions':[{'question_number':'手工1','question_text':'手机照片人工题','is_wrong_question':True,'original_question_available':True,'original_source':{'asset_id':a['id']},'student_answer':'我的答案'}]})
    assert draft.status_code==200
    confirmed=c.post(f"/api/exams/{e['id']}/confirm",json={'questions':draft.json()['questions']})
    assert confirmed.status_code==200
    assert confirmed.json()['question_records'][0]['original_source']['asset_id']==a['id']
