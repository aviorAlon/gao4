"""Accounts, sessions and the two identity modes (optional vs required authentication)."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import exam_diagnosis.app as app_module
from exam_diagnosis import auth

SUBMISSION = {'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}


@pytest.fixture(autouse=True)
def _reset_throttle():
    app_module.LOGIN_THROTTLE.reset()


def _register(client, username='alice', password='correct horse battery', **extra):
    response = client.post('/api/auth/register', json={'username': username, 'password': password, **extra})
    assert response.status_code == 201, response.json()
    return response.json()


def _bearer(token):
    return {'Authorization': f'Bearer {token}'}


# --- primitives ------------------------------------------------------------------------------------


def test_password_hashes_are_salted_and_verifiable():
    first, second = auth.hash_password('secret-pass'), auth.hash_password('secret-pass')
    assert first != second and first.startswith('pbkdf2_sha256$')
    assert auth.verify_password('secret-pass', first) and auth.verify_password('secret-pass', second)
    assert not auth.verify_password('wrong', first)
    assert not auth.verify_password('secret-pass', 'garbage') and not auth.verify_password('x', '')
    assert not auth.verify_password('secret-pass', 'md5$1$00$00')  # unknown scheme is never accepted


def test_credential_validation():
    assert auth.validate_credentials('alice', 'long enough pw') is None
    assert auth.validate_credentials('al', 'long enough pw') == 'invalid_username'
    assert auth.validate_credentials('Alice!', 'long enough pw') == 'invalid_username'
    assert auth.validate_credentials('alice', 'short') == 'password_too_short'
    assert auth.normalize_username('  Alice ') == 'alice'


def test_bearer_parsing_and_token_hashing():
    assert auth.bearer_token('Bearer abc') == 'abc' and auth.bearer_token('bearer abc ') == 'abc'
    assert auth.bearer_token('Basic abc') is None and auth.bearer_token('') is None and auth.bearer_token('Bearer ') is None
    token = auth.new_token()
    assert len(token) >= 40 and auth.token_key(token) != token and len(auth.token_key(token)) == 64


def test_login_throttle_blocks_after_repeated_failures():
    throttle = auth.LoginThrottle(max_failures=2, window_seconds=60)
    assert not throttle.blocked('u')
    throttle.record_failure('u')
    assert not throttle.blocked('u')
    throttle.record_failure('u')
    assert throttle.blocked('u') and not throttle.blocked('someone-else')
    throttle.clear('u')
    assert not throttle.blocked('u')


# --- register / login / logout ---------------------------------------------------------------------


def test_register_returns_token_and_never_leaks_the_hash():
    client = TestClient(app_module.app)
    body = _register(client, username=' Alice ', display_name='小A')
    assert body['token'] and body['expires_at']
    assert body['user']['username'] == 'alice' and body['user']['display_name'] == '小A'
    assert 'password_hash' not in body['user'] and 'password' not in body['user']
    me = client.get('/api/auth/me', headers=_bearer(body['token'])).json()
    assert me['anonymous'] is False and me['user']['id'] == body['user']['id'] and 'password_hash' not in me['user']


def test_register_validates_and_rejects_duplicates():
    client = TestClient(app_module.app)
    assert client.post('/api/auth/register', json={'username': 'ab', 'password': 'long enough pw'}).json() == {'error': 'invalid_username'}
    assert client.post('/api/auth/register', json={'username': 'alice', 'password': 'short'}).status_code == 422
    _register(client)
    duplicate = client.post('/api/auth/register', json={'username': 'ALICE', 'password': 'another long pw'})
    assert duplicate.status_code == 409 and duplicate.json() == {'error': 'username_taken'}


def test_login_succeeds_with_right_password_and_fails_identically_otherwise():
    client = TestClient(app_module.app)
    registered = _register(client)
    ok = client.post('/api/auth/login', json={'username': 'Alice', 'password': 'correct horse battery'})
    assert ok.status_code == 200 and ok.json()['user']['id'] == registered['user']['id'] and ok.json()['token'] != registered['token']
    wrong = client.post('/api/auth/login', json={'username': 'alice', 'password': 'nope nope nope'})
    unknown = client.post('/api/auth/login', json={'username': 'nobody', 'password': 'nope nope nope'})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {'error': 'invalid_credentials'}


def test_login_is_throttled_after_five_failures():
    client = TestClient(app_module.app)
    _register(client)
    for _ in range(5):
        assert client.post('/api/auth/login', json={'username': 'alice', 'password': 'wrong wrong wrong'}).status_code == 401
    blocked = client.post('/api/auth/login', json={'username': 'alice', 'password': 'correct horse battery'})
    assert blocked.status_code == 429 and blocked.json() == {'error': 'too_many_attempts'}
    # other accounts are unaffected
    _register(client, username='bob')
    assert client.post('/api/auth/login', json={'username': 'bob', 'password': 'correct horse battery'}).status_code == 200


def test_logout_revokes_the_token():
    client = TestClient(app_module.app)
    token = _register(client)['token']
    assert client.post('/api/auth/logout', headers=_bearer(token)).json() == {'ok': True}
    after = client.get('/api/auth/me', headers=_bearer(token))
    assert after.status_code == 401 and after.json()['detail'] == {'error': 'invalid_token'}


def test_expired_and_garbage_tokens_are_401_even_when_auth_is_optional():
    client = TestClient(app_module.app)
    token = _register(client)['token']
    session = app_module.AUTH_SESSIONS[auth.token_key(token)]
    session['expires_at'] = (datetime.utcnow() - timedelta(seconds=1)).isoformat() + 'Z'
    app_module.AUTH_SESSIONS[session['id']] = session
    assert client.get('/api/submissions', headers=_bearer(token)).status_code == 401
    assert client.get('/api/submissions', headers={'Authorization': 'Bearer not-a-real-token'}).status_code == 401
    assert client.get('/api/submissions', headers={'Authorization': 'Basic abc'}).status_code == 401
    # no Authorization header at all → still the anonymous local user (auth optional)
    assert client.get('/api/submissions').status_code == 200


# --- identity and isolation ------------------------------------------------------------------------


def test_token_identity_owns_resources_and_beats_the_header():
    client = TestClient(app_module.app)
    alice = _register(client, username='alice')
    bob = _register(client, username='bob')
    created = client.post('/api/submissions', json=SUBMISSION, headers={**_bearer(alice['token']), 'X-User-Id': 'spoofed'}).json()
    assert created['user_id'] == alice['user']['id']
    assert [s['id'] for s in client.get('/api/submissions', headers=_bearer(alice['token'])).json()] == [created['id']]
    assert client.get('/api/submissions', headers=_bearer(bob['token'])).json() == []
    assert client.get(f"/api/submissions/{created['id']}", headers=_bearer(bob['token'])).status_code == 404
    assert client.get(f"/api/submissions/{created['id']}", headers={'X-User-Id': 'spoofed'}).status_code == 404
    assert client.get(f"/api/submissions/{created['id']}", headers=_bearer(alice['token'])).status_code == 200


def test_anonymous_me_in_optional_mode():
    client = TestClient(app_module.app)
    me = client.get('/api/auth/me').json()
    assert me == {'user': {'id': 'local', 'username': 'local', 'display_name': '本地用户', 'created_at': None}, 'anonymous': True, 'auth_required': False}
    assert client.get('/api/auth/me', headers={'X-User-Id': 'kid-1'}).json()['user']['id'] == 'kid-1'


def test_required_mode_rejects_anonymous_requests_but_keeps_register_and_login_open(monkeypatch):
    monkeypatch.setattr(app_module, 'AUTH_REQUIRED', True)
    client = TestClient(app_module.app)
    denied = client.get('/api/submissions')
    assert denied.status_code == 401 and denied.json()['detail'] == {'error': 'authentication_required'}
    assert client.get('/api/submissions', headers={'X-User-Id': 'local'}).status_code == 401  # header is not an identity here
    assert client.get('/api/auth/me').status_code == 401
    registered = _register(client)
    assert client.get('/api/auth/me', headers=_bearer(registered['token'])).json()['auth_required'] is True
    assert client.post('/api/auth/login', json={'username': 'alice', 'password': 'correct horse battery'}).status_code == 200
    assert client.post('/api/submissions', json=SUBMISSION, headers=_bearer(registered['token'])).status_code == 201
    assert client.get('/api/knowledge-partitions').status_code == 401


def test_health_reports_whether_login_is_required(monkeypatch):
    client = TestClient(app_module.app)
    assert client.get('/api/health').json()['auth_required'] is False
    monkeypatch.setattr(app_module, 'AUTH_REQUIRED', True)
    body = client.get('/api/health')  # still public in required mode: the login page needs it before any token exists
    assert body.status_code == 200 and body.json()['auth_required'] is True
