"""Tests for the budget/rate-limit gate in front of the model roles (app.guarded_client_from_env).

Uses monkeypatch.setattr on the module-level MODEL_DAILY_TOKEN_BUDGET / MODEL_MAX_CALLS_PER_MINUTE
constants rather than importlib.reload() + env vars — reload() re-executes the whole module,
which recreates every SQLiteMapping from scratch and would silently stop pointing at this test
session's isolated database (see conftest.py's _use_isolated_test_database), reopening the exact
"tests touch the real dev database" risk that was just fixed. Patching the two already-parsed
constants directly is both safer and a more precise way to exercise just this one behavior.
"""
import exam_diagnosis.app as app_module
from fastapi.testclient import TestClient


def test_no_budget_configured_means_unlimited(monkeypatch):
    monkeypatch.setattr(app_module, 'MODEL_DAILY_TOKEN_BUDGET', None)
    monkeypatch.setattr(app_module, 'MODEL_MAX_CALLS_PER_MINUTE', None)
    assert app_module._budget_block_reason('local') is None


def test_daily_token_budget_blocks_once_exceeded(monkeypatch):
    monkeypatch.setattr(app_module, 'MODEL_DAILY_TOKEN_BUDGET', 100)
    monkeypatch.setattr(app_module, 'MODEL_MAX_CALLS_PER_MINUTE', None)
    app_module.record_model_call('local')({
        'role': 'analysis', 'model': 'm', 'base_url': 'u', 'latency_ms': 1, 'attempts': 1,
        'success': True, 'error': None, 'prompt_tokens': 60, 'completion_tokens': 60, 'total_tokens': 120,
    })
    assert app_module._budget_block_reason('local') == 'daily_token_budget_exceeded'
    # a different user's spend must not count against this one
    assert app_module._budget_block_reason('someone-else') is None


def test_failed_calls_do_not_count_toward_the_token_budget(monkeypatch):
    monkeypatch.setattr(app_module, 'MODEL_DAILY_TOKEN_BUDGET', 100)
    monkeypatch.setattr(app_module, 'MODEL_MAX_CALLS_PER_MINUTE', None)
    app_module.record_model_call('local')({
        'role': 'analysis', 'model': 'm', 'base_url': 'u', 'latency_ms': 1, 'attempts': 2,
        'success': False, 'error': 'boom', 'prompt_tokens': None, 'completion_tokens': None, 'total_tokens': None,
    })
    assert app_module._budget_block_reason('local') is None


def test_rate_limit_blocks_once_exceeded(monkeypatch):
    monkeypatch.setattr(app_module, 'MODEL_DAILY_TOKEN_BUDGET', None)
    monkeypatch.setattr(app_module, 'MODEL_MAX_CALLS_PER_MINUTE', 2)
    for _ in range(2):
        app_module.record_model_call('local')({
            'role': 'split', 'model': 'm', 'base_url': 'u', 'latency_ms': 1, 'attempts': 1,
            'success': True, 'error': None, 'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2,
        })
    assert app_module._budget_block_reason('local') == 'rate_limited'


def test_guarded_client_from_env_returns_none_and_records_a_blocked_call_without_a_real_client(monkeypatch):
    monkeypatch.setattr(app_module, 'MODEL_DAILY_TOKEN_BUDGET', 1)
    monkeypatch.setattr(app_module, 'MODEL_MAX_CALLS_PER_MINUTE', None)
    monkeypatch.setenv('EXAM_DIAGNOSIS_ANALYSIS_API_KEY', 'sk-test')
    app_module.record_model_call('local')({
        'role': 'analysis', 'model': 'm', 'base_url': 'u', 'latency_ms': 1, 'attempts': 1,
        'success': True, 'error': None, 'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2,
    })
    before = len(app_module.MODEL_CALLS)
    client = app_module.guarded_client_from_env('analysis', 'local')
    assert client is None
    assert len(app_module.MODEL_CALLS) == before + 1
    blocked = sorted(app_module.MODEL_CALLS.values(), key=lambda c: c['created_at'])[-1]
    assert blocked['blocked'] is True
    assert blocked['error'] == 'daily_token_budget_exceeded'


def test_model_calls_endpoint_reports_budget_status(monkeypatch):
    monkeypatch.setattr(app_module, 'MODEL_DAILY_TOKEN_BUDGET', 1000)
    monkeypatch.setattr(app_module, 'MODEL_MAX_CALLS_PER_MINUTE', 50)
    app_module.record_model_call('dave')({
        'role': 'report', 'model': 'm', 'base_url': 'u', 'latency_ms': 1, 'attempts': 1,
        'success': True, 'error': None, 'prompt_tokens': 100, 'completion_tokens': 100, 'total_tokens': 200,
    })
    client = TestClient(app_module.app)
    budget = client.get('/api/model-calls', headers={'X-User-Id': 'dave'}).json()['budget']
    assert budget['daily_token_budget'] == 1000
    assert budget['tokens_used_today'] == 200
    assert budget['daily_tokens_remaining'] == 800
    assert budget['max_calls_per_minute'] == 50
    assert budget['calls_in_last_minute'] == 1
