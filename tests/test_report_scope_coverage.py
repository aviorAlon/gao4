from fastapi.testclient import TestClient
from exam_diagnosis.app import app
from exam_diagnosis.reporting import render_html, render_markdown


def build_report(client, question_text='函数定义域'):
    submission = client.post('/api/submissions', json={'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '范围'}).json()
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [{'question_number': '1', 'question_text': question_text, 'is_wrong_question': True, 'original_question_available': True, 'student_answer': '错答'}]})
    run = client.post(f"/api/submissions/{submission['id']}/analysis-runs", json={'exam_ids': [exam['id']]}).json()
    client.post(f"/api/analysis-runs/{run['id']}/confirm-start")
    return run, client.get(f"/api/analysis-runs/{run['id']}/report").json()


def test_report_contrasts_curriculum_scope_with_assessed_scope():
    _, report = build_report(TestClient(app))
    scope = report['scope_coverage']
    assert scope['curriculum_point_count'] == report['knowledge_base']['knowledge_point_count']
    assert scope['assessed_point_count'] == len(report['knowledge_coverage'])
    assert scope['assessed_point_count'] + scope['not_assessed_point_count'] == scope['curriculum_point_count']
    assert 0 < scope['coverage_ratio'] < 1


def test_uncovered_curriculum_points_are_marked_not_assessed_rather_than_unmastered():
    _, report = build_report(TestClient(app))
    scope = report['scope_coverage']
    assessed = {item['knowledge_id']: item for item in scope['points'] if item['status'] != 'not_assessed'}
    assert 'high.math.function.domain' in assessed
    assert assessed['high.math.function.domain']['status'] == 'assessed_with_errors'
    untouched = next(item for item in scope['points'] if item['knowledge_id'] == 'high.math.probability.basic')
    assert untouched['status'] == 'not_assessed' and untouched['question_count'] == 0 and untouched['wrong_count'] == 0
    assert 'high.math.probability.basic' in scope['not_assessed_knowledge_ids']
    assert '概率统计' in scope['not_assessed_chapters']
    assert '不能据此判定未掌握' in scope['note']


def test_assessed_point_without_errors_is_distinguished_from_wrong_one():
    client = TestClient(app)
    submission = client.post('/api/submissions', json={'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '对错混合'}).json()
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [
        {'question_number': '1', 'question_text': '函数定义域', 'is_wrong_question': True, 'original_question_available': True, 'student_answer': '错答'},
        {'question_number': '2', 'question_text': '古典概型与条件概率', 'is_wrong_question': False, 'original_question_available': True, 'student_answer': '对答'},
    ]})
    run = client.post(f"/api/submissions/{submission['id']}/analysis-runs", json={'exam_ids': [exam['id']]}).json()
    client.post(f"/api/analysis-runs/{run['id']}/confirm-start")
    points = {item['knowledge_id']: item for item in client.get(f"/api/analysis-runs/{run['id']}/report").json()['scope_coverage']['points']}
    assert points['high.math.probability.basic']['status'] == 'assessed_without_errors'
    assert points['high.math.function.domain']['status'] == 'assessed_with_errors'


def test_exported_report_states_the_scope_difference():
    client = TestClient(app)
    run, report = build_report(client)
    markdown = client.get(f"/api/reports/{run['id']}/export?format=md").text
    assert '## 课程范围与资料覆盖范围' in markdown
    assert '本次资料未考查' in markdown and '不能据此判定未掌握' in markdown
    html = client.get(f"/api/reports/{run['id']}/export?format=html").text
    assert '<h2>课程范围与资料覆盖范围</h2>' in html and '未考查章节' in html


def test_renderers_skip_the_section_when_scope_coverage_is_absent():
    report = {'wrong_question_count': 0, 'clusters': [], 'knowledge_tree': {}, 'verification_questions': []}
    assert '课程范围与资料覆盖范围' not in render_markdown(report)
    assert '课程范围与资料覆盖范围' not in render_html(report)
