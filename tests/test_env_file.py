import os

from exam_diagnosis.env_file import load_env_file


def test_loads_key_value_pairs(tmp_path, monkeypatch):
    monkeypatch.delenv('EXAM_DIAGNOSIS_TEST_KEY', raising=False)
    env_file = tmp_path / '.env.local'
    env_file.write_text('EXAM_DIAGNOSIS_TEST_KEY=sk-abc123\n', encoding='utf-8')
    load_env_file(env_file)
    assert os.environ['EXAM_DIAGNOSIS_TEST_KEY'] == 'sk-abc123'


def test_skips_blank_lines_and_comments(tmp_path, monkeypatch):
    monkeypatch.delenv('EXAM_DIAGNOSIS_TEST_KEY', raising=False)
    env_file = tmp_path / '.env.local'
    env_file.write_text('# a comment\n\nEXAM_DIAGNOSIS_TEST_KEY=sk-abc123\n# EXAM_DIAGNOSIS_OTHER=nope\n', encoding='utf-8')
    load_env_file(env_file)
    assert os.environ['EXAM_DIAGNOSIS_TEST_KEY'] == 'sk-abc123'
    assert 'EXAM_DIAGNOSIS_OTHER' not in os.environ


def test_strips_surrounding_quotes(tmp_path, monkeypatch):
    monkeypatch.delenv('EXAM_DIAGNOSIS_TEST_KEY', raising=False)
    env_file = tmp_path / '.env.local'
    env_file.write_text('EXAM_DIAGNOSIS_TEST_KEY="sk-abc123"\n', encoding='utf-8')
    load_env_file(env_file)
    assert os.environ['EXAM_DIAGNOSIS_TEST_KEY'] == 'sk-abc123'


def test_real_environment_variable_wins_over_file(tmp_path, monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_TEST_KEY', 'sk-from-real-env')
    env_file = tmp_path / '.env.local'
    env_file.write_text('EXAM_DIAGNOSIS_TEST_KEY=sk-from-file\n', encoding='utf-8')
    load_env_file(env_file)
    assert os.environ['EXAM_DIAGNOSIS_TEST_KEY'] == 'sk-from-real-env'


def test_missing_file_is_a_silent_no_op(tmp_path):
    load_env_file(tmp_path / 'does-not-exist.local')  # must not raise
