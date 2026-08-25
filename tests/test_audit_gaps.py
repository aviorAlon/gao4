from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def new_exam(client, name='盘点'):
    submission = client.post('/api/submissions', json={'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': name}).json()
    return submission, exam


def test_identical_content_is_rejected_whatever_the_filename_is():
    client = TestClient(app)
    _, exam = new_exam(client)
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    url = f"/api/upload-sessions/{session['id']}/assets"
    first = client.post(url, files={'file': ('page-1.jpg', b'same-bytes', 'image/jpeg')})
    assert first.status_code == 201
    repeat = client.post(url, files={'file': ('page-2.jpg', b'same-bytes', 'image/jpeg')})
    assert repeat.status_code == 409
    body = repeat.json()
    assert body['error'] == 'duplicate_asset'
    assert body['duplicate_asset_id'] == first.json()['id'] and body['duplicate_filename'] == 'page-1.jpg'
    assert body['sha256'] == first.json()['sha256']


def test_distinct_pages_with_distinct_content_are_all_accepted():
    client = TestClient(app)
    _, exam = new_exam(client)
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    for name in ('page-1.jpg', 'page-2.jpg', 'page-3.jpg'):
        response = client.post(f"/api/upload-sessions/{session['id']}/assets", files={'file': (name, name.encode(), 'image/jpeg')})
        assert response.status_code == 201
    assert len(client.get(f"/api/exams/{exam['id']}/assets").json()['assets']) == 3


def test_the_same_bytes_in_a_different_exam_are_not_a_duplicate():
    client = TestClient(app)
    _, first_exam = new_exam(client, '甲')
    _, second_exam = new_exam(client, '乙')
    for exam in (first_exam, second_exam):
        session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
        response = client.post(f"/api/upload-sessions/{session['id']}/assets", files={'file': ('paper.jpg', b'shared-bytes', 'image/jpeg')})
        assert response.status_code == 201


def confirm_with_edit(client):
    _, exam = new_exam(client, '溯源')
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    asset = client.post(f"/api/upload-sessions/{session['id']}/assets", files={'file': ('paper.jpg', b'img', 'image/jpeg')}).json()
    client.patch(f"/api/exams/{exam['id']}/parse-draft", json={'questions': [{'question_number': '17', 'question_text': '机器识别题干', 'is_wrong_question': True, 'original_question_available': True}]})
    draft = client.patch(f"/api/exams/{exam['id']}/parse-draft/questions/0", json={'question_text': '用户修正题干'}).json()
    client.post(f"/api/exams/{exam['id']}/questions/{draft['questions'][0]['id']}/link-original", json={'asset_id': asset['id']})
    return exam, client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': []}).json()


def test_provenance_chain_survives_confirmation_into_question_records():
    client = TestClient(app)
    _, confirmed = confirm_with_edit(client)
    chain = confirmed['question_records'][0]['provenance']['question_text']
    assert chain['machine_value'] == '机器识别题干'
    assert chain['user_value'] == '用户修正题干'
    assert chain['confirmed_value'] == '用户修正题干'


def test_untouched_field_still_records_machine_and_confirmed_value():
    client = TestClient(app)
    _, confirmed = confirm_with_edit(client)
    chain = confirmed['question_records'][0]['provenance']['question_number']
    assert chain['machine_value'] == '17' and chain['user_value'] is None and chain['confirmed_value'] == '17'


def test_exported_personal_data_carries_the_provenance_chain():
    client = TestClient(app)
    submission = client.post('/api/submissions', json={'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '导出'}).json()
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [{'question_number': '1', 'question_text': '函数定义域', 'is_wrong_question': True, 'original_question_available': True, 'student_answer': '错答'}]})
    record = client.get(f"/api/submissions/{submission['id']}/export").json()['question_records'][0]
    assert record['provenance']['student_answer']['confirmed_value'] == '错答'


def test_completeness_names_the_missing_answer_key_student_answer_and_correction():
    client = TestClient(app)
    _, exam = new_exam(client, '完整度')
    confirmed = client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [{'question_number': '1', 'question_text': '函数定义域', 'is_wrong_question': True, 'original_question_available': True}]}).json()
    completeness = confirmed['completeness']
    assert completeness['has_wrong_questions'] is True
    assert completeness['has_answer_key'] is False and completeness['has_student_answers'] is False and completeness['has_corrections'] is False
    assert completeness['missing'] == ['has_answer_key', 'has_corrections', 'has_original_sources', 'has_student_answers']


def test_completeness_is_empty_once_answer_key_correction_and_answers_are_present():
    client = TestClient(app)
    _, exam = new_exam(client, '齐全')
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    paper = client.post(f"/api/upload-sessions/{session['id']}/assets", files={'file': ('paper.jpg', b'paper', 'image/jpeg')}).json()
    key = client.post(f"/api/upload-sessions/{session['id']}/assets", files={'file': ('answer.jpg', b'answer', 'image/jpeg')}).json()
    mark = client.post(f"/api/upload-sessions/{session['id']}/assets", files={'file': ('correction.jpg', b'correction', 'image/jpeg')}).json()
    client.patch(f"/api/assets/{key['id']}", json={'material_type': 'answer_key'})
    client.patch(f"/api/assets/{mark['id']}", json={'material_type': 'correction'})
    confirmed = client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [{
        'question_number': '1', 'question_text': '函数定义域', 'is_wrong_question': True,
        'original_question_available': True, 'student_answer': '错答',
        'original_source': {'asset_id': paper['id'], 'page_id': None, 'filename': 'paper.jpg'},
    }]}).json()
    assert confirmed['completeness']['missing'] == []


def test_single_exam_can_be_fetched_by_id():
    client = TestClient(app)
    _, exam = new_exam(client, '单个考试')
    fetched = client.get(f"/api/exams/{exam['id']}")
    assert fetched.status_code == 200 and fetched.json()['id'] == exam['id'] and fetched.json()['name'] == '单个考试'
    assert client.get('/api/exams/does-not-exist').status_code == 404
