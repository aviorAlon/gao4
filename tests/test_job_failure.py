from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def test_run_job_captures_stage_exception_and_retry_requeues():
    c=TestClient(app); s=c.post('/api/submissions',json={'stage':'high','subject':'数学','province':'广东','school_grade':'高二','academic_year_label':'25-26'}).json(); e=c.post(f"/api/submissions/{s['id']}/exams",json={'name':'x'}).json(); u=c.post(f"/api/exams/{e['id']}/upload-sessions").json(); c.post(f"/api/upload-sessions/{u['id']}/assets",files={'file':('paper.txt',b'Math Question 1 x','text/plain')}); job=c.post(f"/api/upload-sessions/{u['id']}/finish").json()['job_id']
    from exam_diagnosis import app as module
    original=module.LocalParser.parse_page
    def fail(*args,**kwargs): raise RuntimeError('parser boom')
    module.LocalParser.parse_page=fail
    try:
      failed=c.post(f'/api/jobs/{job}/run')
      assert failed.status_code == 500
      data=failed.json(); assert data['status']=='failed' and data['failed_stage']=='ocr' and 'parser boom' in data['error']
      retry=c.post(f'/api/jobs/{job}/retry'); assert retry.status_code==202 and retry.json()['status']=='queued'
    finally: module.LocalParser.parse_page=original
