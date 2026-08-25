import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from exam_diagnosis.parser import OpenAICompatibleVisionParser


def run_response(content):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload={'choices':[{'message':{'content':content}}]}
            body=json.dumps(payload).encode(); self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self,*args): pass
    server=HTTPServer(('127.0.0.1',0),Handler); threading.Thread(target=server.serve_forever,daemon=True).start(); time.sleep(0.05); return server


def test_vision_accepts_markdown_fenced_json():
    server=run_response('```json\n{"ocr_text":"x","subject":"数学","questions":[{"question_number":"1","question_text":"题","confidence":0.8}]}\n```')
    result=OpenAICompatibleVisionParser(base_url=f'http://127.0.0.1:{server.server_port}',api_key='k',model='m').parse_page('p.jpg',b'x',1); server.shutdown()
    assert result.ocr_status == 'completed'


def test_vision_accepts_content_array_and_rejects_missing_fields():
    server=run_response([{'type':'text','text':'{"ocr_text":"x","subject":"数学","questions":[]}'}])
    result=OpenAICompatibleVisionParser(base_url=f'http://127.0.0.1:{server.server_port}',api_key='k',model='m').parse_page('p.jpg',b'x',1); server.shutdown()
    assert result.ocr_status == 'completed'
    server=run_response('{"ocr_text":"x"}')
    result=OpenAICompatibleVisionParser(base_url=f'http://127.0.0.1:{server.server_port}',api_key='k',model='m').parse_page('p.jpg',b'x',1); server.shutdown()
    assert result.ocr_status == 'manual_confirmation_required'
