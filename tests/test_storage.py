import json
import os
import subprocess
import sys


def test_nested_mutation_is_visible_to_new_process(tmp_path):
    db = tmp_path / 'nested.sqlite3'
    env = os.environ.copy()
    env['PYTHONPATH'] = os.getcwd()
    env['EXAM_DIAGNOSIS_DB'] = str(db)
    writer = """
from exam_diagnosis.storage import SQLiteMapping
items = SQLiteMapping('items')
items['x'] = {'status': 'new', 'nested': {'count': 1}}
items['x']['nested']['count'] = 2
items.flush()
"""
    reader = """
from exam_diagnosis.storage import SQLiteMapping
print(SQLiteMapping('items')['x']['nested']['count'])
"""
    subprocess.run([sys.executable, '-c', writer], env=env, check=True, capture_output=True, text=True)
    result = subprocess.run([sys.executable, '-c', reader], env=env, check=True, capture_output=True, text=True)
    assert result.stdout.strip() == '2'


def test_api_nested_update_is_visible_after_process_restart(tmp_path, monkeypatch):
    db = tmp_path / 'api-restart.sqlite3'
    monkeypatch.setenv('EXAM_DIAGNOSIS_DB', str(db))
    import importlib
    import exam_diagnosis.app as module
    importlib.reload(module)
    from fastapi.testclient import TestClient
    client = TestClient(module.app)
    submission = client.post('/api/submissions', json={'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    confirmed = client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [{'question_number': '1', 'question_text': '函数定义域', 'is_wrong_question': True, 'original_question_available': True}]}).json()
    record_id = confirmed['question_records'][0]['id']
    client.patch(f'/api/question-records/{record_id}', json={'teacher_comment': '条件遗漏'})
    reader = """
from exam_diagnosis.storage import SQLiteMapping
print(SQLiteMapping('question_records')[__import__('sys').argv[1]]['correction']['teacher_comment'])
"""
    env = os.environ.copy(); env['PYTHONPATH'] = os.getcwd(); env['EXAM_DIAGNOSIS_DB'] = str(db)
    result = subprocess.run([sys.executable, '-c', reader, record_id], env=env, check=True, capture_output=True, text=True)
    assert result.stdout.strip() == '条件遗漏'
