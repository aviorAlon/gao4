import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from exam_diagnosis.parser import OpenAICompatibleVisionParser


class VisionHandler(BaseHTTPRequestHandler):
    response = {'choices': [{'message': {'content': json.dumps({'ocr_text': '数学 第17题 函数定义域', 'subject': '数学', 'questions': [{'question_number': '17', 'question_text': '函数定义域', 'confidence': 0.99}]}, ensure_ascii=False)}}]}
    received = None
    def do_POST(self):
        length = int(self.headers['Content-Length'])
        VisionHandler.received = json.loads(self.rfile.read(length))
        body = json.dumps(self.response).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *_): pass


def server():
    httpd = HTTPServer(('127.0.0.1', 0), VisionHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def test_openai_compatible_vision_sends_multimodal_image_and_parses_strict_json():
    httpd = server()
    parser = OpenAICompatibleVisionParser(base_url=f'http://127.0.0.1:{httpd.server_port}/v1', api_key='test-key', model='vision-test')
    result = parser.parse_page('paper.jpg', b'fake-image', 2)
    httpd.shutdown()
    assert result.ocr_status == 'completed'
    assert result.questions[0].question_number == '17'
    message = VisionHandler.received['messages'][0]
    image = next(part for part in message['content'] if part['type'] == 'image_url')
    assert image['image_url']['url'].startswith('data:image/jpeg;base64,')
    assert VisionHandler.received['model'] == 'vision-test'


def test_unconfigured_vision_parser_downgrades_without_calling_network():
    result = OpenAICompatibleVisionParser().parse_page('paper.jpg', b'fake-image', 1)
    assert result.ocr_status == 'manual_confirmation_required'
    assert result.review_required is True
