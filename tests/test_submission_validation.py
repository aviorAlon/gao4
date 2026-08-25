from fastapi.testclient import TestClient
from exam_diagnosis.app import app


def create_payload(year='25-26'):
    return {'stage':'high','subject':'数学','province':'广东','school_grade':'高二','academic_year_label':year}


def test_academic_year_requires_consecutive_year_format():
    client=TestClient(app)
    assert client.post('/api/submissions',json=create_payload('25-26')).status_code == 201
    assert client.post('/api/submissions',json=create_payload('2025-2026')).status_code == 201
    for bad in ('2025','25-27','2026-2025','abc'):
        response=client.post('/api/submissions',json=create_payload(bad))
        assert response.status_code == 422
        assert response.json()['error'] == 'invalid_academic_year'
