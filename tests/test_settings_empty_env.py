"""An empty environment value must not block config.json — `EXAM_DIAGNOSIS_X_API_KEY=` is how a
shell (or scripts/verify_server.py) says "off", and every reader already treats '' as unset."""
import os

from exam_diagnosis import settings


def test_empty_env_value_is_filled_by_config_and_not_reported_as_locked(monkeypatch, tmp_path):
    monkeypatch.setenv('EXAM_DIAGNOSIS_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('EXAM_DIAGNOSIS_SPLIT_API_KEY', '')
    try:
        assert settings.locked_fields({'split': {'api_key': 'x'}}, {}) == []
        settings.apply_config({'roles': {'split': {'api_key': 'from-config'}}})
        assert os.environ['EXAM_DIAGNOSIS_SPLIT_API_KEY'] == 'from-config'
        assert next(r for r in settings.describe()['roles'] if r['role'] == 'split')['api_key_source'] == 'config'
    finally:
        settings.apply_config({})
