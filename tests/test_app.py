from fastapi.testclient import TestClient

from exam_diagnosis.app import app

client = TestClient(app)


def test_submission_survives_application_restart(tmp_path, monkeypatch):
    db_path = tmp_path / 'restart.sqlite3'
    monkeypatch.setenv('EXAM_DIAGNOSIS_DB', str(db_path))
    import importlib
    import exam_diagnosis.app as app_module
    importlib.reload(app_module)
    first = TestClient(app_module.app)
    created = first.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    importlib.reload(app_module)
    second = TestClient(app_module.app)
    response = second.get(f"/api/submissions/{created['id']}")
    assert response.status_code == 200
    assert response.json()['id'] == created['id']

def test_create_submission_locks_single_subject_and_scope():
    response = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
        'is_repeating_student': False, 'target_exam_year': 2027,
    })
    assert response.status_code == 201
    data = response.json()
    assert data['subject'] == '数学'
    assert data['max_exam_count'] == 10
    assert data['learning_scope']['academic_year_label'] == '25-26'

def test_submission_rejects_eleventh_exam():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    for i in range(10):
        r = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': f'考试{i+1}'})
        assert r.status_code == 201
    r = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '第11次'})
    assert r.status_code == 409

def test_upload_and_confirm_requires_wrong_question_and_original_question():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    asset = client.post(f"/api/exams/{exam['id']}/assets", files={'file': ('paper.jpg', b'fake-image', 'image/jpeg')}).json()
    assert asset['status'] == 'received'
    r = client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': []})
    assert r.status_code == 422

def test_confirm_and_start_analysis_creates_run():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    r = client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [{
        'question_number': '17', 'question_text': '函数题', 'is_wrong_question': True,
        'original_question_available': True, 'student_answer': '错解',
    }]})
    assert r.status_code == 200
    r = client.post(f"/api/submissions/{submission['id']}/analysis-runs", json={'exam_ids': [exam['id']]})
    assert r.status_code == 201
    assert r.json()['status'] == 'awaiting_confirmation'
    r = client.post(f"/api/analysis-runs/{r.json()['id']}/confirm-start")
    assert r.status_code == 202
    assert r.json()['status'] == 'queued'


def test_upload_strips_path_components_from_filename():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    response = client.post(f"/api/exams/{exam['id']}/assets", files={'file': ('../unsafe.jpg', b'fake', 'image/jpeg')})
    assert response.status_code == 200
    assert response.json()['filename'] == 'unsafe.jpg'


def test_multiple_assets_are_grouped_in_an_upload_session_and_finished():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    assert session['status'] == 'uploading'
    for name in ('page-1.jpg', 'page-2.jpg'):
        response = client.post(
            f"/api/upload-sessions/{session['id']}/assets",
            files={'file': (name, b'fake-image', 'image/jpeg')},
        )
        assert response.status_code == 201
        assert response.json()['upload_session_id'] == session['id']
    finished = client.post(f"/api/upload-sessions/{session['id']}/finish").json()
    assert finished['status'] == 'processing'
    assert finished['asset_count'] == 2


def test_parse_draft_can_be_corrected_before_exam_confirmation():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    draft = client.get(f"/api/exams/{exam['id']}/parse-draft")
    assert draft.status_code == 200
    corrected = client.patch(
        f"/api/exams/{exam['id']}/parse-draft",
        json={'questions': [{
            'question_number': '18', 'question_text': '修正后的原题',
            'is_wrong_question': True, 'original_question_available': True,
        }]},
    )
    assert corrected.status_code == 200
    assert corrected.json()['questions'][0]['question_number'] == '18'


def test_confirm_uses_corrected_parse_draft_when_questions_are_omitted():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    client.patch(f"/api/exams/{exam['id']}/parse-draft", json={'questions': [{
        'question_number': '18', 'question_text': '修正后的原题',
        'is_wrong_question': True, 'original_question_available': True,
    }]})
    confirmed = client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': []})
    assert confirmed.status_code == 422
    assert confirmed.json()['error'] == 'original_source_required'


def test_confirmed_exam_can_be_reopened_and_new_version_confirmed():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    first = {'question_number': '17', 'question_text': '原题',
             'is_wrong_question': True, 'original_question_available': True}
    assert client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [first]}).status_code == 200
    reopened = client.post(f"/api/exams/{exam['id']}/reopen").json()
    assert reopened['status'] == 'uploading'
    assert reopened['version'] == 2
    second = {'question_number': '18', 'question_text': '补充后的原题',
              'is_wrong_question': True, 'original_question_available': True}
    updated = client.patch(f"/api/exams/{exam['id']}/parse-draft", json={'questions': [second]})
    assert updated.status_code == 200
    confirmed = client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [second]})
    assert confirmed.status_code == 200
    assert confirmed.json()['status'] == 'confirmed'
    assert confirmed.json()['version'] == 2


def test_confirmed_exam_cannot_be_edited_without_reopen():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    question = {'question_number': '1', 'question_text': '原题',
                'is_wrong_question': True, 'original_question_available': True}
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [question]})
    response = client.patch(f"/api/exams/{exam['id']}/parse-draft", json={'questions': [question]})
    assert response.status_code == 409


def test_finish_upload_creates_parse_job_with_stages():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    client.post(
        f"/api/upload-sessions/{session['id']}/assets",
        files={'file': ('page.jpg', b'fake', 'image/jpeg')},
    )
    finished = client.post(f"/api/upload-sessions/{session['id']}/finish")
    assert finished.status_code == 200
    job_id = finished.json()['job_id']
    job = client.get(f"/api/jobs/{job_id}")
    assert job.status_code == 200
    assert job.json()['job_type'] == 'parse_exam'
    assert [stage['name'] for stage in job.json()['stages']] == [
        'classify', 'ocr', 'question_split'
    ]


def test_failed_job_can_be_retried_and_records_attempt():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    client.post(
        f"/api/upload-sessions/{session['id']}/assets",
        files={'file': ('page.jpg', b'fake', 'image/jpeg')},
    )
    job_id = client.post(f"/api/upload-sessions/{session['id']}/finish").json()['job_id']
    failed = client.post(f"/api/jobs/{job_id}/fail", json={'stage': 'ocr', 'error': 'temporary'})
    assert failed.status_code == 200
    retried = client.post(f"/api/jobs/{job_id}/retry")
    assert retried.status_code == 202
    assert retried.json()['attempt'] == 2
    assert retried.json()['status'] == 'queued'


def test_parse_job_run_advances_stages_and_creates_parse_draft():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    client.post(
        f"/api/upload-sessions/{session['id']}/assets",
        files={'file': ('page.jpg', b'fake', 'image/jpeg')},
    )
    job_id = client.post(f"/api/upload-sessions/{session['id']}/finish").json()['job_id']
    response = client.post(f"/api/jobs/{job_id}/run")
    assert response.status_code == 200
    assert response.json()['status'] == 'completed'
    draft = client.get(f"/api/exams/{exam['id']}/parse-draft").json()
    assert draft['status'] == 'waiting_confirmation'
    assert draft['questions'] == []


def test_parse_job_run_rejects_a_job_that_is_not_queued():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    client.post(f"/api/upload-sessions/{session['id']}/assets", files={'file': ('page.jpg', b'fake', 'image/jpeg')})
    job_id = client.post(f"/api/upload-sessions/{session['id']}/finish").json()['job_id']
    client.post(f"/api/jobs/{job_id}/run")
    response = client.post(f"/api/jobs/{job_id}/run")
    assert response.status_code == 409


def test_parse_job_records_page_results_and_classifies_materials():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    for name in ('paper-1.jpg', 'answer-1.jpg'):
        client.post(
            f"/api/upload-sessions/{session['id']}/assets",
            files={'file': (name, b'fake', 'image/jpeg')},
        )
    job_id = client.post(f"/api/upload-sessions/{session['id']}/finish").json()['job_id']
    response = client.post(f"/api/jobs/{job_id}/run")
    assert response.status_code == 200
    result = response.json()
    assert result['stages'][0]['result']['material_counts']['paper'] == 1
    assert result['stages'][0]['result']['material_counts']['answer_key'] == 1
    assert result['stages'][1]['result']['pages_processed'] == 2
    pages = client.get(f"/api/exams/{exam['id']}/pages")
    assert pages.status_code == 200
    assert len(pages.json()['pages']) == 2
    assert pages.json()['pages'][0]['page_order'] == 1


def test_filename_classification_marks_unknown_material_for_confirmation():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    client.post(
        f"/api/upload-sessions/{session['id']}/assets",
        files={'file': ('IMG_001.jpg', b'fake', 'image/jpeg')},
    )
    job_id = client.post(f"/api/upload-sessions/{session['id']}/finish").json()['job_id']
    result = client.post(f"/api/jobs/{job_id}/run").json()
    assert result['stages'][0]['result']['material_counts']['unknown'] == 1
    assets = __import__('exam_diagnosis.app', fromlist=['ASSETS']).ASSETS.values()
    asset = next(value for value in assets if value['exam_id'] == exam['id'])
    assert asset['material_type'] == 'unknown'


def test_auto_parse_draft_is_confirmable_after_job_run():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    client.post(
        f"/api/upload-sessions/{session['id']}/assets",
        files={'file': ('math-paper.txt', '数学 第17题 函数 f(x)'.encode('utf-8'), 'text/plain')},
    )
    job_id = client.post(f"/api/upload-sessions/{session['id']}/finish").json()['job_id']
    client.post(f"/api/jobs/{job_id}/run")
    draft = client.get(f"/api/exams/{exam['id']}/parse-draft").json()
    assert draft['questions'][0]['question_number'] == '17'
    assert draft['questions'][0]['original_question_available'] is True
    corrected = dict(draft['questions'][0])
    corrected['is_wrong_question'] = True
    confirmed = client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [corrected]})
    assert confirmed.status_code == 200


def test_parse_draft_field_edit_preserves_machine_value_and_records_user_value():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    client.patch(f"/api/exams/{exam['id']}/parse-draft", json={'questions': [{
        'question_number': '17', 'question_text': '机器识别题干',
        'is_wrong_question': True, 'original_question_available': True,
    }]})
    response = client.patch(
        f"/api/exams/{exam['id']}/parse-draft/questions/0",
        json={'question_text': '用户修正题干'},
    )
    assert response.status_code == 200
    question = response.json()['questions'][0]
    assert question['question_text'] == '用户修正题干'
    assert question['provenance']['question_text']['machine_value'] == '机器识别题干'
    assert question['provenance']['question_text']['user_value'] == '用户修正题干'
    assert question['provenance']['question_text']['final_value'] == '用户修正题干'


def test_parse_draft_field_edit_rejects_confirmed_exam():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    question = {'question_number': '1', 'question_text': '原题',
                'is_wrong_question': True, 'original_question_available': True}
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [question]})
    response = client.patch(
        f"/api/exams/{exam['id']}/parse-draft/questions/0",
        json={'question_text': '禁止修改'},
    )
    assert response.status_code == 409


def test_wrong_question_can_link_to_original_asset_and_confirmation_reports_completeness():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    asset = client.post(
        f"/api/upload-sessions/{session['id']}/assets",
        files={'file': ('math-paper.jpg', b'fake', 'image/jpeg')},
    ).json()
    draft = client.patch(f"/api/exams/{exam['id']}/parse-draft", json={'questions': [{
        'question_number': '17', 'question_text': '函数题',
        'is_wrong_question': True, 'original_question_available': True,
    }]}).json()
    question_id = draft['questions'][0]['id']
    linked = client.post(
        f"/api/exams/{exam['id']}/questions/{question_id}/link-original",
        json={'asset_id': asset['id']},
    )
    assert linked.status_code == 200
    assert linked.json()['original_source']['asset_id'] == asset['id']
    confirmed = client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': []})
    assert confirmed.status_code == 200
    assert confirmed.json()['completeness']['has_wrong_questions'] is True
    assert confirmed.json()['completeness']['has_original_sources'] is True


def test_confirm_rejects_wrong_question_without_original_source_link():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    client.patch(f"/api/exams/{exam['id']}/parse-draft", json={'questions': [{
        'question_number': '17', 'question_text': '函数题',
        'is_wrong_question': True, 'original_question_available': True,
    }]})
    confirmed = client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': []})
    assert confirmed.status_code == 422
    assert confirmed.json()['error'] == 'original_source_required'


def test_confirmed_exam_persists_question_records_and_links_original_asset():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    asset = client.post(f"/api/upload-sessions/{session['id']}/assets", files={'file': ('paper.jpg', b'fake', 'image/jpeg')}).json()
    draft = client.patch(f"/api/exams/{exam['id']}/parse-draft", json={'questions': [{
        'question_number': '17', 'question_text': '函数题', 'is_wrong_question': True,
        'original_question_available': True, 'student_answer': '错误答案',
    }]}).json()
    client.post(f"/api/exams/{exam['id']}/questions/{draft['questions'][0]['id']}/link-original", json={'asset_id': asset['id']})
    confirmed = client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': []})
    assert confirmed.status_code == 200
    record = confirmed.json()['question_records'][0]
    assert record['question_number'] == '17'
    assert record['student_answer']['text'] == '错误答案'
    assert record['original_source']['asset_id'] == asset['id']
    records = client.get(f"/api/exams/{exam['id']}/question-records")
    assert records.status_code == 200
    assert records.json()['records'][0]['id'] == record['id']


def test_question_record_can_store_score_and_teacher_correction():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    question = {'question_number': '1', 'question_text': '原题', 'is_wrong_question': True,
                'original_question_available': True}
    confirmed = client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [question]}).json()
    record_id = confirmed['question_records'][0]['id']
    response = client.patch(f"/api/question-records/{record_id}", json={
        'score_obtained': 3, 'score_total': 10, 'teacher_comment': '条件遗漏',
    })
    assert response.status_code == 200
    assert response.json()['score']['lost_points'] == 7
    assert response.json()['correction']['teacher_comment'] == '条件遗漏'


def test_question_record_can_be_mapped_to_public_knowledge_points():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    question = {'question_number': '1', 'question_text': '函数定义域与单调性',
                'is_wrong_question': True, 'original_question_available': True}
    confirmed = client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [question]}).json()
    record_id = confirmed['question_records'][0]['id']
    response = client.post(f"/api/question-records/{record_id}/knowledge-mapping")
    assert response.status_code == 200
    assert response.json()['knowledge_points'][0]['knowledge_id'] == 'high.math.function.domain'
    records = client.get(f"/api/exams/{exam['id']}/question-records").json()['records']
    assert records[0]['knowledge_point_ids'] == [
        'high.math.function.domain', 'high.math.function.monotonicity'
    ]


def test_analysis_run_generates_minimal_report_with_coverage_and_wrong_questions():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    question = {'question_number': '1', 'question_text': '函数定义域',
                'is_wrong_question': True, 'original_question_available': True}
    confirmed = client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [question]}).json()
    record_id = confirmed['question_records'][0]['id']
    client.post(f"/api/question-records/{record_id}/knowledge-mapping")
    run = client.post(f"/api/submissions/{submission['id']}/analysis-runs", json={'exam_ids': [exam['id']]}).json()
    started = client.post(f"/api/analysis-runs/{run['id']}/confirm-start")
    assert started.status_code == 202
    report = client.get(f"/api/analysis-runs/{run['id']}/report")
    assert report.status_code == 200
    data = report.json()
    assert data['wrong_question_count'] == 1
    assert data['knowledge_coverage'][0]['knowledge_id'] == 'high.math.function.domain'
    assert data['source_question_ids'] == [record_id]


def test_analysis_run_report_marks_missing_knowledge_mapping():
    submission = client.post('/api/submissions', json={
        'stage': 'high', 'subject': '数学', 'province': '广东',
        'school_grade': '高中二年级', 'academic_year_label': '25-26',
    }).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    question = {'question_number': '1', 'question_text': '未知题目',
                'is_wrong_question': True, 'original_question_available': True}
    confirmed = client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [question]}).json()
    run = client.post(f"/api/submissions/{submission['id']}/analysis-runs", json={'exam_ids': [exam['id']]}).json()
    client.post(f"/api/analysis-runs/{run['id']}/confirm-start")
    report = client.get(f"/api/analysis-runs/{run['id']}/report").json()
    assert report['wrong_question_count'] == 1
    assert report['unmapped_question_ids'] == [confirmed['question_records'][0]['id']]
