from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def build_exam(client, files):
    submission = client.post('/api/submissions', json={'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '混传'}).json()
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    for name, text in files:
        client.post(f"/api/upload-sessions/{session['id']}/assets", files={'file': (name, text.encode(), 'text/plain')})
    job = client.post(f"/api/upload-sessions/{session['id']}/finish").json()['job_id']
    client.post(f"/api/jobs/{job}/run")
    return submission, exam


def test_other_subject_page_is_kept_visible_but_excluded_from_the_draft():
    client = TestClient(app)
    _, exam = build_exam(client, [('math.txt', '数学 第1题 函数定义域'), ('physics.txt', '物理 第1题 牛顿第二定律')])
    pages = client.get(f"/api/exams/{exam['id']}/pages").json()
    mismatched = [page for page in pages['pages'] if page['subject_mismatch']]
    assert len(mismatched) == 1 and mismatched[0]['include_in_analysis'] is False
    assert mismatched[0]['exclusion_reason'] == 'subject_mismatch'
    assert pages['excluded_pages'][0]['page_id'] == mismatched[0]['id']
    draft = client.get(f"/api/exams/{exam['id']}/parse-draft").json()['questions']
    assert draft and all(item['original_source']['page_id'] != mismatched[0]['id'] for item in draft)


def test_user_can_explicitly_pull_a_mismatched_page_back_into_the_analysis():
    client = TestClient(app)
    _, exam = build_exam(client, [('math.txt', '数学 第1题 函数定义域'), ('physics.txt', '物理 第1题 牛顿第二定律')])
    mismatched = next(page for page in client.get(f"/api/exams/{exam['id']}/pages").json()['pages'] if page['subject_mismatch'])
    before = len(client.get(f"/api/exams/{exam['id']}/parse-draft").json()['questions'])
    patched = client.patch(f"/api/pages/{mismatched['id']}", json={'include_in_analysis': True}).json()
    assert patched['inclusion_decided_by'] == 'user' and patched['subject_mismatch'] is True
    assert patched['draft_rebuild']['pages_excluded'] == 0
    after = client.get(f"/api/exams/{exam['id']}/parse-draft").json()['questions']
    assert len(after) == before + 1


def test_user_can_exclude_a_matching_page_and_the_draft_shrinks():
    client = TestClient(app)
    _, exam = build_exam(client, [('math.txt', '数学 第1题 函数定义域'), ('math2.txt', '数学 第2题 三角函数')])
    pages = client.get(f"/api/exams/{exam['id']}/pages").json()['pages']
    before = len(client.get(f"/api/exams/{exam['id']}/parse-draft").json()['questions'])
    client.patch(f"/api/pages/{pages[0]['id']}", json={'include_in_analysis': False})
    after = client.get(f"/api/exams/{exam['id']}/parse-draft").json()
    assert len(after['questions']) < before
    assert client.get(f"/api/exams/{exam['id']}/pages").json()['excluded_pages'][0]['reason'] == 'user_excluded'


def test_excluded_pages_are_named_in_the_confirmed_completeness_report():
    client = TestClient(app)
    _, exam = build_exam(client, [('math.txt', '数学 第1题 函数定义域'), ('physics.txt', '物理 第1题 牛顿第二定律')])
    draft = client.get(f"/api/exams/{exam['id']}/parse-draft").json()['questions']
    for item in draft:
        item.update({'is_wrong_question': True, 'original_question_available': True, 'student_answer': '错答'})
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': draft})
    completeness = client.get(f"/api/exams/{exam['id']}").json()['completeness']
    assert completeness['excluded_pages'] and completeness['excluded_pages'][0]['reason'] == 'subject_mismatch'


def test_editing_a_page_does_not_discard_draft_edits_made_by_the_user():
    client = TestClient(app)
    _, exam = build_exam(client, [('math.txt', '数学 第1题 函数定义域'), ('physics.txt', '物理 第1题 牛顿第二定律')])
    draft = client.get(f"/api/exams/{exam['id']}/parse-draft").json()['questions']
    draft[0]['question_text'] = '用户改过的题干'
    client.patch(f"/api/exams/{exam['id']}/parse-draft", json={'questions': draft})
    mismatched = next(page for page in client.get(f"/api/exams/{exam['id']}/pages").json()['pages'] if page['subject_mismatch'])
    client.patch(f"/api/pages/{mismatched['id']}", json={'include_in_analysis': True})
    rebuilt = client.get(f"/api/exams/{exam['id']}/parse-draft").json()['questions']
    assert '用户改过的题干' in [item['question_text'] for item in rebuilt]
