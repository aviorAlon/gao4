from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def test_report_pipeline_includes_diagnosis_and_export_formats():
    client = TestClient(app)
    submission = client.post('/api/submissions', json={'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    confirmed = client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [{'question_number': '1', 'question_text': '函数定义域', 'is_wrong_question': True, 'original_question_available': True}]}).json()
    record_id = confirmed['question_records'][0]['id']
    client.post(f'/api/question-records/{record_id}/knowledge-mapping')
    run = client.post(f"/api/submissions/{submission['id']}/analysis-runs", json={'exam_ids': [exam['id']]}).json()
    client.post(f"/api/analysis-runs/{run['id']}/confirm-start")
    report = client.get(f"/api/analysis-runs/{run['id']}/report").json()
    assert report['clusters']
    assert report['knowledge_tree']
    assert report['verification_questions']
    markdown = client.get(f"/api/reports/{run['id']}/export?format=md")
    html = client.get(f"/api/reports/{run['id']}/export?format=html")
    assert '错因专题' in markdown.text
    assert '<h1>' in html.text
    assert '&lt;h1&gt;' not in html.text
