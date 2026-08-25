from fastapi.testclient import TestClient
from exam_diagnosis.app import app
from exam_diagnosis.knowledge import require_partition
from exam_diagnosis.reporting import render_html, render_markdown
from exam_diagnosis.review import review_report, withhold_unreviewed_answer


def build_run(client, question_text='函数定义域'):
    submission = client.post('/api/submissions', json={'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '复核'}).json()
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [{'question_number': '1', 'question_text': question_text, 'is_wrong_question': True, 'original_question_available': True, 'student_answer': '错答'}]})
    run = client.post(f"/api/submissions/{submission['id']}/analysis-runs", json={'exam_ids': [exam['id']]}).json()
    client.post(f"/api/analysis-runs/{run['id']}/confirm-start")
    return run, client.get(f"/api/analysis-runs/{run['id']}/report").json()


def test_review_stage_checks_clusters_and_verification_questions():
    partition = require_partition('high', '数学', '广东')
    report = {
        'clusters': [{'cluster_id': 'k:计算错误', 'knowledge_id': partition.points[0]['knowledge_id'], 'question_ids': ['q1']}],
        'verification_questions': [{'knowledge_id': partition.points[0]['knowledge_id'], 'answer': '要点', 'source_question_ids': ['q1']}],
    }
    review = review_report(report, partition)
    assert review['status'] == 'approved' and review['findings'] == []
    assert report['verification_questions'][0]['review_status'] == 'approved'


def test_empty_answer_and_missing_evidence_are_held_back():
    partition = require_partition('high', '数学', '广东')
    report = {'clusters': [], 'verification_questions': [{'knowledge_id': partition.points[0]['knowledge_id'], 'answer': '   ', 'source_question_ids': []}]}
    review = review_report(report, partition)
    assert review['status'] == 'needs_review' and review['pending_verification_questions'] == 1
    assert set(report['verification_questions'][0]['review_findings']) == {'answer_missing', 'evidence_missing'}


def test_unmapped_cluster_knowledge_is_reported_by_the_review_stage():
    partition = require_partition('high', '数学', '广东')
    report = {'clusters': [{'cluster_id': 'unmapped:待人工确认', 'knowledge_id': 'unmapped', 'question_ids': ['q1']}], 'verification_questions': []}
    assert {item['code'] for item in review_report(report, partition)['findings']} == {'cluster_knowledge_unmapped'}


def test_unreviewed_answer_is_never_handed_out():
    held = withhold_unreviewed_answer({'answer': '机密答案', 'review_status': 'needs_review', 'review_findings': ['answer_missing']})
    assert held['answer'] is None and held['answer_withheld'] is True and held['withheld_reason']
    passed = withhold_unreviewed_answer({'answer': '答案', 'review_status': 'approved'})
    assert passed['answer'] == '答案' and passed['answer_withheld'] is False


def test_report_carries_the_review_verdict_and_flags_pending_answers():
    client = TestClient(app)
    _, report = build_run(client, question_text='这道题跟任何知识点都对不上')
    assert report['review']['status'] == 'needs_review'
    assert any(flag['code'] == 'review_pending' for flag in report['quality_flags'])
    assert all(item['answer_withheld'] for item in report['verification_questions'])


def test_api_withholds_the_answer_until_the_user_approves_it():
    client = TestClient(app)
    run, _ = build_run(client, question_text='这道题跟任何知识点都对不上')
    question = client.get(f"/api/analysis-runs/{run['id']}/verification-questions").json()['questions'][0]
    assert question['answer'] is None and question['answer_withheld'] is True
    assert client.get(f"/api/verification-questions/{question['id']}").json()['answer'] is None
    approved = client.post(f"/api/verification-questions/{question['id']}/review", json={'approved': True, 'answer': '人工复核后的参考答案', 'note': '已核对'}).json()
    assert approved['answer'] == '人工复核后的参考答案' and approved['answer_withheld'] is False
    assert client.get(f"/api/verification-questions/{question['id']}").json()['answer'] == '人工复核后的参考答案'


def test_approving_an_empty_answer_is_refused():
    client = TestClient(app)
    run, _ = build_run(client, question_text='这道题跟任何知识点都对不上')
    question = client.get(f"/api/analysis-runs/{run['id']}/verification-questions").json()['questions'][0]
    rejected = client.post(f"/api/verification-questions/{question['id']}/review", json={'approved': True, 'answer': '   '})
    assert rejected.status_code == 422 and rejected.json()['error'] == 'cannot_approve_empty_answer'


def test_report_reflects_the_approval_without_regenerating_the_analysis():
    client = TestClient(app)
    run, before = build_run(client, question_text='这道题跟任何知识点都对不上')
    question = client.get(f"/api/analysis-runs/{run['id']}/verification-questions").json()['questions'][0]
    client.post(f"/api/verification-questions/{question['id']}/review", json={'approved': True, 'answer': '复核答案'})
    after = client.get(f"/api/analysis-runs/{run['id']}/report").json()
    assert after['source_question_ids'] == before['source_question_ids']
    assert after['verification_questions'][0]['answer'] == '复核答案'
    assert after['review']['pending_verification_questions'] == 0


def test_exports_do_not_leak_an_unreviewed_answer():
    client = TestClient(app)
    run, _ = build_run(client, question_text='这道题跟任何知识点都对不上')
    markdown = client.get(f"/api/reports/{run['id']}/export?format=md").text
    assert '未通过复核，答案暂不展示' in markdown
    assert '围绕 unmapped 的定义' not in markdown


def test_renderers_show_the_withheld_marker_for_unreviewed_answers():
    report = {'verification_questions': [{'question_text': '验证题', 'answer': None, 'answer_withheld': True, 'withheld_reason': ['参考答案为空']}], 'clusters': [], 'knowledge_tree': {}, 'wrong_question_count': 0}
    assert '未通过复核，答案暂不展示' in render_markdown(report)
    assert '未通过复核，答案暂不展示' in render_html(report)
