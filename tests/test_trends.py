"""Longitudinal trends across a submission's exams: ordering, per-knowledge series, and the
honesty rules about what a comparison may claim."""
import pytest
from fastapi.testclient import TestClient

import exam_diagnosis.app as app_module
from exam_diagnosis.trends import COMPARABILITY_NOTE, build_trends, exam_sort_key

SUBMISSION = {'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}


def _exam(eid, seq, date=None, name=None):
    return {'id': eid, 'submission_id': 's', 'sequence_no': seq, 'exam_date': date, 'name': name or eid, 'version': 1}


def _record(rid, exam_id, wrong, knowledge=('k1',)):
    return {
        'id': rid, 'exam_id': exam_id, 'status': 'confirmed', 'is_wrong_question': wrong,
        'knowledge_points': [{'knowledge_id': k, 'name': f'知识点{k}', 'chapter': '函数'} for k in knowledge],
    }


# --- ordering -------------------------------------------------------------------------------------


def test_exams_are_ordered_by_date_then_sequence():
    exams = [_exam('c', 3, '2026-03-01'), _exam('a', 1, '2026-01-01'), _exam('d', 2), _exam('b', 2, '2026-02-01')]
    assert [e['id'] for e in sorted(exams, key=exam_sort_key)] == ['a', 'b', 'c', 'd']  # undated sorts last
    result = build_trends(exams, {})
    assert [e['exam_id'] for e in result['exams']] == ['a', 'b', 'c', 'd']


def test_empty_submission_yields_empty_but_valid_shape():
    result = build_trends([], {})
    assert result['exams'] == [] and result['knowledge_trends'] == []
    assert result['summary']['overall_direction'] == 'insufficient'
    assert result['summary']['first_error_rate'] is None and result['summary']['overall_delta'] is None
    assert result['note'] == COMPARABILITY_NOTE


# --- per-exam and per-knowledge series ---------------------------------------------------------------


def test_per_exam_counts_and_knowledge_series():
    exams = [_exam('e1', 1, '2026-01-01'), _exam('e2', 2, '2026-02-01')]
    records = {
        'e1': [_record('r1', 'e1', True), _record('r2', 'e1', True), _record('r3', 'e1', False)],
        'e2': [_record('r4', 'e2', False), _record('r5', 'e2', False)],
    }
    result = build_trends(exams, records)
    first, second = result['exams']
    assert (first['question_count'], first['wrong_count'], first['error_rate']) == (3, 2, 0.6667)
    assert (second['question_count'], second['wrong_count'], second['error_rate']) == (2, 0, 0.0)
    assert first['analysed'] is False and first['root_cause_counts'] == {}
    trend = result['knowledge_trends'][0]
    assert trend['knowledge_id'] == 'k1' and trend['exam_count'] == 2
    assert [p['error_rate'] for p in trend['points']] == [0.6667, 0.0]
    assert trend['direction'] == 'improving' and trend['delta'] == -0.6667
    assert trend['total_question_count'] == 5 and trend['total_wrong_count'] == 2
    assert result['summary']['improving'] == ['k1'] and result['summary']['worsening'] == []
    assert result['summary']['overall_direction'] == 'improving'


def test_worsening_and_stable_are_distinguished_by_threshold():
    exams = [_exam('e1', 1), _exam('e2', 2)]
    worse = build_trends(exams, {
        'e1': [_record('a', 'e1', False), _record('b', 'e1', False)],
        'e2': [_record('c', 'e2', True), _record('d', 'e2', True)],
    })['knowledge_trends'][0]
    assert worse['direction'] == 'worsening' and worse['delta'] == 1.0
    # 1/2 → 2/3 is a 0.17 move: under the threshold, so it stays "stable" rather than being
    # reported as a change the data can't support.
    stable = build_trends(exams, {
        'e1': [_record('a', 'e1', True), _record('b', 'e1', False)],
        'e2': [_record('c', 'e2', True), _record('d', 'e2', True), _record('e', 'e2', False)],
    })['knowledge_trends'][0]
    assert stable['direction'] == 'stable' and abs(stable['delta']) < 0.2


def test_single_exam_never_claims_a_direction():
    result = build_trends([_exam('e1', 1)], {'e1': [_record('a', 'e1', True)]})
    trend = result['knowledge_trends'][0]
    assert trend['direction'] == 'insufficient' and trend['delta'] is None
    assert trend['persistent'] is False  # one exam is not a pattern
    assert result['summary']['overall_direction'] == 'insufficient'


def test_knowledge_point_only_appears_for_exams_that_assessed_it():
    exams = [_exam('e1', 1), _exam('e2', 2), _exam('e3', 3)]
    result = build_trends(exams, {
        'e1': [_record('a', 'e1', True, ('k1',))],
        'e2': [_record('b', 'e2', False, ('k2',))],
        'e3': [_record('c', 'e3', False, ('k1',))],
    })
    by_id = {t['knowledge_id']: t for t in result['knowledge_trends']}
    assert [p['exam_id'] for p in by_id['k1']['points']] == ['e1', 'e3']  # e2 didn't assess k1
    assert by_id['k1']['direction'] == 'improving'
    assert by_id['k2']['exam_count'] == 1 and by_id['k2']['direction'] == 'insufficient'


def test_persistent_flags_points_still_wrong_in_the_latest_exam():
    exams = [_exam('e1', 1), _exam('e2', 2), _exam('e3', 3)]
    result = build_trends(exams, {
        'e1': [_record('a', 'e1', True, ('stuck',)), _record('b', 'e1', True, ('fixed',))],
        'e2': [_record('c', 'e2', True, ('stuck',)), _record('d', 'e2', False, ('fixed',))],
        'e3': [_record('e', 'e3', True, ('stuck',)), _record('f', 'e3', False, ('fixed',))],
    })
    assert result['summary']['persistent'] == ['stuck']
    by_id = {t['knowledge_id']: t for t in result['knowledge_trends']}
    assert by_id['stuck']['persistent'] is True and by_id['fixed']['persistent'] is False


def test_worst_and_most_recent_sorts_first():
    exams = [_exam('e1', 1), _exam('e2', 2)]
    result = build_trends(exams, {
        'e1': [_record('a', 'e1', False, ('good',)), _record('b', 'e1', True, ('bad',))],
        'e2': [_record('c', 'e2', False, ('good',)), _record('d', 'e2', True, ('bad',))],
    })
    assert [t['knowledge_id'] for t in result['knowledge_trends']] == ['bad', 'good']


def test_unmapped_records_count_toward_the_exam_but_not_any_knowledge_trend():
    result = build_trends([_exam('e1', 1)], {'e1': [{'id': 'r', 'exam_id': 'e1', 'status': 'confirmed', 'is_wrong_question': True, 'knowledge_points': []}]})
    assert result['exams'][0]['wrong_count'] == 1 and result['knowledge_trends'] == []


# --- root causes from reports --------------------------------------------------------------------


def test_root_causes_are_attributed_to_the_exams_their_report_covered():
    exams = [_exam('e1', 1), _exam('e2', 2)]
    records = {'e1': [_record('r1', 'e1', True), _record('r2', 'e1', True)], 'e2': [_record('r3', 'e2', True)]}
    reports = [{
        'exam_versions': [{'exam_id': 'e1', 'name': 'x', 'version': 1}],
        'wrong_question_collection': [
            {'question_id': 'r1', 'root_cause': '条件遗漏'},
            {'question_id': 'r2', 'root_cause': '条件遗漏'},
            {'question_id': 'r3', 'root_cause': '计算错误'},  # belongs to e2, not covered by this report
        ],
    }]
    result = build_trends(exams, records, reports)
    first, second = result['exams']
    assert first['analysed'] is True and first['root_cause_counts'] == {'条件遗漏': 2}
    assert second['analysed'] is False and second['root_cause_counts'] == {}
    assert result['summary']['root_cause_counts'] == {'条件遗漏': 2}
    assert result['summary']['analysed_exam_count'] == 1


# --- API -------------------------------------------------------------------------------------------


def _confirmed_exam(client, submission_id, name, questions):
    exam = client.post(f'/api/submissions/{submission_id}/exams', json={'name': name}).json()
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': questions})
    return exam


def _q(number, wrong, text='求函数的定义域'):
    return {'question_number': number, 'question_text': text, 'is_wrong_question': wrong, 'original_question_available': True}


def test_trends_endpoint_follows_a_knowledge_point_across_two_exams():
    client = TestClient(app_module.app)
    submission = client.post('/api/submissions', json=SUBMISSION).json()
    # An exam needs at least one wrong question to be confirmable (the tool exists to diagnose
    # mistakes), so improvement shows up as a falling error rate, not as a flawless paper.
    _confirmed_exam(client, submission['id'], '月考一', [_q('1', True), _q('2', True)])
    _confirmed_exam(client, submission['id'], '月考二', [_q('1', True), _q('2', False), _q('3', False)])
    body = client.get(f"/api/submissions/{submission['id']}/trends").json()
    assert [e['name'] for e in body['exams']] == ['月考一', '月考二']
    assert [e['error_rate'] for e in body['exams']] == [1.0, 0.3333]
    domain = next(t for t in body['knowledge_trends'] if t['knowledge_id'] == 'high.math.function.domain')
    assert domain['direction'] == 'improving' and domain['exam_count'] == 2
    assert [p['question_count'] for p in domain['points']] == [2, 3]
    assert body['summary']['overall_direction'] == 'improving'
    assert body['summary']['improving'] == ['high.math.function.domain']
    assert body['note'] == COMPARABILITY_NOTE


def test_trends_are_private_and_missing_submissions_are_404():
    client = TestClient(app_module.app)
    submission = client.post('/api/submissions', json=SUBMISSION).json()
    assert client.get('/api/submissions/nope/trends').status_code == 404
    other = TestClient(app_module.app, headers={'X-User-Id': 'someone-else'})
    assert other.get(f"/api/submissions/{submission['id']}/trends").status_code == 404


def test_reopened_exam_contributes_only_its_current_confirmed_records():
    client = TestClient(app_module.app)
    submission = client.post('/api/submissions', json=SUBMISSION).json()
    exam = _confirmed_exam(client, submission['id'], '月考', [_q('1', True), _q('2', True, '判断单调性')])
    assert client.get(f"/api/submissions/{submission['id']}/trends").json()['exams'][0]['question_count'] == 2
    client.post(f"/api/exams/{exam['id']}/reopen")
    # superseded records must not linger in the trend
    assert client.get(f"/api/submissions/{submission['id']}/trends").json()['exams'][0]['question_count'] == 0
