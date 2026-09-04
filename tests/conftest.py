import os
from pathlib import Path

import pytest
import yaml

SHIPPED_KNOWLEDGE_PATH = Path(__file__).resolve().parents[1] / 'data' / 'knowledge' / 'high_math_guangdong_v1.yaml'
SHIPPED_KNOWLEDGE_VERSION = yaml.safe_load(SHIPPED_KNOWLEDGE_PATH.read_text(encoding='utf-8'))['version']

MODEL_ROLES = ('ANALYSIS', 'REPORT', 'SPLIT', 'VERIFY', 'GRADE')


@pytest.fixture(autouse=True, scope='session')
def _never_use_real_model_credentials():
    """Tests must never make real, paid calls to a model provider. app.py itself now refuses to
    load a local .env.local while running under pytest (see the guard at its top) — that closes
    the main leak, where a real .env.local sitting in the repo got re-read every time
    test_app.py's application-restart test does `importlib.reload(app_module)`, silently handing
    every subsequent report-generation test a real, working client instead of the None the tests
    were written to expect. This is the belt-and-suspenders half: if a real EXAM_DIAGNOSIS_*_
    API_KEY is already sitting in the shell's actual environment (e.g. left over from manually
    testing the server in the same terminal), scrub it before any test can see it. Runs once,
    before the isolated-database fixture even starts, so nothing in the whole session can pick up
    a real key by accident. Tests that need a client can still set one via their own
    monkeypatch.setenv — that happens inside the test body, after this has already run.
    """
    for role in MODEL_ROLES:
        os.environ.pop(f'EXAM_DIAGNOSIS_{role}_API_KEY', None)


@pytest.fixture(autouse=True, scope='session')
def _use_isolated_test_database(tmp_path_factory):
    """Point every SQLiteMapping at a throwaway file for the whole test session.

    Without this, each mapping resolves its default path relative to the process's working
    directory — the same directory a locally running dev server is normally started from — so a
    test run and a dev server launched from the same place silently share one sqlite file. The
    per-test `clear()` below then wipes real data every time the suite runs, not just test
    fixtures. This fixture runs once per session, before any test's `clear()` can touch anything.
    """
    import exam_diagnosis.app as app_module
    db_path = tmp_path_factory.mktemp('exam_diagnosis_tests') / 'test.sqlite3'
    for mapping in app_module._PERSISTENT_MAPPINGS:
        mapping.repoint(db_path)


@pytest.fixture(autouse=True)
def isolate_sqlite_state(_use_isolated_test_database):
    import exam_diagnosis.app as app_module
    for mapping in app_module._PERSISTENT_MAPPINGS:
        mapping.clear()
    yield
