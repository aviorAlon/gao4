"""Tests for the on_call hook (llm.ChatClient) and the /api/model-calls endpoint it feeds."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from fastapi.testclient import TestClient

from exam_diagnosis.app import app
from exam_diagnosis.llm import ChatClient


def _server(handler_cls):
    """Handlers must drain the request body before answering. Replying and closing with unread
    bytes still in the socket makes the OS send a RST, and on Windows the client then sees
    ConnectionResetError instead of the response — which showed up as a flaky `attempts == 2`
    (the client's retry succeeded, the first attempt had been reset)."""
    server = HTTPServer(('127.0.0.1', 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_on_call_fires_once_on_success_with_latency_and_usage():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length') or 0))  # drain the body: see _server
            body = json.dumps({
                'choices': [{'message': {'content': '{"ok": true}'}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
            }).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = _server(Handler)
    calls = []
    client = ChatClient(role='analysis', base_url=f'http://127.0.0.1:{server.server_port}', api_key='k', model='m', on_call=calls.append)
    try:
        result = client.complete_json('sys', 'user')
    finally:
        server.shutdown()
        server.server_close()

    assert result == {'ok': True}
    assert len(calls) == 1
    record = calls[0]
    assert record['role'] == 'analysis' and record['model'] == 'm'
    assert record['success'] is True
    assert record['attempts'] == 1
    assert record['error'] is None
    assert record['latency_ms'] >= 0
    assert record['prompt_tokens'] == 10 and record['completion_tokens'] == 5 and record['total_tokens'] == 15


def test_on_call_fires_once_on_final_failure_after_retries_exhausted():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length') or 0))  # drain the body: see _server
            self.send_response(500)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = _server(Handler)
    calls = []
    client = ChatClient(role='split', base_url=f'http://127.0.0.1:{server.server_port}', api_key='k', model='m', max_retries=2, on_call=calls.append)
    try:
        result = client.complete_json('sys', 'user')
    finally:
        server.shutdown()
        server.server_close()

    assert result is None
    assert len(calls) == 1  # one record per complete_json call, not one per retry attempt
    record = calls[0]
    assert record['success'] is False
    assert record['attempts'] == 2  # max_retries
    assert record['error']


def test_on_call_exception_never_breaks_the_real_call():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length') or 0))  # drain the body: see _server
            body = json.dumps({'choices': [{'message': {'content': '{"ok": true}'}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = _server(Handler)

    def broken_on_call(_record):
        raise RuntimeError('logging backend is down')

    client = ChatClient(role='report', base_url=f'http://127.0.0.1:{server.server_port}', api_key='k', model='m', on_call=broken_on_call)
    try:
        result = client.complete_json('sys', 'user')
    finally:
        server.shutdown()
        server.server_close()

    assert result == {'ok': True}  # the model call itself must not be reported as failed


def test_model_calls_endpoint_reports_summary_and_isolates_by_user():
    client = TestClient(app)
    from exam_diagnosis.app import record_model_call

    record_model_call('alice', exam_id='e1')({'role': 'analysis', 'model': 'deepseek-v4-pro', 'base_url': 'https://api.deepseek.com', 'latency_ms': 120, 'attempts': 1, 'success': True, 'error': None, 'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15})
    record_model_call('alice', exam_id='e1')({'role': 'analysis', 'model': 'deepseek-v4-pro', 'base_url': 'https://api.deepseek.com', 'latency_ms': 200, 'attempts': 2, 'success': False, 'error': 'boom', 'prompt_tokens': None, 'completion_tokens': None, 'total_tokens': None})
    record_model_call('bob', exam_id='e2')({'role': 'split', 'model': 'deepseek-v4-flash', 'base_url': 'https://api.deepseek.com', 'latency_ms': 50, 'attempts': 1, 'success': True, 'error': None, 'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2})

    alice = client.get('/api/model-calls', headers={'X-User-Id': 'alice'}).json()
    assert alice['summary']['total'] == 2
    assert alice['summary']['succeeded'] == 1
    assert alice['summary']['failed'] == 1
    assert alice['summary']['total_tokens'] == 15
    assert all(call['user_id'] == 'alice' for call in alice['calls'])

    bob = client.get('/api/model-calls', headers={'X-User-Id': 'bob'}).json()
    assert bob['summary']['total'] == 1
    assert bob['calls'][0]['exam_id'] == 'e2'


def test_model_calls_endpoint_filters_by_role():
    client = TestClient(app)
    from exam_diagnosis.app import record_model_call

    record_model_call('carol')({'role': 'analysis', 'model': 'm', 'base_url': 'u', 'latency_ms': 1, 'attempts': 1, 'success': True, 'error': None, 'prompt_tokens': None, 'completion_tokens': None, 'total_tokens': None})
    record_model_call('carol')({'role': 'split', 'model': 'm', 'base_url': 'u', 'latency_ms': 1, 'attempts': 1, 'success': True, 'error': None, 'prompt_tokens': None, 'completion_tokens': None, 'total_tokens': None})

    result = client.get('/api/model-calls?role=split', headers={'X-User-Id': 'carol'}).json()
    assert all(call['role'] == 'split' for call in result['calls'])
