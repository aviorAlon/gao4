"""python -m exam_diagnosis.server: free-port selection and the LISTENING announcement a
supervising process relies on."""
import io
import json
import socket
import threading
import time
import urllib.request

from exam_diagnosis import server


def test_pick_free_port_returns_a_bindable_port():
    port = server.pick_free_port()
    assert 1024 < port < 65536
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', port))  # still free right after picking


def test_parse_args_defaults_and_overrides():
    args = server.parse_args([])
    assert (args.host, args.port, args.data_dir, args.log_level) == ('127.0.0.1', 8765, None, 'info')
    args = server.parse_args(['--port', '0', '--data-dir', 'X', '--host', '0.0.0.0', '--log-level', 'warning'])
    assert (args.host, args.port, args.data_dir, args.log_level) == ('0.0.0.0', 0, 'X', 'warning')


def test_server_announces_its_address_once_it_is_actually_listening(tmp_path, monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_DATA_DIR', str(tmp_path / 'data'))
    port = server.pick_free_port()
    announced = io.StringIO()
    instance = server.build_server('127.0.0.1', port, log_level='warning', announce=announced)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and f'LISTENING 127.0.0.1:{port}' not in announced.getvalue():
            time.sleep(0.05)
        assert f'LISTENING 127.0.0.1:{port}' in announced.getvalue()
        # the announcement means the socket is bound: a request right now must succeed
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=5) as response:
            body = json.loads(response.read().decode())
        assert body['status'] == 'ok' and body['data_dir'] == str((tmp_path / 'data').resolve())
    finally:
        instance.should_exit = True
        thread.join(timeout=10)
    assert not thread.is_alive()
