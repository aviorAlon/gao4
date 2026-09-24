"""config.json-backed runtime settings: precedence against the environment, the settings API,
and the data-dir plumbing they live on."""
import json
import os

import pytest
from fastapi.testclient import TestClient

import exam_diagnosis.app as app_module
from exam_diagnosis import llm, paths, settings


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_DATA_DIR', str(tmp_path / 'data'))
    for name in list(settings._APPLIED):
        os.environ.pop(name, None)
    settings._APPLIED.clear()
    yield
    for name in list(settings._APPLIED):
        os.environ.pop(name, None)
    settings._APPLIED.clear()
    app_module._refresh_budget_from_env()


# --- paths ---------------------------------------------------------------------------------------


def test_data_dir_defaults_to_cwd_data_and_follows_the_env(monkeypatch, tmp_path):
    monkeypatch.delenv('EXAM_DIAGNOSIS_DATA_DIR', raising=False)
    monkeypatch.delenv('EXAM_DIAGNOSIS_DB', raising=False)
    assert paths.data_dir() == (paths.Path.cwd() / 'data').resolve()
    assert paths.database_path() == paths.data_dir() / 'exam_diagnosis.sqlite3'
    assert paths.uploads_dir() == paths.data_dir() / 'uploads'
    monkeypatch.setenv('EXAM_DIAGNOSIS_DATA_DIR', str(tmp_path / 'elsewhere'))
    assert paths.data_dir() == (tmp_path / 'elsewhere').resolve()
    assert paths.config_path() == (tmp_path / 'elsewhere' / 'config.json').resolve()
    monkeypatch.setenv('EXAM_DIAGNOSIS_DB', str(tmp_path / 'custom.sqlite3'))
    assert paths.database_path() == (tmp_path / 'custom.sqlite3').resolve()  # explicit DB path still wins


def test_uploads_land_under_the_configured_data_dir(tmp_path):
    client = TestClient(app_module.app)
    sub = client.post('/api/submissions', json={'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}).json()
    exam = client.post(f"/api/submissions/{sub['id']}/exams", json={'name': '月考'}).json()
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    asset = client.post(f"/api/upload-sessions/{session['id']}/assets", files={'file': ('a.txt', b'1. x', 'text/plain')}).json()
    stored = paths.Path(asset['storage_path'])
    assert stored.is_file() and (tmp_path / 'data' / 'uploads') in stored.parents
    assert client.delete(f"/api/assets/{asset['id']}").json()['deleted'] is True
    assert not stored.exists()  # safe_remove_asset_file recognises the configured root


# --- config file + precedence ---------------------------------------------------------------------


def test_save_and_load_round_trip_and_apply_fills_only_unset_env(monkeypatch):
    monkeypatch.delenv('EXAM_DIAGNOSIS_ANALYSIS_API_KEY', raising=False)
    monkeypatch.setenv('EXAM_DIAGNOSIS_REPORT_API_KEY', 'from-env')
    config = {'roles': {'analysis': {'api_key': 'from-config', 'model': 'm1'}, 'report': {'api_key': 'ignored'}}, 'budget': {'daily_token_budget': 5000}}
    written = settings.save_config(config)
    assert written == paths.config_path() and json.loads(written.read_text(encoding='utf-8')) == config
    assert settings.load_config() == config
    settings.apply_config(config)
    assert os.environ['EXAM_DIAGNOSIS_ANALYSIS_API_KEY'] == 'from-config'
    assert os.environ['EXAM_DIAGNOSIS_ANALYSIS_MODEL'] == 'm1'
    assert os.environ['EXAM_DIAGNOSIS_REPORT_API_KEY'] == 'from-env'  # environment wins
    assert os.environ['EXAM_DIAGNOSIS_MODEL_DAILY_TOKEN_BUDGET'] == '5000'
    # re-applying a smaller config removes what the previous apply added
    settings.apply_config({'roles': {'analysis': {'api_key': 'v2'}}})
    assert os.environ['EXAM_DIAGNOSIS_ANALYSIS_API_KEY'] == 'v2'
    assert 'EXAM_DIAGNOSIS_ANALYSIS_MODEL' not in os.environ
    assert 'EXAM_DIAGNOSIS_MODEL_DAILY_TOKEN_BUDGET' not in os.environ
    assert os.environ['EXAM_DIAGNOSIS_REPORT_API_KEY'] == 'from-env'


def test_load_config_tolerates_missing_or_corrupt_file():
    assert settings.load_config() == {}
    paths.config_path().parent.mkdir(parents=True, exist_ok=True)
    paths.config_path().write_text('{not json', encoding='utf-8')
    assert settings.load_config() == {}
    paths.config_path().write_text('[1, 2]', encoding='utf-8')
    assert settings.load_config() == {}


def test_describe_reports_source_and_never_the_key(monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_VERIFY_API_KEY', 'env-secret')
    settings.apply_config({'roles': {'analysis': {'api_key': 'config-secret', 'timeout': 45}, 'vision': {'api_key': 'v', 'base_url': 'http://127.0.0.1:1'}}, 'budget': {'max_calls_per_minute': 3}})
    described = settings.describe()
    assert 'env-secret' not in json.dumps(described) and 'config-secret' not in json.dumps(described)
    by_role = {r['role']: r for r in described['roles']}
    assert set(by_role) == set(settings.CONFIGURABLE_ROLES)
    assert by_role['analysis']['api_key_set'] and by_role['analysis']['api_key_source'] == 'config' and by_role['analysis']['configured'] and not by_role['analysis']['locked']
    assert by_role['analysis']['timeout'] == 45.0 and by_role['analysis']['timeout_source'] == 'config' and by_role['analysis']['model'] is None and by_role['analysis']['model_default'] == llm.BUILTIN_PROVIDER['reasoning_model']
    assert by_role['verify']['api_key_source'] == 'env' and by_role['verify']['locked']
    assert by_role['report']['api_key_set'] is False and by_role['report']['api_key_source'] is None
    assert by_role['vision']['configured'] is False  # base_url set but no model → not usable
    assert described['budget'] == {'daily_token_budget': None, 'daily_token_budget_source': None, 'max_calls_per_minute': 3, 'max_calls_per_minute_source': 'config'}


def test_merge_changes_semantics_and_validation():
    base = {'roles': {'analysis': {'api_key': 'k', 'model': 'm'}}, 'budget': {'daily_token_budget': 10}}
    merged = settings.merge_changes(base, {'roles': {'analysis': {'model': ''}, 'split': {'api_key': ' new ', 'timeout': '12'}}, 'budget': {'daily_token_budget': None, 'max_calls_per_minute': 4}})
    assert merged == {'roles': {'analysis': {'api_key': 'k'}, 'split': {'api_key': 'new', 'timeout': 12.0}}, 'budget': {'max_calls_per_minute': 4}}
    assert base['roles']['analysis']['model'] == 'm'  # input untouched
    assert settings.merge_changes(base, {'roles': {'analysis': {'api_key': ''}}})['roles'] == {} or 'analysis' not in settings.merge_changes(base, {'roles': {'analysis': {'api_key': '', 'model': ''}}})['roles']
    for bad in (
        {'roles': {'nope': {'api_key': 'x'}}},
        {'roles': {'analysis': {'colour': 'red'}}},
        {'roles': {'analysis': {'timeout': 'soon'}}},
        {'roles': {'analysis': {'timeout': -1}}},
        {'roles': {'analysis': {'model': 5}}},
        {'budget': {'weekly': 1}},
        {'budget': {'daily_token_budget': -5}},
        {'budget': {'daily_token_budget': True}},
    ):
        with pytest.raises(ValueError):
            settings.merge_changes(base, bad)


def test_locked_fields_only_flags_environment_owned_values(monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_GRADE_API_KEY', 'env')
    settings.apply_config({'roles': {'split': {'api_key': 'cfg'}}})
    assert settings.locked_fields({'grade': {'api_key': 'x'}, 'split': {'api_key': 'y'}, 'match': {'model': 'z'}}, {}) == ['EXAM_DIAGNOSIS_GRADE_API_KEY']
    monkeypatch.setenv('EXAM_DIAGNOSIS_MODEL_MAX_CALLS_PER_MINUTE', '9')
    assert settings.locked_fields({}, {'max_calls_per_minute': 1}) == ['EXAM_DIAGNOSIS_MODEL_MAX_CALLS_PER_MINUTE']


# --- provider-wide defaults ------------------------------------------------------------------------


def test_provider_defaults_from_config_flow_into_every_role():
    settings.apply_config({'provider': {'base_url': 'https://cfg.example/v1', 'reasoning_model': 'cfg-big'}})
    described = settings.describe()
    provider = described['provider']
    assert provider['base_url'] == 'https://cfg.example/v1' and provider['base_url_source'] == 'config'
    assert provider['fast_model'] is None and provider['fast_model_source'] is None and provider['fast_model_default'] == llm.BUILTIN_PROVIDER['fast_model']
    by_role = {r['role']: r for r in described['roles']}
    assert by_role['analysis']['base_url_default'] == 'https://cfg.example/v1' and by_role['analysis']['model_default'] == 'cfg-big'
    assert by_role['split']['base_url_default'] == 'https://cfg.example/v1' and by_role['split']['model_default'] == llm.BUILTIN_PROVIDER['fast_model']
    assert by_role['vision']['base_url_default'] is None  # the vision parser has no provider fallback: all three of its vars are required
    assert settings.provider_status()['reasoning_model_source'] == 'config' and settings.provider_status()['fast_model_source'] == 'builtin'
    # re-applying without the section removes it again
    settings.apply_config({})
    assert 'EXAM_DIAGNOSIS_DEFAULT_BASE_URL' not in os.environ
    assert settings.describe()['roles'][0]['base_url_default'] == llm.BUILTIN_PROVIDER['base_url']


def test_environment_owns_provider_defaults_over_config(monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_DEFAULT_BASE_URL', 'https://env.example/v1')
    settings.apply_config({'provider': {'base_url': 'https://cfg.example/v1', 'fast_model': 'cfg-small'}})
    assert os.environ['EXAM_DIAGNOSIS_DEFAULT_BASE_URL'] == 'https://env.example/v1'
    assert os.environ['EXAM_DIAGNOSIS_DEFAULT_FAST_MODEL'] == 'cfg-small'
    assert settings.locked_fields({}, {}, {'base_url': 'x', 'fast_model': 'y'}) == ['EXAM_DIAGNOSIS_DEFAULT_BASE_URL']
    assert settings.provider_status()['base_url_source'] == 'env'


def test_merge_changes_handles_the_provider_section():
    merged = settings.merge_changes({}, {'provider': {'base_url': ' https://p.example/v1 ', 'fast_model': 'f'}})
    assert merged == {'provider': {'base_url': 'https://p.example/v1', 'fast_model': 'f'}}
    cleared = settings.merge_changes(merged, {'provider': {'fast_model': ''}})
    assert cleared == {'provider': {'base_url': 'https://p.example/v1'}}
    assert settings.merge_changes(cleared, {'provider': {'base_url': None}}) == {}
    for bad in ({'provider': {'colour': 'x'}}, {'provider': {'base_url': 5}}, {'provider': 'nope'}):
        with pytest.raises(ValueError):
            settings.merge_changes({}, bad)


# --- API -------------------------------------------------------------------------------------------


def test_settings_api_saves_provider_defaults_and_reports_them_in_status():
    client = TestClient(app_module.app)
    response = client.put('/api/settings', json={'provider': {'base_url': 'https://ui.example/v1', 'reasoning_model': 'ui-big'}, 'roles': {'grade': {'api_key': 'sk-ui'}}})
    assert response.status_code == 200, response.json()
    saved = response.json()
    assert saved['provider']['base_url'] == 'https://ui.example/v1' and saved['provider']['base_url_source'] == 'config'
    grade = next(r for r in saved['roles'] if r['role'] == 'grade')
    assert grade['base_url_default'] == 'https://ui.example/v1' and grade['model_default'] == 'ui-big' and grade['configured']
    assert app_module.client_from_env('grade').base_url == 'https://ui.example/v1' and app_module.client_from_env('grade').model == 'ui-big'
    assert json.loads(paths.config_path().read_text(encoding='utf-8'))['provider'] == {'base_url': 'https://ui.example/v1', 'reasoning_model': 'ui-big'}
    status = client.get('/api/system/status').json()
    assert status['model_provider']['base_url'] == 'https://ui.example/v1' and status['model_provider']['base_url_source'] == 'config'
    assert status['model_provider']['fast_model_source'] == 'builtin'
    assert next(r for r in status['model_roles'] if r['role'] == 'grade')['model'] == 'ui-big'
    bad = client.put('/api/settings', json={'provider': {'nope': 'x'}})
    assert bad.status_code == 422 and bad.json()['error'] == 'invalid_settings'


def test_settings_api_refuses_env_owned_provider_defaults(monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_DEFAULT_REASONING_MODEL', 'env-big')
    client = TestClient(app_module.app)
    locked = client.put('/api/settings', json={'provider': {'reasoning_model': 'other'}})
    assert locked.status_code == 409 and locked.json() == {'error': 'locked_by_environment', 'fields': ['EXAM_DIAGNOSIS_DEFAULT_REASONING_MODEL']}


def test_settings_api_saves_applies_and_reports(monkeypatch):
    monkeypatch.delenv('EXAM_DIAGNOSIS_ANALYSIS_API_KEY', raising=False)
    client = TestClient(app_module.app)
    before = client.get('/api/settings').json()
    assert before['writable'] is True and before['auth_required'] is False
    assert all(not r['api_key_set'] for r in before['roles'])
    response = client.put('/api/settings', json={'roles': {'analysis': {'api_key': 'sk-from-ui', 'model': 'custom'}}, 'budget': {'daily_token_budget': 1234}})
    assert response.status_code == 200, response.json()
    assert 'sk-from-ui' not in response.text
    analysis = next(r for r in response.json()['roles'] if r['role'] == 'analysis')
    assert analysis['api_key_set'] and analysis['api_key_source'] == 'config' and analysis['model'] == 'custom'
    assert response.json()['budget']['daily_token_budget'] == 1234
    # took effect for the code that actually uses it
    assert app_module.client_from_env('analysis').model == 'custom'
    assert app_module.MODEL_DAILY_TOKEN_BUDGET == 1234
    assert json.loads(paths.config_path().read_text(encoding='utf-8'))['roles']['analysis']['api_key'] == 'sk-from-ui'
    status = client.get('/api/system/status').json()
    assert next(r for r in status['model_roles'] if r['role'] == 'analysis')['configured'] is True
    # clearing
    cleared = client.put('/api/settings', json={'roles': {'analysis': {'api_key': ''}}, 'budget': {'daily_token_budget': None}}).json()
    assert next(r for r in cleared['roles'] if r['role'] == 'analysis')['api_key_set'] is False
    assert app_module.client_from_env('analysis') is None and app_module.MODEL_DAILY_TOKEN_BUDGET is None


def test_settings_api_refuses_env_locked_fields_and_bad_input(monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_REPORT_API_KEY', 'env-owned')
    client = TestClient(app_module.app)
    locked = client.put('/api/settings', json={'roles': {'report': {'api_key': 'new'}}})
    assert locked.status_code == 409 and locked.json() == {'error': 'locked_by_environment', 'fields': ['EXAM_DIAGNOSIS_REPORT_API_KEY']}
    bad = client.put('/api/settings', json={'roles': {'analysis': {'timeout': 'later'}}})
    assert bad.status_code == 422 and bad.json()['error'] == 'invalid_settings'
    assert not paths.config_path().exists()  # nothing written on refusal


def test_settings_are_read_only_in_multi_user_mode(monkeypatch):
    monkeypatch.setattr(app_module, 'AUTH_REQUIRED', True)
    client = TestClient(app_module.app)
    token = client.post('/api/auth/register', json={'username': 'alice', 'password': 'correct horse battery'}).json()['token']
    headers = {'Authorization': f'Bearer {token}'}
    assert client.get('/api/settings', headers=headers).json()['writable'] is False
    assert client.put('/api/settings', json={'roles': {'analysis': {'api_key': 'x'}}}, headers=headers).status_code == 403
    assert client.get('/api/health').status_code == 200  # health stays public


def test_health_is_public_and_names_the_data_dir(tmp_path):
    client = TestClient(app_module.app)
    body = client.get('/api/health').json()
    assert body['status'] == 'ok' and body['data_dir'] == str((tmp_path / 'data').resolve())
