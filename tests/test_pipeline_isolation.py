from fastapi.testclient import TestClient
from exam_diagnosis.app import app

PAYLOAD = {'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}


def new_submission(client):
    return client.post('/api/submissions', json=PAYLOAD).json()


def new_exam(client, submission, name):
    return client.post(f"/api/submissions/{submission['id']}/exams", json={'name': name}).json()


def upload(client, exam, files):
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    for name, text in files:
        client.post(f"/api/upload-sessions/{session['id']}/assets", files={'file': (name, text.encode(), 'text/plain')})
    return session


def parse(client, session):
    job_id = client.post(f"/api/upload-sessions/{session['id']}/finish").json()['job_id']
    return job_id, client.post(f"/api/jobs/{job_id}/run")


def confirm(client, exam):
    draft = client.get(f"/api/exams/{exam['id']}/parse-draft").json()['questions']
    for item in draft:
        item.update({'is_wrong_question': True, 'original_question_available': True, 'student_answer': '错答'})
    return client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': draft})


def test_only_registered_stages_can_be_chosen_at_all():
    client = TestClient(app)
    registered = {item['stage'] for item in client.get('/api/knowledge-partitions').json()['partitions']}
    assert registered == {'high'}
    for stage in ('primary', 'middle', 'university', ''):
        rejected = client.post('/api/submissions', json={**PAYLOAD, 'stage': stage})
        assert rejected.status_code == 422


def test_two_upload_sessions_on_the_same_exam_keep_their_assets_apart():
    client = TestClient(app)
    exam = new_exam(client, new_submission(client), '两轮上传')
    first = upload(client, exam, [('paper1.txt', '数学 第1题 函数定义域')])
    second = upload(client, exam, [('paper2.txt', '数学 第2题 三角函数')])
    assert first['id'] != second['id']
    assets = client.get(f"/api/exams/{exam['id']}/assets").json()['assets']
    by_session = {asset['upload_session_id'] for asset in assets}
    assert by_session == {first['id'], second['id']}
    assert len({asset['storage_path'] for asset in assets}) == 2


def test_uploads_from_different_exams_never_bleed_into_each_other():
    client = TestClient(app)
    submission = new_submission(client)
    left, right = new_exam(client, submission, '左'), new_exam(client, submission, '右')
    parse(client, upload(client, left, [('paper.txt', '数学 第1题 函数定义域')]))
    parse(client, upload(client, right, [('paper.txt', '数学 第2题 三角函数')]))
    left_pages = client.get(f"/api/exams/{left['id']}/pages").json()['pages']
    right_pages = client.get(f"/api/exams/{right['id']}/pages").json()['pages']
    assert len(left_pages) == 1 and len(right_pages) == 1
    assert '第1题' in left_pages[0]['ocr_text'] and '第2题' in right_pages[0]['ocr_text']


def test_one_upload_of_mixed_material_types_is_classified_per_file():
    client = TestClient(app)
    exam = new_exam(client, new_submission(client), '混传')
    session = upload(client, exam, [
        ('试卷.txt', '数学 第1题 函数定义域'),
        ('答案.txt', '数学 第1题 参考答案'),
        ('学生作答.txt', '数学 第1题 学生答案'),
        ('批改.txt', '数学 第1题 教师批改'),
    ])
    _, response = parse(client, session)
    counts = next(stage['result']['material_counts'] for stage in response.json()['stages'] if stage['name'] == 'classify')
    assert counts == {'paper': 1, 'answer_key': 1, 'student_answer': 1, 'correction': 1, 'unknown': 0}


def test_a_failed_parse_job_does_not_disturb_another_exam():
    client = TestClient(app)
    submission = new_submission(client)
    broken, healthy = new_exam(client, submission, '坏的'), new_exam(client, submission, '好的')
    broken_job, _ = parse(client, upload(client, broken, [('paper.txt', '数学 第1题 函数定义域')]))
    client.post(f"/api/jobs/{broken_job}/fail", json={'stage': 'ocr', 'error': '模拟失败'})
    parse(client, upload(client, healthy, [('paper.txt', '数学 第2题 三角函数')]))
    assert client.get(f"/api/jobs/{broken_job}").json()['status'] == 'failed'
    assert client.get(f"/api/exams/{healthy['id']}").json()['status'] == 'waiting_confirmation'
    assert len(client.get(f"/api/exams/{healthy['id']}/parse-draft").json()['questions']) == 1


def test_analysis_can_target_a_subset_of_the_confirmed_exams():
    client = TestClient(app)
    submission = new_submission(client)
    first, second, third = (new_exam(client, submission, name) for name in ('一', '二', '三'))
    for exam, text in ((first, '数学 第1题 函数定义域'), (second, '数学 第2题 三角函数'), (third, '数学 第3题 数列通项')):
        parse(client, upload(client, exam, [('paper.txt', text)]))
        confirm(client, exam)
    run = client.post(f"/api/submissions/{submission['id']}/analysis-runs", json={'exam_ids': [first['id'], third['id']]}).json()
    client.post(f"/api/analysis-runs/{run['id']}/confirm-start")
    report = client.get(f"/api/analysis-runs/{run['id']}/report").json()
    assert report['question_count'] == 2
    assert {item['name'] for item in report['exam_versions']} == {'一', '三'}


def test_unconfirmed_exams_cannot_be_pulled_into_an_analysis_run():
    client = TestClient(app)
    submission = new_submission(client)
    confirmed, pending = new_exam(client, submission, '已确认'), new_exam(client, submission, '未确认')
    parse(client, upload(client, confirmed, [('paper.txt', '数学 第1题 函数定义域')]))
    confirm(client, confirmed)
    rejected = client.post(f"/api/submissions/{submission['id']}/analysis-runs", json={'exam_ids': [confirmed['id'], pending['id']]})
    assert rejected.status_code == 422 and rejected.json()['error'] == 'exams_not_confirmed'


def test_retrying_a_failed_job_reparses_without_duplicating_pages_or_questions():
    client = TestClient(app)
    exam = new_exam(client, new_submission(client), '重跑')
    job_id, _ = parse(client, upload(client, exam, [('paper.txt', '数学 第1题 函数定义域')]))
    pages_before = len(client.get(f"/api/exams/{exam['id']}/pages").json()['pages'])
    questions_before = len(client.get(f"/api/exams/{exam['id']}/parse-draft").json()['questions'])
    client.post(f"/api/jobs/{job_id}/fail", json={'stage': 'ocr', 'error': '模拟失败'})
    retried = client.post(f"/api/jobs/{job_id}/retry").json()
    client.post(f"/api/jobs/{retried['id']}/run")
    assert len(client.get(f"/api/exams/{exam['id']}/pages").json()['pages']) == pages_before
    assert len(client.get(f"/api/exams/{exam['id']}/parse-draft").json()['questions']) == questions_before


def test_a_reparse_keeps_the_user_decision_about_which_pages_count():
    client = TestClient(app)
    exam = new_exam(client, new_submission(client), '重跑保留判定')
    session = upload(client, exam, [('math.txt', '数学 第1题 函数定义域'), ('physics.txt', '物理 第1题 牛顿第二定律')])
    job_id, _ = parse(client, session)
    mismatched = next(page for page in client.get(f"/api/exams/{exam['id']}/pages").json()['pages'] if page['subject_mismatch'])
    client.patch(f"/api/pages/{mismatched['id']}", json={'include_in_analysis': True})
    client.post(f"/api/jobs/{job_id}/fail", json={'stage': 'ocr', 'error': '模拟失败'})
    retried = client.post(f"/api/jobs/{job_id}/retry").json()
    client.post(f"/api/jobs/{retried['id']}/run")
    pages = client.get(f"/api/exams/{exam['id']}/pages").json()
    reparsed = next(page for page in pages['pages'] if page['subject_mismatch'])
    assert reparsed['include_in_analysis'] is True and reparsed['inclusion_decided_by'] == 'user'
    assert pages['excluded_pages'] == []


def test_a_reparse_keeps_draft_edits_and_repoints_them_at_the_new_pages():
    client = TestClient(app)
    exam = new_exam(client, new_submission(client), '重跑保留编辑')
    job_id, _ = parse(client, upload(client, exam, [('paper.txt', '数学 第1题 函数定义域')]))
    draft = client.get(f"/api/exams/{exam['id']}/parse-draft").json()['questions']
    draft[0]['question_text'] = '用户改过的题干'
    client.patch(f"/api/exams/{exam['id']}/parse-draft", json={'questions': draft})
    client.post(f"/api/jobs/{job_id}/fail", json={'stage': 'ocr', 'error': '模拟失败'})
    retried = client.post(f"/api/jobs/{job_id}/retry").json()
    client.post(f"/api/jobs/{retried['id']}/run")
    rebuilt = client.get(f"/api/exams/{exam['id']}/parse-draft").json()['questions']
    live_pages = {page['id'] for page in client.get(f"/api/exams/{exam['id']}/pages").json()['pages']}
    assert [item['question_text'] for item in rebuilt] == ['用户改过的题干']
    assert rebuilt[0]['original_source']['page_id'] in live_pages
