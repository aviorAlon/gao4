"""Every origin the UI can be served from must get CORS headers — the packaged desktop app's
webview included. Verified with real Origin headers, which is what the browser layer checks."""
import pytest
from fastapi.testclient import TestClient

import exam_diagnosis.app as app_module


@pytest.mark.parametrize('origin', ['http://localhost:5173', 'http://127.0.0.1:5174', 'http://tauri.localhost', 'https://tauri.localhost', 'tauri://localhost'])
def test_ui_origins_are_allowed(origin):
    client = TestClient(app_module.app)
    response = client.get('/api/health', headers={'Origin': origin})
    assert response.headers.get('access-control-allow-origin') == origin
    preflight = client.options('/api/submissions', headers={'Origin': origin, 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'x-user-id,content-type'})
    assert preflight.status_code == 200 and preflight.headers.get('access-control-allow-origin') == origin


@pytest.mark.parametrize('origin', ['http://evil.example', 'http://localhost.evil.example', 'http://tauri.localhost.evil.example'])
def test_other_origins_get_no_cors_header(origin):
    client = TestClient(app_module.app)
    assert client.get('/api/health', headers={'Origin': origin}).headers.get('access-control-allow-origin') is None
