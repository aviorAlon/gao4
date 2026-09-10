"""Spaced repetition over mastery records: the ladder, the reset on failure, and the plan view."""
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

import exam_diagnosis.app as app_module
from exam_diagnosis.review_plan import (
    REVIEW_INTERVALS_DAYS, build_review_plan, due_state, initial_schedule, parse_due, schedule_after_attempt,
)

NOW = datetime(2026, 9, 10, 12, 0, 0)
SUBMISSION = {'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}


def _record(**overrides):
    return {'id': 'm1', 'submission_id': 's1', 'knowledge_id': 'k1', 'error_cluster': '计算错误', 'status': 'not_started', 'attempt_history': [], **initial_schedule(NOW), **overrides}


# --- the ladder ------------------------------------------------------------------------------------


def test_a_new_record_is_due_immediately():
    record = _record()
    assert record['review_stage'] == 0 and parse_due(record['due_at']) == NOW
    assert due_state(record, NOW) == ('due', 0)


def test_each_pass_climbs_one_rung():
    record = _record()
    for expected in REVIEW_INTERVALS_DAYS:
        schedule_after_attempt(record, passed=True, now=NOW)
        assert record['interval_days'] == expected
        assert parse_due(record['due_at']) == NOW + timedelta(days=expected)
        assert due_state(record, NOW) == ('upcoming', expected)


def test_a_failure_drops_back_to_the_first_rung():
    record = _record()
    for _ in range(3):
        schedule_after_attempt(record, passed=True, now=NOW)
    assert record['review_stage'] == 3 and record['interval_days'] == REVIEW_INTERVALS_DAYS[2]  # 3rd pass → 3rd rung
    schedule_after_attempt(record, passed=False, now=NOW)
    assert record['review_stage'] == 0 and record['interval_days'] == REVIEW_INTERVALS_DAYS[0]
    assert parse_due(record['due_at']) == NOW + timedelta(days=REVIEW_INTERVALS_DAYS[0])


def test_the_ladder_stops_only_once_the_status_machine_says_resolved():
    # Climbing past the last rung while still only provisionally resolved keeps scheduling at the
    # longest interval — the schedule never overrides the mastery state machine's own verdict.
    provisional = _record(status='provisionally_resolved')
    for _ in range(len(REVIEW_INTERVALS_DAYS) + 2):
        schedule_after_attempt(provisional, passed=True, now=NOW)
    assert provisional['due_at'] is not None and provisional['interval_days'] == REVIEW_INTERVALS_DAYS[-1]
    assert due_state(provisional, NOW)[0] == 'upcoming'

    done = _record(status='resolved', review_stage=len(REVIEW_INTERVALS_DAYS))  # every rung walked
    schedule_after_attempt(done, passed=True, now=NOW)
    assert done['due_at'] is None and done['interval_days'] is None
    assert due_state(done, NOW) == ('completed', None)


def test_last_reviewed_at_is_stamped_on_every_attempt():
    record = _record()
    assert record['last_reviewed_at'] is None
    schedule_after_attempt(record, passed=False, now=NOW)
    assert parse_due(record['last_reviewed_at']) == NOW


# --- due states -------------------------------------------------------------------------------------


def test_overdue_and_upcoming_are_measured_in_whole_days():
    overdue = _record(due_at=(NOW - timedelta(days=3)).isoformat() + 'Z')
    assert due_state(overdue, NOW) == ('due', -3)
    upcoming = _record(due_at=(NOW + timedelta(days=2)).isoformat() + 'Z')
    assert due_state(upcoming, NOW) == ('upcoming', 2)


def test_records_without_a_schedule_are_due_not_lost():
    # Written before scheduling existed: it exists because something was wrong, so ask now.
    legacy = {'id': 'old', 'submission_id': 's1', 'knowledge_id': 'k', 'status': 'in_progress', 'attempt_history': []}
    assert due_state(legacy, NOW) == ('due', 0)
    assert parse_due('not-a-date') is None and parse_due(None) is None
    plan = build_review_plan([legacy], now=NOW)
    assert plan['summary']['due_count'] == 1


# --- the plan ---------------------------------------------------------------------------------------


def test_plan_groups_and_sorts_most_overdue_first():
    records = [
        _record(id='a', knowledge_id='ka', due_at=(NOW - timedelta(days=1)).isoformat() + 'Z'),
        _record(id='b', knowledge_id='kb', due_at=(NOW - timedelta(days=9)).isoformat() + 'Z'),
        _record(id='c', knowledge_id='kc', due_at=(NOW + timedelta(days=5)).isoformat() + 'Z'),
        _record(id='d', knowledge_id='kd', due_at=(NOW + timedelta(days=2)).isoformat() + 'Z'),
        _record(id='e', knowledge_id='ke', status='resolved', due_at=None),
    ]
    plan = build_review_plan(records, now=NOW)
    assert [item['mastery_id'] for item in plan['due']] == ['b', 'a']  # 9 days overdue before 1
    assert [item['mastery_id'] for item in plan['upcoming']] == ['d', 'c']  # soonest first
    assert [item['mastery_id'] for item in plan['completed']] == ['e']
    assert plan['due'][0]['overdue_days'] == 9 and plan['upcoming'][0]['days_until_due'] == 2
    assert plan['summary'] == {'due_count': 2, 'overdue_count': 2, 'upcoming_count': 2, 'completed_count': 1, 'next_due_at': plan['upcoming'][0]['due_at']}
    assert plan['intervals_days'] == list(REVIEW_INTERVALS_DAYS)


def test_plan_links_the_verification_question_to_practise():
    record = _record()
    questions = {('s1', 'k1'): {'id': 'q1', 'question_text': '变式题干'}}
    item = build_review_plan([record], questions, now=NOW)['due'][0]
    assert item['verification_question_id'] == 'q1' and item['question_text'] == '变式题干'
    # no question for that point yet → the plan still lists it, just without a link
    orphan = build_review_plan([_record(knowledge_id='other')], questions, now=NOW)['due'][0]
    assert orphan['verification_question_id'] is None and orphan['question_text'] is None


def test_empty_plan_is_valid():
    plan = build_review_plan([], now=NOW)
    assert plan['due'] == [] and plan['summary']['due_count'] == 0 and plan['summary']['next_due_at'] is None


# --- API ---------------------------------------------------------------------------------------------


def _submission_with_verification(client):
    submission = client.post('/api/submissions', json=SUBMISSION).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [{'question_number': '1', 'question_text': '函数定义域', 'is_wrong_question': True, 'original_question_available': True}]})
    run = client.post(f"/api/submissions/{submission['id']}/analysis-runs", json={'exam_ids': [exam['id']]}).json()
    client.post(f"/api/analysis-runs/{run['id']}/confirm-start")
    report = client.get(f"/api/analysis-runs/{run['id']}/report").json()
    return submission, report['verification_question_ids'][0]


def test_new_mastery_records_are_due_and_link_their_question():
    client = TestClient(app_module.app)
    submission, question_id = _submission_with_verification(client)
    plan = client.get('/api/review-plan').json()
    assert plan['summary']['due_count'] == 1 and plan['summary']['upcoming_count'] == 0
    item = plan['due'][0]
    assert item['submission_id'] == submission['id'] and item['status'] == 'not_started'
    assert item['verification_question_id'] == question_id and item['question_text']


def test_passing_pushes_the_next_review_out_and_failing_brings_it_back():
    client = TestClient(app_module.app)
    _, question_id = _submission_with_verification(client)
    client.post(f'/api/verification-questions/{question_id}/attempts', json={'answer': 'x', 'score': 10, 'max_score': 10})
    plan = client.get('/api/review-plan').json()
    assert plan['summary']['due_count'] == 0 and plan['summary']['upcoming_count'] == 1
    assert plan['upcoming'][0]['interval_days'] == REVIEW_INTERVALS_DAYS[0] and plan['upcoming'][0]['review_stage'] == 1

    client.post(f'/api/verification-questions/{question_id}/attempts', json={'answer': 'x', 'score': 1, 'max_score': 10})
    after_failure = client.get('/api/review-plan').json()
    assert after_failure['upcoming'][0]['review_stage'] == 0
    assert after_failure['upcoming'][0]['attempt_count'] == 2


def test_plan_can_be_narrowed_to_one_submission_and_is_private():
    client = TestClient(app_module.app)
    first, _ = _submission_with_verification(client)
    _submission_with_verification(client)
    assert client.get('/api/review-plan').json()['summary']['due_count'] == 2
    narrowed = client.get('/api/review-plan', params={'submission_id': first['id']}).json()
    assert narrowed['submission_id'] == first['id'] and narrowed['summary']['due_count'] == 1
    other = TestClient(app_module.app, headers={'X-User-Id': 'someone-else'})
    assert other.get('/api/review-plan').json()['summary']['due_count'] == 0
