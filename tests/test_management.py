from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def setup_exam():
    c=TestClient(app); s=c.post('/api/submissions',json={'stage':'high','subject':'数学','province':'广东','school_grade':'高二','academic_year_label':'25-26'}).json(); e=c.post(f"/api/submissions/{s['id']}/exams",json={'name':'管理'}).json(); return c,s,e


def test_page_order_material_and_score_correction_editing():
    c,s,e=setup_exam(); u=c.post(f"/api/exams/{e['id']}/upload-sessions").json(); c.post(f"/api/upload-sessions/{u['id']}/assets",files={'file':('photo.jpg',b'x','image/jpeg')}); j=c.post(f"/api/upload-sessions/{u['id']}/finish").json()['job_id']; c.post(f'/api/jobs/{j}/run'); page=c.get(f"/api/exams/{e['id']}/pages").json()['pages'][0]
    edited=c.patch(f"/api/pages/{page['id']}",json={'page_order':3,'material_type':'paper'}); assert edited.json()['page_order']==3 and edited.json()['material_type']=='paper'
    q={'question_number':'1','question_text':'题','is_wrong_question':True,'original_question_available':True}; confirmed=c.post(f"/api/exams/{e['id']}/confirm",json={'questions':[q]}).json(); record=confirmed['question_records'][0]
    scored=c.patch(f"/api/question-records/{record['id']}",json={'score_obtained':6,'score_total':10,'teacher_comment':'计算错误'}).json(); assert scored['score']['lost_points']==4


def test_submission_delete_export_and_analysis_history():
    c,s,e=setup_exam(); q={'question_number':'1','question_text':'题','is_wrong_question':True,'original_question_available':True}; c.post(f"/api/exams/{e['id']}/confirm",json={'questions':[q]}); run=c.post(f"/api/submissions/{s['id']}/analysis-runs",json={'exam_ids':[e['id']]}).json()
    history=c.get(f"/api/submissions/{s['id']}/analysis-runs"); assert history.status_code==200 and history.json()['runs'][0]['id']==run['id']
    exported=c.get(f"/api/submissions/{s['id']}/export"); assert exported.status_code==200 and exported.json()['submission']['id']==s['id']
    deleted=c.delete(f"/api/submissions/{s['id']}"); assert deleted.status_code==200
    assert c.get(f"/api/submissions/{s['id']}").status_code==404
