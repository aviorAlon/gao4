"""Background task queue: parse jobs and report generation off the request thread, with polling."""
import threading

import pytest
from fastapi.testclient import TestClient

import exam_diagnosis.app as app_module
from exam_diagnosis.storage import SQLiteMapping
from exam_diagnosis.tasks import TaskQueue


# --- TaskQueue on its own -----------------------------------------------------------------------


def test_task_runs_in_background_and_records_result():
    store = {}
    flushed = []
    queue = TaskQueue(store, workers=1, after_task=lambda: flushed.append(True))
    gate = threading.Event()

    def work():
        gate.wait(5)
        return {'answer': 42}

    task = queue.submit('demo', work, user_id='u', job_id='j1')
    assert task['status'] in ('queued', 'running') and task['job_id'] == 'j1' and task['user_id'] == 'u'
    gate.set()
    done = queue.wait(task['id'], timeout=5)
    assert done['status'] == 'completed' and done['result'] == {'answer': 42}
    assert done['started_at'] and done['finished_at'] and done['error'] is None
    assert flushed == [True]


def test_task_failure_lands_on_the_record_not_the_worker():
    store = {}
    queue = TaskQueue(store, workers=1)

    def boom():
        raise ValueError('bad input')

    failed = queue.wait(queue.submit('demo', boom, user_id='u')['id'], timeout=5)
    assert failed['status'] == 'failed' and failed['error'] == 'ValueError: bad input'
    # the worker is still alive afterwards
    ok = queue.wait(queue.submit('demo', lambda: 'fine', user_id='u')['id'], timeout=5)
    assert ok['status'] == 'completed' and ok['result'] == 'fine'


def test_single_worker_runs_tasks_in_submission_order():
    store, order = {}, []
    queue = TaskQueue(store, workers=1)
    gate = threading.Event()

    def first():
        gate.wait(5)
        order.append(1)

    a = queue.submit('demo', first, user_id='u')
    b = queue.submit('demo', lambda: order.append(2), user_id='u')
    assert queue.active_for(kind='demo')['id'] == a['id']
    gate.set()
    queue.wait(b['id'], timeout=5)
    assert order == [1, 2]
    assert queue.active_for(kind='demo') is None


def test_recover_orphaned_marks_stale_active_tasks_failed():
    store = {
        't1': {'id': 't1', 'kind': 'x', 'status': 'running', 'error': None},
        't2': {'id': 't2', 'kind': 'x', 'status': 'queued', 'error': None},
        't3': {'id': 't3', 'kind': 'x', 'status': 'completed', 'error': None},
    }
    recovered = TaskQueue(store).recover_orphaned()
    assert sorted(t['id'] for t in recovered) == ['t1', 't2']
    assert store['t1']['status'] == 'failed' and store['t1']['error'] == 'process_restarted' and store['t1']['finished_at']
    assert store['t3']['status'] == 'completed'


def test_worker_count_is_env_configurable(monkeypatch):
    assert app_module.TASK_QUEUE.workers == 1  # default
    assert TaskQueue({}, workers=0).workers == 1  # never zero


# --- parse jobs through the API ------------------------------------------------------------------


def _queued_job(client, content=b'1. What is 2+2\n2. What is 3+3', name='paper.txt', media_type='text/plain'):
    sub = client.post('/api/submissions', json={'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}).json()
    exam = client.post(f"/api/submissions/{sub['id']}/exams", json={'name': '月考'}).json()
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    client.post(f"/api/upload-sessions/{session['id']}/assets", files={'file': (name, content, media_type)})
    job_id = client.post(f"/api/upload-sessions/{session['id']}/finish").json()['job_id']
    return sub, exam, job_id


def test_enqueue_job_returns_202_and_finishes_in_background():
    client = TestClient(app_module.app)
    _, exam, job_id = _queued_job(client)
    response = client.post(f'/api/jobs/{job_id}/enqueue')
    assert response.status_code == 202
    body = response.json()
    assert body['task_id'] and body['job']['task_id'] == body['task_id']
    task = app_module.TASK_QUEUE.wait(body['task_id'], timeout=10)
    assert task['status'] == 'completed' and task['result'] == {'job_id': job_id, 'status': 'completed'}
    assert task['kind'] == 'parse_exam' and task['job_id'] == job_id and task['exam_id'] == exam['id']
    job = client.get(f'/api/jobs/{job_id}').json()
    assert job['status'] == 'completed' and all(stage['status'] == 'completed' for stage in job['stages'])
    assert client.get(f"/api/exams/{exam['id']}").json()['status'] == 'waiting_confirmation'
    assert [q['question_number'] for q in client.get(f"/api/exams/{exam['id']}/parse-draft").json()['questions']] == ['1', '2']
    assert client.get(f"/api/tasks/{body['task_id']}").json()['status'] == 'completed'


def test_worker_results_are_flushed_to_sqlite_without_a_request():
    client = TestClient(app_module.app)
    _, _, job_id = _queued_job(client)
    task_id = client.post(f'/api/jobs/{job_id}/enqueue').json()['task_id']
    app_module.TASK_QUEUE.wait(task_id, timeout=10)
    # A brand-new mapping over the same file has no cache: it sees only what was actually written.
    fresh_jobs = SQLiteMapping('jobs', path=app_module.JOBS.path)
    fresh_tasks = SQLiteMapping('tasks', path=app_module.TASKS.path)
    assert fresh_jobs[job_id]['status'] == 'completed'
    assert fresh_tasks[task_id]['status'] == 'completed'


def test_enqueue_and_run_refuse_a_job_that_is_already_queued_or_running(monkeypatch):
    client = TestClient(app_module.app)
    _, _, job_id = _queued_job(client)
    gate = threading.Event()
    real = app_module._execute_parse_job

    def slow(jid):
        gate.wait(5)
        return real(jid)

    monkeypatch.setattr(app_module, '_execute_parse_job', slow)
    first = client.post(f'/api/jobs/{job_id}/enqueue')
    assert first.status_code == 202
    task_id = first.json()['task_id']
    second = client.post(f'/api/jobs/{job_id}/enqueue')
    assert second.status_code == 409 and second.json() == {'error': 'job_already_enqueued', 'task_id': task_id}
    sync = client.post(f'/api/jobs/{job_id}/run')
    assert sync.status_code == 409 and sync.json()['error'] == 'job_already_enqueued'
    gate.set()
    assert app_module.TASK_QUEUE.wait(task_id, timeout=10)['status'] == 'completed'
    assert client.post(f'/api/jobs/{job_id}/enqueue').status_code == 409  # completed → job_not_queued


def test_failed_background_parse_leaves_job_retryable(monkeypatch):
    client = TestClient(app_module.app)
    _, _, job_id = _queued_job(client)
    monkeypatch.setattr(app_module, 'classify_filename', lambda filename: (_ for _ in ()).throw(RuntimeError('classifier exploded')))
    task_id = client.post(f'/api/jobs/{job_id}/enqueue').json()['task_id']
    task = app_module.TASK_QUEUE.wait(task_id, timeout=10)
    assert task['status'] == 'failed' and task['error'] == 'RuntimeError: classify: classifier exploded'
    job = client.get(f'/api/jobs/{job_id}').json()
    assert job['status'] == 'failed' and job['failed_stage'] == 'classify' and job['error'] == 'classifier exploded'
    monkeypatch.undo()
    assert client.post(f'/api/jobs/{job_id}/retry').status_code == 202
    task = app_module.TASK_QUEUE.wait(client.post(f'/api/jobs/{job_id}/enqueue').json()['task_id'], timeout=10)
    assert task['status'] == 'completed'


def test_synchronous_run_still_works_and_returns_the_finished_job():
    client = TestClient(app_module.app)
    _, _, job_id = _queued_job(client)
    response = client.post(f'/api/jobs/{job_id}/run')
    assert response.status_code == 200 and response.json()['status'] == 'completed'


# --- report generation through the API -------------------------------------------------------------


def _confirmed_run(client):
    sub = client.post('/api/submissions', json={'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}).json()
    exam = client.post(f"/api/submissions/{sub['id']}/exams", json={'name': '月考'}).json()
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [{'question_number': '1', 'question_text': '函数定义域', 'is_wrong_question': True, 'original_question_available': True}]})
    run = client.post(f"/api/submissions/{sub['id']}/analysis-runs", json={'exam_ids': [exam['id']]}).json()
    client.post(f"/api/analysis-runs/{run['id']}/confirm-start")
    return run['id']


def test_generate_report_in_background_then_fetch(monkeypatch):
    client = TestClient(app_module.app)
    rid = _confirmed_run(client)
    gate = threading.Event()
    real = app_module._generate_report

    def slow(*args, **kwargs):
        gate.wait(5)
        return real(*args, **kwargs)

    monkeypatch.setattr(app_module, '_generate_report', slow)
    started = client.post(f'/api/analysis-runs/{rid}/generate')
    assert started.status_code == 202
    task_id = started.json()['task_id']
    assert started.json()['status'] == 'generating'
    # while it runs: fetching the report says "come back later" instead of starting a second one
    pending = client.get(f'/api/analysis-runs/{rid}/report')
    assert pending.status_code == 202 and pending.json()['error'] == 'report_generating' and pending.json()['task_id'] == task_id
    again = client.post(f'/api/analysis-runs/{rid}/generate')
    assert again.status_code == 202 and again.json()['task_id'] == task_id
    gate.set()
    task = app_module.TASK_QUEUE.wait(task_id, timeout=10)
    assert task['status'] == 'completed' and task['result'] == {'run_id': rid, 'status': 'report_ready'}
    ready = client.get(f'/api/analysis-runs/{rid}/report')
    assert ready.status_code == 200 and ready.json()['wrong_question_count'] == 1
    # once ready, generate is a no-op that says so
    assert client.post(f'/api/analysis-runs/{rid}/generate').status_code == 200


def test_generate_refuses_a_run_that_was_never_started():
    client = TestClient(app_module.app)
    sub = client.post('/api/submissions', json={'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}).json()
    exam = client.post(f"/api/submissions/{sub['id']}/exams", json={'name': '月考'}).json()
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [{'question_number': '1', 'question_text': 'x', 'is_wrong_question': True, 'original_question_available': True}]})
    run = client.post(f"/api/submissions/{sub['id']}/analysis-runs", json={'exam_ids': [exam['id']]}).json()
    assert client.post(f"/api/analysis-runs/{run['id']}/generate").status_code == 409
    assert client.post('/api/analysis-runs/nope/generate').status_code == 404


def test_failed_background_generation_reverts_run_to_queued(monkeypatch):
    client = TestClient(app_module.app)
    rid = _confirmed_run(client)
    monkeypatch.setattr(app_module, 'find_partition', lambda *args, **kwargs: None)
    task = app_module.TASK_QUEUE.wait(client.post(f'/api/analysis-runs/{rid}/generate').json()['task_id'], timeout=10)
    assert task['status'] == 'failed' and task['error'] == 'RuntimeError: knowledge_partition_not_found'
    assert app_module.RUNS[rid]['status'] == 'queued'
    monkeypatch.undo()
    assert client.get(f'/api/analysis-runs/{rid}/report').status_code == 200


# --- task listing, ownership, restart recovery -------------------------------------------------------


def test_tasks_are_listed_per_user_and_hidden_from_other_users():
    client = TestClient(app_module.app)
    _, _, job_id = _queued_job(client)
    task_id = client.post(f'/api/jobs/{job_id}/enqueue').json()['task_id']
    app_module.TASK_QUEUE.wait(task_id, timeout=10)
    listing = client.get('/api/tasks').json()
    assert [t['id'] for t in listing['tasks']] == [task_id] and listing['workers'] == 1
    assert client.get('/api/tasks?status=failed').json()['tasks'] == []
    other = TestClient(app_module.app, headers={'X-User-Id': 'someone-else'})
    assert other.get('/api/tasks').json()['tasks'] == []
    assert other.get(f'/api/tasks/{task_id}').status_code == 404
    assert client.get('/api/tasks/nope').status_code == 404


def test_startup_recovery_repairs_jobs_and_runs_left_mid_flight():
    app_module.TASKS['t-job'] = {'id': 't-job', 'kind': 'parse_exam', 'status': 'running', 'user_id': 'local', 'job_id': 'j1', 'created_at': 'x', 'error': None}
    app_module.TASKS['t-run'] = {'id': 't-run', 'kind': 'generate_report', 'status': 'queued', 'user_id': 'local', 'run_id': 'r1', 'created_at': 'x', 'error': None}
    app_module.JOBS['j1'] = {'id': 'j1', 'status': 'running', 'stages': [{'name': 'classify', 'status': 'completed', 'error': None}, {'name': 'ocr', 'status': 'running', 'error': None}, {'name': 'question_split', 'status': 'queued', 'error': None}]}
    app_module.RUNS['r1'] = {'id': 'r1', 'status': 'generating'}
    recovered = app_module.recover_orphaned_tasks()
    assert sorted(t['id'] for t in recovered) == ['t-job', 't-run']
    job = app_module.JOBS['j1']
    assert job['status'] == 'failed' and job['failed_stage'] == 'ocr' and job['error'] == 'process_restarted'
    assert job['stages'][1] == {'name': 'ocr', 'status': 'failed', 'error': 'process_restarted'}
    assert app_module.RUNS['r1']['status'] == 'queued'
    assert app_module.TASKS['t-job']['status'] == 'failed' and app_module.TASKS['t-job']['error'] == 'process_restarted'
    # idempotent: nothing left to recover on a second call
    assert app_module.recover_orphaned_tasks() == []
