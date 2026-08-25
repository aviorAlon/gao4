import pytest


@pytest.fixture(autouse=True)
def isolate_sqlite_state():
    import exam_diagnosis.app as app_module
    for mapping in (
        app_module.SUBMISSIONS, app_module.EXAMS, app_module.ASSETS,
        app_module.QUESTIONS, app_module.RUNS, app_module.UPLOAD_SESSIONS,
        app_module.JOBS, app_module.PAGES, app_module.QUESTION_RECORDS,
        app_module.VERIFICATION_QUESTIONS, app_module.MASTERY_RECORDS,
    ):
        mapping.clear()
    yield
