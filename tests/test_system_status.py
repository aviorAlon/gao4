"""GET /api/system/status: which model roles are live, with what, plus auth/task/budget facts."""
from fastapi.testclient import TestClient

import exam_diagnosis.app as app_module
from exam_diagnosis.llm import BUILTIN_PROVIDER, ROLE_DEFAULTS, role_status


def test_role_status_reports_configuration_without_the_key(monkeypatch):
    monkeypatch.delenv('EXAM_DIAGNOSIS_ANALYSIS_API_KEY', raising=False)
    off = role_status('analysis')
    assert off['configured'] is False and off['model'] == BUILTIN_PROVIDER['reasoning_model'] and off['purpose'] == '错因诊断'
    monkeypatch.setenv('EXAM_DIAGNOSIS_ANALYSIS_API_KEY', 'sk-secret-value')
    monkeypatch.setenv('EXAM_DIAGNOSIS_ANALYSIS_MODEL', 'custom-model')
    on = role_status('analysis')
    assert on['configured'] is True and on['model'] == 'custom-model'
    assert 'sk-secret-value' not in str(on)


def test_status_endpoint_lists_every_role_and_never_leaks_secrets(monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_MATCH_API_KEY', 'sk-match-secret')
    monkeypatch.setenv('EXAM_DIAGNOSIS_VISION_BASE_URL', 'http://127.0.0.1:1')
    monkeypatch.setenv('EXAM_DIAGNOSIS_VISION_API_KEY', 'sk-vision-secret')
    monkeypatch.setenv('EXAM_DIAGNOSIS_VISION_MODEL', 'qwen-vl')
    client = TestClient(app_module.app)
    response = client.get('/api/system/status')
    assert response.status_code == 200
    body = response.json()
    assert 'sk-match-secret' not in response.text and 'sk-vision-secret' not in response.text
    by_role = {item['role']: item for item in body['model_roles']}
    assert set(by_role) == set(ROLE_DEFAULTS) | {'vision'}
    assert by_role['match']['configured'] is True and by_role['analysis']['configured'] is False
    assert by_role['vision'] == {'role': 'vision', 'purpose': '图片 / 扫描 PDF 视觉解析', 'configured': True, 'base_url': 'http://127.0.0.1:1', 'model': 'qwen-vl', 'timeout': 60.0, 'env_prefix': 'EXAM_DIAGNOSIS_VISION_'}
    assert body['auth'] == {'required': False, 'session_ttl_hours': app_module.SESSION_TTL_HOURS}
    assert body['tasks'] == {'workers': 1, 'active': 0, 'failed': 0}
    assert body['budget'] == {'daily_token_budget': None, 'max_calls_per_minute': None, 'block_reason': None}
    assert body['application']['name'] == 'Exam Diagnosis'
    assert body['features'] == {'qr_code': True}
    assert body['model_provider']['base_url'] == BUILTIN_PROVIDER['base_url'] and body['model_provider']['base_url_source'] == 'builtin'
    assert body['storage']['database'].endswith('.sqlite3')
    assert any(p['stage'] == 'high' and p['subject'] == '数学' for p in body['knowledge']['partitions'])


def test_status_counts_this_users_tasks_only():
    client = TestClient(app_module.app)
    app_module.TASKS['t1'] = {'id': 't1', 'kind': 'x', 'status': 'failed', 'user_id': 'local', 'created_at': 'x'}
    app_module.TASKS['t2'] = {'id': 't2', 'kind': 'x', 'status': 'running', 'user_id': 'someone-else', 'created_at': 'x'}
    assert client.get('/api/system/status').json()['tasks'] == {'workers': 1, 'active': 0, 'failed': 1}
    other = TestClient(app_module.app, headers={'X-User-Id': 'someone-else'})
    assert other.get('/api/system/status').json()['tasks'] == {'workers': 1, 'active': 1, 'failed': 0}


def test_status_reports_a_missing_qr_library(monkeypatch):
    from exam_diagnosis import mobile
    monkeypatch.setattr(mobile, 'qr_svg', lambda text: None)
    assert TestClient(app_module.app).get('/api/system/status').json()['features']['qr_code'] is False
