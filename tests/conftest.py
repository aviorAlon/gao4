from pathlib import Path

import pytest
import yaml

SHIPPED_KNOWLEDGE_PATH = Path(__file__).resolve().parents[1] / 'data' / 'knowledge' / 'high_math_guangdong_v1.yaml'
SHIPPED_KNOWLEDGE_VERSION = yaml.safe_load(SHIPPED_KNOWLEDGE_PATH.read_text(encoding='utf-8'))['version']


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
