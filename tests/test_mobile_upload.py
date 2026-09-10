"""Phone → desktop upload: scoped tokens, the LAN listener and what it refuses to expose."""
import ipaddress
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import exam_diagnosis.app as app_module
from exam_diagnosis import mobile
from exam_diagnosis.lan_listener import LanListener

SUBMISSION = {'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}
NOW = datetime(2026, 9, 10, 12, 0, 0)


@pytest.fixture(autouse=True)
def _listener_is_stopped_after_each_test():
    yield
    app_module.LAN_LISTENER.stop()


def _exam(client, name='月考'):
    submission = client.post('/api/submissions', json=SUBMISSION).json()
    return client.post(f"/api/submissions/{submission['id']}/exams", json={'name': name}).json()


# --- tokens ------------------------------------------------------------------------------------------


def test_tokens_are_random_and_only_their_hash_is_stored():
    first, second = mobile.new_token(), mobile.new_token()
    assert first != second and len(first) >= 30
    assert mobile.token_key(first) != first and len(mobile.token_key(first)) == 64
    assert mobile.token_key(first) == mobile.token_key(first)  # stable


def test_session_is_open_until_closed_or_expired():
    live = {'expires_at': mobile.expiry(10, NOW), 'closed': False}
    assert mobile.session_is_open(live, NOW) and not mobile.is_expired(live, NOW)
    assert not mobile.session_is_open({**live, 'closed': True}, NOW)
    expired = {'expires_at': (NOW - timedelta(minutes=1)).isoformat() + 'Z', 'closed': False}
    assert mobile.is_expired(expired, NOW) and not mobile.session_is_open(expired, NOW)
    assert mobile.is_expired({'expires_at': 'nonsense'}, NOW)  # unparseable = expired, never open
    assert not mobile.session_is_open(None, NOW)


# --- addresses and QR --------------------------------------------------------------------------------


def test_lan_addresses_are_routable_ipv4_with_private_first():
    addresses = mobile.lan_addresses()
    for address in addresses:
        parsed = ipaddress.IPv4Address(address)
        assert not (parsed.is_loopback or parsed.is_link_local or parsed.is_multicast)
    assert len(addresses) == len(set(addresses))
    privates = [i for i, a in enumerate(addresses) if ipaddress.IPv4Address(a).is_private]
    publics = [i for i, a in enumerate(addresses) if not ipaddress.IPv4Address(a).is_private]
    assert not (privates and publics) or max(privates) < min(publics)


def test_upload_urls_point_at_the_mobile_page():
    urls = mobile.upload_urls(8767, 'tok', ['192.168.1.5', '10.0.0.9'])
    assert urls == ['http://192.168.1.5:8767/m/tok', 'http://10.0.0.9:8767/m/tok']


def test_qr_is_svg_and_encodes_something():
    svg = mobile.qr_svg('http://192.168.1.5:8767/m/tok')
    assert svg.lstrip().startswith('<?xml') and '<svg' in svg and '<path' in svg
    assert len(svg) > 500


# --- the LAN restriction -----------------------------------------------------------------------------


def test_only_the_phone_upload_surface_is_reachable_from_the_lan():
    assert mobile.path_allowed_from_lan('/m/abc')
    assert mobile.path_allowed_from_lan('/api/mobile-upload/abc/assets')
    for blocked in ('/api/submissions', '/api/settings', '/api/health', '/api/reports/x/export'):
        assert not mobile.path_allowed_from_lan(blocked)


def test_lan_requests_are_identified_by_the_listening_port_not_the_client():
    # A web deployment bound to 0.0.0.0 on purpose must not be restricted, so the check is on the
    # socket that accepted the request, not on who connected.
    assert mobile.is_lan_request({'server': ('0.0.0.0', 8767)}, 8767)
    assert not mobile.is_lan_request({'server': ('0.0.0.0', 8765)}, 8767)
    assert not mobile.is_lan_request({'server': ('0.0.0.0', 8767)}, None)
    assert not mobile.is_lan_request({}, 8767)


# --- the listener ------------------------------------------------------------------------------------


def test_listener_starts_serves_and_stops():
    listener = LanListener(app_module.app)
    assert not listener.running and listener.describe()['port'] is None
    port = listener.start()
    try:
        assert port and listener.running
        assert listener.start() == port  # idempotent
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/m/not-a-real-token', timeout=5) as response:
            body = response.read().decode()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()  # 410 for an unknown token is the expected answer
    finally:
        listener.stop()
    assert '链接已失效' in body
    assert not listener.running
    listener.stop()  # stopping twice is fine


def test_listener_refuses_everything_else_over_the_lan():
    client = TestClient(app_module.app)
    exam = _exam(client)
    started = client.post(f"/api/exams/{exam['id']}/mobile-upload")
    assert started.status_code == 201, started.json()
    port = app_module.LAN_LISTENER.port
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(f'http://127.0.0.1:{port}/api/submissions', timeout=5)
    assert caught.value.code == 403
    assert json.loads(caught.value.read())['error'] == 'not_available_over_lan'
    # ...while the same path on the normal listener is untouched
    assert client.get('/api/submissions').status_code == 200


# --- the API flow -------------------------------------------------------------------------------------


def test_start_returns_a_qr_and_reachable_urls():
    client = TestClient(app_module.app)
    exam = _exam(client, '期中')
    body = client.post(f"/api/exams/{exam['id']}/mobile-upload").json()
    assert body['exam_id'] == exam['id'] and body['closed'] is False and body['upload_count'] == 0
    assert body['listener']['running'] is True and body['listener']['port']
    assert body['qr_svg'] and '<svg' in body['qr_svg']
    assert body['urls'] and all(url.startswith('http://') and '/m/' in url for url in body['urls'])
    assert body['security_note'] == mobile.SECURITY_NOTE
    status = client.get(f"/api/exams/{exam['id']}/mobile-upload").json()
    assert status['open'] is True and status['upload_count'] == 0
    assert 'qr_svg' not in status  # the token is only handed out once, at creation


def test_photo_upload_lands_on_the_exam_and_can_then_be_parsed():
    client = TestClient(app_module.app)
    exam = _exam(client)
    token = client.post(f"/api/exams/{exam['id']}/mobile-upload").json()['urls'][0].rsplit('/m/', 1)[1]

    page = client.get(f'/m/{token}')
    assert page.status_code == 200 and '上传到「月考」' in page.text
    assert 'capture="environment"' in page.text  # opens the camera without needing https

    uploaded = client.post(f'/api/mobile-upload/{token}/assets', files={'file': ('photo.txt', b'1. from the phone', 'text/plain')})
    assert uploaded.status_code == 201 and uploaded.json()['upload_count'] == 1
    assets = client.get(f"/api/exams/{exam['id']}/assets").json()['assets']
    assert [a['filename'] for a in assets] == ['photo.txt']
    assert client.get(f"/api/exams/{exam['id']}/mobile-upload").json()['upload_count'] == 1

    # the desktop then parses them with the ordinary flow
    job = client.post(f"/api/exams/{exam['id']}/reparse").json()['job_id']
    assert client.post(f'/api/jobs/{job}/run').json()['status'] == 'completed'
    assert client.get(f"/api/exams/{exam['id']}/pages").json()['pages'][0]['asset_id'] == assets[0]['id']


def test_the_same_limits_apply_as_to_desktop_uploads():
    client = TestClient(app_module.app)
    exam = _exam(client)
    token = client.post(f"/api/exams/{exam['id']}/mobile-upload").json()['urls'][0].rsplit('/m/', 1)[1]
    assert client.post(f'/api/mobile-upload/{token}/assets', files={'file': ('x.exe', b'x', 'application/x-msdownload')}).status_code == 415
    assert client.post(f'/api/mobile-upload/{token}/assets', files={'file': ('big.jpg', b'x' * (10 * 1024 * 1024 + 1), 'image/jpeg')}).status_code == 413
    client.post(f'/api/mobile-upload/{token}/assets', files={'file': ('a.jpg', b'same', 'image/jpeg')})
    duplicate = client.post(f'/api/mobile-upload/{token}/assets', files={'file': ('b.jpg', b'same', 'image/jpeg')})
    assert duplicate.status_code == 409 and duplicate.json()['error'] == 'duplicate_asset'


def test_closing_the_channel_revokes_the_token_and_stops_the_listener():
    client = TestClient(app_module.app)
    exam = _exam(client)
    token = client.post(f"/api/exams/{exam['id']}/mobile-upload").json()['urls'][0].rsplit('/m/', 1)[1]
    assert app_module.LAN_LISTENER.running
    assert client.delete(f"/api/exams/{exam['id']}/mobile-upload").json()['closed'] == 1
    assert not app_module.LAN_LISTENER.running  # no live sessions, no open port
    assert client.get(f'/m/{token}').status_code == 410
    assert client.post(f'/api/mobile-upload/{token}/assets', files={'file': ('a.jpg', b'x', 'image/jpeg')}).status_code == 410
    assert client.get(f"/api/exams/{exam['id']}/mobile-upload").json()['open'] is False


def test_the_phone_can_close_the_channel_itself():
    client = TestClient(app_module.app)
    exam = _exam(client)
    token = client.post(f"/api/exams/{exam['id']}/mobile-upload").json()['urls'][0].rsplit('/m/', 1)[1]
    client.post(f'/api/mobile-upload/{token}/assets', files={'file': ('a.jpg', b'x', 'image/jpeg')})
    closed = client.delete(f'/api/mobile-upload/{token}').json()
    assert closed == {'closed': True, 'upload_count': 1}
    assert client.delete(f'/api/mobile-upload/{token}').json() == {'closed': False}
    assert not app_module.LAN_LISTENER.running


def test_an_expired_token_stops_working_without_anyone_closing_it():
    client = TestClient(app_module.app)
    exam = _exam(client)
    token = client.post(f"/api/exams/{exam['id']}/mobile-upload", params={'minutes': 10}).json()['urls'][0].rsplit('/m/', 1)[1]
    session = app_module.MOBILE_SESSIONS[mobile.token_key(token)]
    session['expires_at'] = (datetime.utcnow() - timedelta(seconds=1)).isoformat() + 'Z'
    app_module.MOBILE_SESSIONS[session['id']] = session
    assert client.get(f'/m/{token}').status_code == 410
    assert client.post(f'/api/mobile-upload/{token}/assets', files={'file': ('a.jpg', b'x', 'image/jpeg')}).status_code == 410
    assert client.get(f"/api/exams/{exam['id']}/mobile-upload").json()['open'] is False


def test_a_token_cannot_reach_anything_but_its_own_exam():
    client = TestClient(app_module.app)
    first, second = _exam(client, '第一场'), _exam(client, '第二场')
    token = client.post(f"/api/exams/{first['id']}/mobile-upload").json()['urls'][0].rsplit('/m/', 1)[1]
    client.post(f'/api/mobile-upload/{token}/assets', files={'file': ('a.txt', b'x', 'text/plain')})
    assert len(client.get(f"/api/exams/{first['id']}/assets").json()['assets']) == 1
    assert client.get(f"/api/exams/{second['id']}/assets").json()['assets'] == []
    # a made-up token gets nothing at all
    assert client.get('/m/made-up-token').status_code == 410
    assert client.post('/api/mobile-upload/made-up-token/assets', files={'file': ('a.txt', b'x', 'text/plain')}).status_code == 410


def test_a_confirmed_exam_will_not_open_a_channel():
    client = TestClient(app_module.app)
    exam = _exam(client)
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [{'question_number': '1', 'question_text': 'x', 'is_wrong_question': True, 'original_question_available': True}]})
    refused = client.post(f"/api/exams/{exam['id']}/mobile-upload")
    assert refused.status_code == 409 and refused.json()['error'] == 'exam_confirmed'
    assert client.post('/api/exams/nope/mobile-upload').status_code == 404


def test_two_exams_can_have_channels_and_the_listener_survives_until_the_last_closes():
    client = TestClient(app_module.app)
    first, second = _exam(client, 'A'), _exam(client, 'B')
    client.post(f"/api/exams/{first['id']}/mobile-upload")
    client.post(f"/api/exams/{second['id']}/mobile-upload")
    port = app_module.LAN_LISTENER.port
    client.delete(f"/api/exams/{first['id']}/mobile-upload")
    assert app_module.LAN_LISTENER.running and app_module.LAN_LISTENER.port == port
    client.delete(f"/api/exams/{second['id']}/mobile-upload")
    assert not app_module.LAN_LISTENER.running
