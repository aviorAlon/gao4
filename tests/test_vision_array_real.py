import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from exam_diagnosis.parser import OpenAICompatibleVisionParser


def test_content_array_response_is_supported():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            content=[{'type':'text','text':'{"ocr_text":"x","subject":"数学","questions":[]}'}]
            body=json.dumps({'choices':[{'message':{'content':content}}]}).encode(); self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self,*args): pass
    server=HTTPServer(('127.0.0.1',0),Handler); threading.Thread(target=server.serve_forever,daemon=True).start()
    time.sleep(0.05)
    result=OpenAICompatibleVisionParser(base_url=f'http://127.0.0.1:{server.server_port}',api_key='k',model='m').parse_page('p.jpg',b'x',1); server.shutdown(); server.server_close()
    assert result.ocr_status == 'completed'
