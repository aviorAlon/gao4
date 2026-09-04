"""Scanned (image-only) PDFs: detected by an empty text layer, rendered to images, parsed page by
page through the vision parser, and folded back into one page result per uploaded asset."""
import fitz
import pytest
from fastapi.testclient import TestClient

import exam_diagnosis.app as app_module
from exam_diagnosis.parser import (
    LocalParser, OpenAICompatibleVisionParser, PageParseResult, QuestionCandidate,
    extract_pdf_text, image_data_url, is_scanned_pdf_result, parse_scanned_pdf, render_pdf_pages,
)


def _tiny_png() -> bytes:
    return fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), 0).tobytes('png')


@pytest.fixture
def scanned_pdf(tmp_path):
    """Three pages, each nothing but a pasted image — what a phone-scan-to-PDF app produces."""
    document = fitz.open()
    for _ in range(3):
        page = document.new_page(width=200, height=200)
        page.insert_image(fitz.Rect(10, 10, 190, 190), stream=_tiny_png())
    path = tmp_path / 'scan.pdf'
    document.save(str(path))
    return path


@pytest.fixture
def text_pdf(tmp_path):
    document = fitz.open()
    document.new_page().insert_text((50, 50), '1. What is 2+2')
    path = tmp_path / 'text.pdf'
    document.save(str(path))
    return path


class StubVision:
    """Deterministic stand-in for OpenAICompatibleVisionParser: one canned result per page."""

    def __init__(self, per_page):
        self.per_page = per_page
        self.calls = []

    def parse_page(self, filename, content, page_order):
        self.calls.append((filename, content[:8], page_order))
        outcome = self.per_page[page_order - 1]
        if outcome == 'fail':
            return PageParseResult(filename=filename, page_order=page_order, material_type='paper', subject='', ocr_text='', ocr_confidence=0.0, review_required=True, ocr_status='manual_confirmation_required', warning='视觉解析调用 failed: boom')
        return PageParseResult(filename=filename, page_order=page_order, material_type='paper', subject=outcome.get('subject', ''), ocr_text=outcome['text'], ocr_confidence=outcome.get('confidence', 0.9), review_required=False, questions=[QuestionCandidate(n, t, 0.9) for n, t in outcome.get('questions', [])])


# --- detection -----------------------------------------------------------------------------


def test_scanned_pdf_is_detected_by_empty_text_layer_with_pages(scanned_pdf, text_pdf):
    scanned = extract_pdf_text(scanned_pdf)
    assert scanned['status'] == 'completed' and scanned['text'] == '' and scanned['page_count'] == 3
    assert is_scanned_pdf_result(scanned)
    assert not is_scanned_pdf_result(extract_pdf_text(text_pdf))


def test_unreadable_pdf_is_not_mistaken_for_a_scan(tmp_path):
    broken = tmp_path / 'broken.pdf'
    broken.write_bytes(b'not a pdf')
    result = extract_pdf_text(broken)
    assert result['status'] == 'manual_confirmation_required' and result['page_count'] == 0
    assert not is_scanned_pdf_result(result)


# --- rendering -----------------------------------------------------------------------------


def test_render_pdf_pages_yields_one_png_per_page(scanned_pdf):
    images = render_pdf_pages(scanned_pdf)
    assert len(images) == 3
    assert all(image.startswith(b'\x89PNG') for image in images)


def test_render_pdf_pages_honours_page_cap(scanned_pdf, monkeypatch):
    assert len(render_pdf_pages(scanned_pdf, max_pages=2)) == 2
    monkeypatch.setenv('EXAM_DIAGNOSIS_SCANNED_PDF_MAX_PAGES', '1')
    assert len(render_pdf_pages(scanned_pdf)) == 1
    monkeypatch.setenv('EXAM_DIAGNOSIS_SCANNED_PDF_MAX_PAGES', 'garbage')
    assert len(render_pdf_pages(scanned_pdf)) == 3  # bad value → default cap (30), not a crash


def test_image_data_url_labels_png_and_jpeg_correctly():
    assert image_data_url(_tiny_png()).startswith('data:image/png;base64,')
    assert image_data_url(b'\xff\xd8\xff\xe0jpeg-ish').startswith('data:image/jpeg;base64,')


# --- merging per-page vision results ---------------------------------------------------------


def test_parse_scanned_pdf_merges_all_pages_into_one_result(scanned_pdf):
    vision = StubVision([
        {'text': '第一页', 'subject': '数学', 'questions': [('1', '求定义域')]},
        {'text': '第二页', 'questions': [('2', '求值域'), ('3', '证明')]},
        {'text': '第三页', 'confidence': 0.7},
    ])
    result = parse_scanned_pdf(vision, '试卷.pdf', scanned_pdf, 1)
    assert [call[2] for call in vision.calls] == [1, 2, 3]
    assert all(call[1].startswith(b'\x89PNG') for call in vision.calls)
    assert result.ocr_status == 'completed'
    assert result.filename == '试卷.pdf' and result.page_order == 1 and result.material_type == 'paper'
    assert result.subject == '数学'
    assert result.ocr_text == '第一页\n\n第二页\n\n第三页'
    assert [q.question_number for q in result.questions] == ['1', '2', '3']
    assert result.ocr_confidence == 0.7  # the weakest page decides, not the best one
    assert result.review_required is False
    assert result.warning == ''


def test_parse_scanned_pdf_partial_failure_keeps_good_pages_but_flags_review(scanned_pdf):
    vision = StubVision([{'text': 'p1', 'questions': [('1', 'a')]}, 'fail', {'text': 'p3'}])
    result = parse_scanned_pdf(vision, 'scan.pdf', scanned_pdf, 1)
    assert result.ocr_status == 'completed'
    assert result.ocr_text == 'p1\n\np3'
    assert result.review_required is True
    assert '第 2 页' in result.warning


def test_parse_scanned_pdf_all_pages_failing_is_manual_confirmation(scanned_pdf):
    result = parse_scanned_pdf(StubVision(['fail', 'fail', 'fail']), 'scan.pdf', scanned_pdf, 1)
    assert result.ocr_status == 'manual_confirmation_required'
    assert result.review_required is True and result.ocr_text == '' and result.questions == []
    assert '3 页' in result.warning and 'boom' in result.warning


def test_parse_scanned_pdf_page_cap_is_reported_and_forces_review(scanned_pdf, monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_SCANNED_PDF_MAX_PAGES', '2')
    vision = StubVision([{'text': 'p1', 'questions': [('1', 'a')]}, {'text': 'p2', 'questions': [('2', 'b')]}, {'text': 'never'}])
    result = parse_scanned_pdf(vision, 'scan.pdf', scanned_pdf, 1)
    assert len(vision.calls) == 2
    assert result.review_required is True
    assert '共 3 页' in result.warning and '前 2 页' in result.warning


def test_parse_scanned_pdf_without_vision_is_manual_confirmation_not_a_fake(scanned_pdf):
    # LocalParser on image bytes always says "manual confirmation" — the merge must not turn
    # three honest refusals into a completed page.
    result = parse_scanned_pdf(LocalParser(), 'scan.pdf', scanned_pdf, 1)
    assert result.ocr_status == 'manual_confirmation_required'


# --- vision parser plumbing --------------------------------------------------------------------


def test_vision_parser_timeout_is_env_configurable(monkeypatch):
    monkeypatch.delenv('EXAM_DIAGNOSIS_VISION_TIMEOUT', raising=False)
    assert OpenAICompatibleVisionParser().timeout == 60.0
    monkeypatch.setenv('EXAM_DIAGNOSIS_VISION_TIMEOUT', '120')
    assert OpenAICompatibleVisionParser().timeout == 120.0
    assert OpenAICompatibleVisionParser(timeout=3.5).timeout == 3.5  # explicit argument still wins
    monkeypatch.setenv('EXAM_DIAGNOSIS_VISION_TIMEOUT', 'nope')
    assert OpenAICompatibleVisionParser().timeout == 60.0


def test_vision_parser_reports_failed_calls_through_on_call_hook():
    seen = []
    parser = OpenAICompatibleVisionParser(base_url='http://127.0.0.1:1', api_key='k', model='m', timeout=1.0, on_call=seen.append)
    result = parser.parse_page('p.jpg', b'x', 1)
    assert result.ocr_status == 'manual_confirmation_required'
    assert len(seen) == 1
    assert seen[0]['role'] == 'vision' and seen[0]['model'] == 'm' and seen[0]['success'] is False
    assert seen[0]['attempts'] == 2 and seen[0]['error']


def test_vision_parser_for_is_none_when_unconfigured_and_logs_nothing():
    assert app_module.vision_parser_for('user-x', exam_id='e') is None
    assert list(app_module.MODEL_CALLS.values()) == []


def test_vision_parser_for_returns_observed_parser_when_configured(monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_VISION_BASE_URL', 'http://127.0.0.1:1')
    monkeypatch.setenv('EXAM_DIAGNOSIS_VISION_API_KEY', 'k')
    monkeypatch.setenv('EXAM_DIAGNOSIS_VISION_MODEL', 'm')
    parser = app_module.vision_parser_for('user-x', exam_id='e', asset_id='a')
    assert isinstance(parser, OpenAICompatibleVisionParser) and parser.configured
    parser.timeout = 1.0
    parser.parse_page('p.jpg', b'x', 1)  # connection refused → logged as a failed vision call
    calls = list(app_module.MODEL_CALLS.values())
    assert len(calls) == 1
    assert calls[0]['role'] == 'vision' and calls[0]['success'] is False
    assert calls[0]['exam_id'] == 'e' and calls[0]['asset_id'] == 'a' and calls[0]['user_id'] == 'user-x'


def test_vision_parser_for_respects_the_shared_budget_gate(monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_VISION_BASE_URL', 'http://127.0.0.1:1')
    monkeypatch.setenv('EXAM_DIAGNOSIS_VISION_API_KEY', 'k')
    monkeypatch.setenv('EXAM_DIAGNOSIS_VISION_MODEL', 'm')
    monkeypatch.setattr(app_module, 'MODEL_MAX_CALLS_PER_MINUTE', 1)
    app_module.record_model_call('user-x')({'role': 'analysis', 'model': 'm', 'base_url': 'b', 'latency_ms': 1, 'attempts': 1, 'success': True, 'error': '', 'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2})
    assert app_module.vision_parser_for('user-x', exam_id='e') is None
    blocked = [c for c in app_module.MODEL_CALLS.values() if c.get('blocked')]
    assert len(blocked) == 1 and blocked[0]['role'] == 'vision' and blocked[0]['error'] == 'rate_limited'


# --- end to end through the job pipeline -------------------------------------------------------


def _exam_with_upload(client, filename, content, media_type):
    sub = client.post('/api/submissions', json={'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}).json()
    exam = client.post(f"/api/submissions/{sub['id']}/exams", json={'name': '扫描件'}).json()
    session = client.post(f"/api/exams/{exam['id']}/upload-sessions").json()
    client.post(f"/api/upload-sessions/{session['id']}/assets", files={'file': (filename, content, media_type)})
    job = client.post(f"/api/upload-sessions/{session['id']}/finish").json()['job_id']
    return exam, job


def test_job_parses_scanned_pdf_through_vision(scanned_pdf, monkeypatch):
    client = TestClient(app_module.app)
    exam, job = _exam_with_upload(client, '期中试卷.pdf', scanned_pdf.read_bytes(), 'application/pdf')
    stub = StubVision([{'text': 'p1', 'subject': '数学', 'questions': [('1', '求定义域')]}, {'text': 'p2', 'questions': [('2', '求值域')]}, {'text': 'p3'}])
    seen_context = {}
    monkeypatch.setattr(app_module, 'vision_parser_for', lambda user_id, **ctx: seen_context.update(ctx) or stub)
    assert client.post(f'/api/jobs/{job}/run').json()['status'] == 'completed'
    page = client.get(f"/api/exams/{exam['id']}/pages").json()['pages'][0]
    assert page['ocr_status'] == 'completed'
    assert page['ocr_text'] == 'p1\n\np2\n\np3'
    assert [q['question_number'] for q in page['questions']] == ['1', '2']
    assert page['subject'] == '数学' and page['subject_mismatch'] is False
    assert seen_context == {'exam_id': exam['id'], 'asset_id': client.get(f"/api/exams/{exam['id']}/assets").json()['assets'][0]['id']}
    asset = client.get(f"/api/exams/{exam['id']}/assets").json()['assets'][0]
    assert asset['scanned_pdf'] is True and asset['ocr_status'] == 'completed'
    draft = client.get(f"/api/exams/{exam['id']}/parse-draft").json()
    assert [q['question_number'] for q in draft['questions']] == ['1', '2']


def test_job_scanned_pdf_without_vision_is_manual_confirmation_with_clear_reason(scanned_pdf):
    client = TestClient(app_module.app)
    exam, job = _exam_with_upload(client, '期中试卷.pdf', scanned_pdf.read_bytes(), 'application/pdf')
    assert client.post(f'/api/jobs/{job}/run').json()['status'] == 'completed'
    page = client.get(f"/api/exams/{exam['id']}/pages").json()['pages'][0]
    assert page['ocr_status'] == 'manual_confirmation_required'
    assert page['ocr_text'] == '' and page['questions'] == []
    assert '扫描件' in page['warning'] and '3 页' in page['warning'] and 'EXAM_DIAGNOSIS_VISION' in page['warning']
    assert list(app_module.MODEL_CALLS.values()) == []  # nothing was called, nothing was charged


def test_job_text_pdf_still_takes_the_text_path(text_pdf, monkeypatch):
    client = TestClient(app_module.app)
    exam, job = _exam_with_upload(client, 'paper.pdf', text_pdf.read_bytes(), 'application/pdf')
    monkeypatch.setattr(app_module, 'vision_parser_for', lambda user_id, **ctx: pytest.fail('text PDFs must never go to vision'))
    assert client.post(f'/api/jobs/{job}/run').json()['status'] == 'completed'
    page = client.get(f"/api/exams/{exam['id']}/pages").json()['pages'][0]
    assert 'What is 2+2' in page['ocr_text']
    assert page['questions'][0]['question_number'] == '1'
    assert 'scanned_pdf' not in client.get(f"/api/exams/{exam['id']}/assets").json()['assets'][0]
