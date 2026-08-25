from fastapi.testclient import TestClient
from exam_diagnosis.app import app
from exam_diagnosis.diagnosis import diagnose_records, review_priority
from exam_diagnosis.reporting import render_html, render_markdown


def point(knowledge_id, name, chapter):
    return [{'knowledge_id': knowledge_id, 'name': name, 'chapter': chapter}]


def record(qid, wrong, knowledge_id, name, chapter):
    return {'id': qid, 'is_wrong_question': wrong, 'knowledge_points': point(knowledge_id, name, chapter)}


def test_review_priority_weighs_error_rate_not_only_error_count():
    assert review_priority(2, 2) == 'high'
    assert review_priority(10, 2) == 'medium'
    assert review_priority(1, 1) == 'medium'
    assert review_priority(10, 1) == 'low'
    assert review_priority(3, 0) == 'none'


def test_knowledge_tree_nodes_carry_error_rate_and_review_priority():
    result = diagnose_records([
        record('q1', True, 'k1', '定义域', '函数'),
        record('q2', True, 'k1', '定义域', '函数'),
        record('q3', False, 'k2', '值域', '函数'),
    ])
    weak = result['knowledge_tree']['函数']['定义域']
    solid = result['knowledge_tree']['函数']['值域']
    assert weak['error_rate'] == 1.0 and weak['review_priority'] == 'high'
    assert solid['error_rate'] == 0.0 and solid['review_priority'] == 'none'


def test_review_order_puts_the_most_urgent_knowledge_point_first():
    result = diagnose_records([
        record('q1', True, 'k1', '定义域', '函数'),
        record('q2', True, 'k1', '定义域', '函数'),
        record('q3', True, 'k2', '值域', '函数'),
        record('q4', False, 'k2', '值域', '函数'),
        record('q5', False, 'k3', '单调性', '函数'),
    ])
    order = [item['name'] for item in result['review_order']]
    assert order[0] == '定义域' and order[-1] == '单调性'


def test_renderers_state_the_review_priority():
    result = diagnose_records([record('q1', True, 'k1', '定义域', '函数'), record('q2', True, 'k1', '定义域', '函数')])
    assert '复习优先级 高' in render_markdown(result) and '复习优先顺序' in render_markdown(result)
    assert '复习优先级 高' in render_html(result) and '复习优先顺序' in render_html(result)


def build_report(client, files, confirm_all=True):
    submission = client.post('/api/submissions', json={'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '质量'}).json()
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    for name, text in files:
        client.post(f"/api/upload-sessions/{session['id']}/assets", files={'file': (name, text.encode(), 'text/plain')})
    job = client.post(f"/api/upload-sessions/{session['id']}/finish").json()['job_id']
    client.post(f"/api/jobs/{job}/run")
    draft = client.get(f"/api/exams/{exam['id']}/parse-draft").json()['questions']
    for item in draft:
        item.update({'is_wrong_question': True, 'original_question_available': True, 'student_answer': '错答' if confirm_all else ''})
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': draft})
    run = client.post(f"/api/submissions/{submission['id']}/analysis-runs", json={'exam_ids': [exam['id']]}).json()
    client.post(f"/api/analysis-runs/{run['id']}/confirm-start")
    return run, client.get(f"/api/analysis-runs/{run['id']}/report").json()


def test_report_collects_every_caveat_into_one_quality_flag_list():
    client = TestClient(app)
    _, report = build_report(client, [('math.txt', '数学 第1题 函数定义域'), ('physics.txt', '物理 第1题 牛顿第二定律')])
    codes = {flag['code'] for flag in report['quality_flags']}
    assert 'incomplete_materials' in codes
    assert 'page_excluded' in codes
    assert 'missing_scores' in codes
    assert all(flag['message'] and flag['severity'] in {'warning', 'info'} for flag in report['quality_flags'])


def test_quality_flags_name_the_exam_that_is_missing_materials():
    client = TestClient(app)
    _, report = build_report(client, [('math.txt', '数学 第1题 函数定义域')])
    incomplete = next(flag for flag in report['quality_flags'] if flag['code'] == 'incomplete_materials')
    assert incomplete['exam_name'] == '质量' and 'has_answer_key' in incomplete['detail']


def test_exported_report_shows_the_quality_flags_section():
    client = TestClient(app)
    run, _ = build_report(client, [('math.txt', '数学 第1题 函数定义域')])
    markdown = client.get(f"/api/reports/{run['id']}/export?format=md").text
    html = client.get(f"/api/reports/{run['id']}/export?format=html").text
    assert '结论可信度提示' in markdown and '需注意' in markdown
    assert '结论可信度提示' in html


def test_renderers_skip_the_quality_section_when_nothing_is_flagged():
    clean = {'wrong_question_count': 0, 'clusters': [], 'knowledge_tree': {}, 'verification_questions': [], 'quality_flags': []}
    assert '结论可信度提示' not in render_markdown(clean)
    assert '结论可信度提示' not in render_html(clean)
