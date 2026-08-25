from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def create_payload(year='25-26'):
    return {'stage':'high','subject':'数学','province':'广东','school_grade':'高二','academic_year_label':year}


def test_academic_year_requires_a_well_formed_range():
    client=TestClient(app)
    assert client.post('/api/submissions',json=create_payload('25-26')).status_code == 201
    assert client.post('/api/submissions',json=create_payload('2025-2026')).status_code == 201
    for bad in ('2025','2026-2025','25-25','25-2026','abc'):
        response=client.post('/api/submissions',json=create_payload(bad))
        assert response.status_code == 422
        assert response.json()['error'] == 'invalid_academic_year'


def test_analysis_scope_may_span_more_than_one_academic_year():
    client=TestClient(app)
    created=client.post('/api/submissions',json=create_payload('25-28'))
    assert created.status_code == 201
    assert created.json()['academic_year_span'] == 3
    assert created.json()['learning_scope']['academic_year_start'] == 25


def test_scope_shorter_than_one_academic_year_is_refused_with_the_rule():
    response=TestClient(app).post('/api/submissions',json=create_payload('25-25'))
    assert response.status_code == 422
    assert '至少覆盖一整学年' in response.json()['rule']
