import pytest

from exam_diagnosis.llm import ROLE_DEFAULTS, client_from_env


def test_role_not_configured_returns_none(monkeypatch):
    monkeypatch.delenv('EXAM_DIAGNOSIS_ANALYSIS_API_KEY', raising=False)
    assert client_from_env('analysis') is None


def test_api_key_alone_activates_role_with_deepseek_defaults(monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_ANALYSIS_API_KEY', 'sk-test')
    monkeypatch.delenv('EXAM_DIAGNOSIS_ANALYSIS_BASE_URL', raising=False)
    monkeypatch.delenv('EXAM_DIAGNOSIS_ANALYSIS_MODEL', raising=False)
    client = client_from_env('analysis')
    assert client is not None
    assert client.base_url == ROLE_DEFAULTS['analysis']['base_url']
    assert client.model == ROLE_DEFAULTS['analysis']['model'] == 'deepseek-v4-pro'


def test_report_role_defaults_to_cheaper_model(monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_REPORT_API_KEY', 'sk-test')
    monkeypatch.delenv('EXAM_DIAGNOSIS_REPORT_MODEL', raising=False)
    client = client_from_env('report')
    assert client.model == 'deepseek-v4-flash'


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


def test_split_role_defaults_to_cheaper_model(monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_SPLIT_API_KEY', 'sk-test')
    monkeypatch.delenv('EXAM_DIAGNOSIS_SPLIT_MODEL', raising=False)
    client = client_from_env('split')
    assert client.model == 'deepseek-v4-flash'


def test_verify_role_defaults_to_the_reasoning_tier_model(monkeypatch):
    # Generating a correct worked answer needs the same reasoning tier as root-cause diagnosis —
    # unlike split/report, this one shouldn't default to the cheaper model.
    monkeypatch.setenv('EXAM_DIAGNOSIS_VERIFY_API_KEY', 'sk-test')
    monkeypatch.delenv('EXAM_DIAGNOSIS_VERIFY_MODEL', raising=False)
    client = client_from_env('verify')
    assert client.model == 'deepseek-v4-pro'
