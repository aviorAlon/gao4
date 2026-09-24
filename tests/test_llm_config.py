import pytest

from exam_diagnosis.llm import BUILTIN_PROVIDER, PROVIDER_ENV, ROLE_DEFAULTS, ROLE_TIERS, client_from_env, provider_defaults, role_status


def test_role_not_configured_returns_none(monkeypatch):
    monkeypatch.delenv('EXAM_DIAGNOSIS_ANALYSIS_API_KEY', raising=False)
    assert client_from_env('analysis') is None


def test_api_key_alone_activates_role_with_builtin_provider_defaults(monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_ANALYSIS_API_KEY', 'sk-test')
    monkeypatch.delenv('EXAM_DIAGNOSIS_ANALYSIS_BASE_URL', raising=False)
    monkeypatch.delenv('EXAM_DIAGNOSIS_ANALYSIS_MODEL', raising=False)
    client = client_from_env('analysis')
    assert client is not None
    assert client.base_url == ROLE_DEFAULTS['analysis']['base_url']
    assert client.model == ROLE_DEFAULTS['analysis']['model'] == BUILTIN_PROVIDER['reasoning_model']


def test_report_role_defaults_to_cheaper_model(monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_REPORT_API_KEY', 'sk-test')
    monkeypatch.delenv('EXAM_DIAGNOSIS_REPORT_MODEL', raising=False)
    client = client_from_env('report')
    assert client.model == BUILTIN_PROVIDER['fast_model']


def test_base_url_and_model_are_overridable(monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_ANALYSIS_API_KEY', 'sk-test')
    monkeypatch.setenv('EXAM_DIAGNOSIS_ANALYSIS_BASE_URL', 'https://example.com/v1')
    monkeypatch.setenv('EXAM_DIAGNOSIS_ANALYSIS_MODEL', 'some-other-model')
    client = client_from_env('analysis')
    assert client.base_url == 'https://example.com/v1'
    assert client.model == 'some-other-model'


def test_unknown_role_rejected():
    with pytest.raises(ValueError):
        client_from_env('translation')


def test_grade_role_defaults_to_the_reasoning_tier_model(monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_GRADE_API_KEY', 'sk-test')
    monkeypatch.delenv('EXAM_DIAGNOSIS_GRADE_MODEL', raising=False)
    assert client_from_env('grade').model == BUILTIN_PROVIDER['reasoning_model']


def test_no_role_has_a_real_credential_by_default():
    # Regression guard for a real incident: a genuine .env.local sitting in the repo (created
    # while working through this project's own model setup) got re-read by app.py's module
    # reload during test_app.py's restart test, silently activating real model calls inside
    # report-generation tests that expected client_from_env to return None. conftest.py now scrubs
    # these before the session starts and app.py refuses to load .env.local under pytest at all —
    # this asserts the guarantee that matters: no role is ever live unless a test explicitly
    # (via its own monkeypatch.setenv, as the other tests in this file do) turns it on.
    for role in ('analysis', 'report', 'split', 'verify', 'grade', 'match'):
        assert client_from_env(role) is None


def test_match_role_defaults_to_cheaper_model(monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_MATCH_API_KEY', 'sk-test')
    monkeypatch.delenv('EXAM_DIAGNOSIS_MATCH_MODEL', raising=False)
    assert client_from_env('match').model == BUILTIN_PROVIDER['fast_model']


def test_split_role_defaults_to_cheaper_model(monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_SPLIT_API_KEY', 'sk-test')
    monkeypatch.delenv('EXAM_DIAGNOSIS_SPLIT_MODEL', raising=False)
    client = client_from_env('split')
    assert client.model == BUILTIN_PROVIDER['fast_model']


def test_verify_role_defaults_to_the_reasoning_tier_model(monkeypatch):
    # Generating a correct worked answer needs the same reasoning tier as root-cause diagnosis —
    # unlike split/report, this one shouldn't default to the cheaper model.
    monkeypatch.setenv('EXAM_DIAGNOSIS_VERIFY_API_KEY', 'sk-test')
    monkeypatch.delenv('EXAM_DIAGNOSIS_VERIFY_MODEL', raising=False)
    client = client_from_env('verify')
    assert client.model == BUILTIN_PROVIDER['reasoning_model']


def test_provider_wide_defaults_apply_to_every_role_by_tier(monkeypatch):
    # One place to point the whole application at a different OpenAI-compatible provider: no role
    # needs its own BASE_URL/MODEL, and each still lands on the right tier.
    monkeypatch.setenv('EXAM_DIAGNOSIS_DEFAULT_BASE_URL', 'https://models.example.com/v1')
    monkeypatch.setenv('EXAM_DIAGNOSIS_DEFAULT_REASONING_MODEL', 'big-model')
    monkeypatch.setenv('EXAM_DIAGNOSIS_DEFAULT_FAST_MODEL', 'small-model')
    for role in ROLE_DEFAULTS:
        monkeypatch.setenv(f'EXAM_DIAGNOSIS_{role.upper()}_API_KEY', 'sk-test')
        monkeypatch.delenv(f'EXAM_DIAGNOSIS_{role.upper()}_MODEL', raising=False)
        monkeypatch.delenv(f'EXAM_DIAGNOSIS_{role.upper()}_BASE_URL', raising=False)
    for role, tier in ROLE_TIERS.items():
        client = client_from_env(role)
        assert client.base_url == 'https://models.example.com/v1', role
        assert client.model == ('big-model' if tier == 'reasoning' else 'small-model'), role
        assert role_status(role)['model'] == client.model
    assert set(ROLE_TIERS) == set(ROLE_DEFAULTS)


def test_per_role_setting_beats_the_provider_wide_default(monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_DEFAULT_FAST_MODEL', 'small-model')
    monkeypatch.setenv('EXAM_DIAGNOSIS_SPLIT_API_KEY', 'sk-test')
    monkeypatch.setenv('EXAM_DIAGNOSIS_SPLIT_MODEL', 'split-special')
    monkeypatch.setenv('EXAM_DIAGNOSIS_REPORT_API_KEY', 'sk-test')
    monkeypatch.delenv('EXAM_DIAGNOSIS_REPORT_MODEL', raising=False)
    assert client_from_env('split').model == 'split-special'
    assert client_from_env('report').model == 'small-model'


def test_provider_defaults_report_where_each_value_came_from(monkeypatch):
    for name in PROVIDER_ENV.values():
        monkeypatch.delenv(name, raising=False)
    builtin = provider_defaults()
    assert builtin['base_url'] == BUILTIN_PROVIDER['base_url'] and builtin['base_url_source'] == 'builtin'
    monkeypatch.setenv('EXAM_DIAGNOSIS_DEFAULT_BASE_URL', 'https://x.example/v1')
    overridden = provider_defaults()
    assert overridden['base_url'] == 'https://x.example/v1' and overridden['base_url_source'] == 'env'
    assert overridden['fast_model'] == BUILTIN_PROVIDER['fast_model'] and overridden['fast_model_source'] == 'builtin'
    assert overridden['base_url_builtin'] == BUILTIN_PROVIDER['base_url']
