from dataclasses import asdict, dataclass, field
import base64
import json
import os
from pathlib import Path
import re
import urllib.request
import urllib.error
from typing import Any


# Best-effort, last-resort question-boundary matcher for LocalParser — used only when the
# `split` model role (ModelTextSplitter, below) is unconfigured or fails. Deliberately narrow:
# real exam PDFs are full of numbers that aren't question numbers (fractions broken across
# OCR/text-extraction lines, sub-question labels like "(1)(2)" inside one essay question,
# answer-choice values). Matching against a real 2025 national paper's extracted text, adding
# "(N)"-style bracket numbering here caused essay sub-questions to be mis-split as new top-level
# questions — so only "第N题"/"N题"/"N."/"N、"/"N．" are matched, all guarded so a number can't be
# part of a longer number (no digit immediately before/after), which also keeps decimals like
# "2.5" from being read as a question boundary. Whitespace here is [ \t] on purpose, not \s: \s
# matches \n too, and against that same real paper it let a bare fraction-denominator line like
# "2." swallow the *next* line as if it were the captured question text. This still won't handle
# every paper; that's why it's a fallback and why callers must treat its output as
# review_required, never as a green light.
QUESTION_NUMBER_RE = re.compile(r"(?<!\d)(?:第[ \t]*)?(\d{1,3})[ \t]*(?:题|[.、．])(?!\d)[ \t]*([^\n]+)")


def extract_pdf_text(path: str | Path) -> dict[str, Any]:
    """Pull the PDF's text layer. `page_count` lets callers tell a scanned PDF (pages exist, text
    layer empty) apart from a genuinely empty document — see is_scanned_pdf_result."""
    try:
        import fitz
        document = fitz.open(str(path))
        text = '\n'.join(page.get_text() for page in document).strip()
        return {'status': 'completed', 'text': text, 'warning': '', 'page_count': len(document)}
    except Exception as exc:
        return {'status': 'manual_confirmation_required', 'text': '', 'warning': f'未能提取 PDF 文本，请人工确认：{exc}', 'page_count': 0}


def is_scanned_pdf_result(pdf_result: dict[str, Any]) -> bool:
    """A PDF that opened fine and has pages but no text layer at all is (almost always) a scan —
    pages are images, so the text path has nothing to split and the only way in is vision."""
    return pdf_result.get('status') == 'completed' and not pdf_result.get('text', '').strip() and pdf_result.get('page_count', 0) > 0


SCANNED_PDF_MAX_PAGES_DEFAULT = 30
SCANNED_PDF_RENDER_DPI = 150


def scanned_pdf_max_pages() -> int:
    """Cap on how many pages of one scanned PDF get sent to the vision model. Every page is a paid
    vision call, so a 200-page scan must not silently turn into 200 calls; overridable via env."""
    try:
        return max(1, int(os.getenv('EXAM_DIAGNOSIS_SCANNED_PDF_MAX_PAGES', SCANNED_PDF_MAX_PAGES_DEFAULT)))
    except ValueError:
        return SCANNED_PDF_MAX_PAGES_DEFAULT


def render_pdf_pages(path: str | Path, max_pages: int | None = None, dpi: int = SCANNED_PDF_RENDER_DPI) -> list[bytes]:
    """Rasterise the first `max_pages` pages to PNG bytes (150 dpi is plenty for printed exam text
    and keeps each image well under typical vision-API size limits)."""
    import fitz
    limit = max_pages if max_pages is not None else scanned_pdf_max_pages()
    document = fitz.open(str(path))
    images = []
    for index, page in enumerate(document):
        if index >= limit:
            break
        images.append(page.get_pixmap(dpi=dpi).tobytes('png'))
    return images


def parse_scanned_pdf(vision_parser: 'LocalParser', filename: str, path: str | Path, page_order: int) -> 'PageParseResult':
    """Run every rendered page of a scanned PDF through the vision parser and fold the per-page
    results into one PageParseResult, because the data model treats one uploaded asset as one
    logical page. Partial success is surfaced honestly: any page the vision model couldn't read
    leaves the whole asset review_required with a warning naming those pages, and if no page could
    be read at all the asset falls back to manual confirmation like any other unparsed upload."""
    name = Path(filename).name
    material_type = LocalParser.classify_material(filename)
    try:
        images = render_pdf_pages(path)
        total_pages = extract_pdf_text(path).get('page_count', len(images))
    except Exception as exc:
        return PageParseResult(filename=name, page_order=page_order, material_type=material_type, subject='', ocr_text='', ocr_confidence=0.0, review_required=True, ocr_status='manual_confirmation_required', warning=f'扫描 PDF 无法渲染为图片，请人工确认：{exc}')
    if not images:
        return PageParseResult(filename=name, page_order=page_order, material_type=material_type, subject='', ocr_text='', ocr_confidence=0.0, review_required=True, ocr_status='manual_confirmation_required', warning='PDF 没有任何页面，请人工确认。')
    page_results = [vision_parser.parse_page(f'{name}#p{index + 1}', image, index + 1) for index, image in enumerate(images)]
    parsed = [(index + 1, result) for index, result in enumerate(page_results) if result.ocr_status == 'completed']
    failed = [index + 1 for index, result in enumerate(page_results) if result.ocr_status != 'completed']
    if not parsed:
        first_warning = page_results[0].warning
        return PageParseResult(filename=name, page_order=page_order, material_type=material_type, subject='', ocr_text='', ocr_confidence=0.0, review_required=True, ocr_status='manual_confirmation_required', warning=f'扫描 PDF（无文字层）共 {total_pages} 页，视觉解析均未成功：{first_warning}')
    questions: list[QuestionCandidate] = []
    for _, result in parsed:
        questions.extend(result.questions)
    warnings = []
    if failed:
        warnings.append(f"扫描 PDF 第 {', '.join(map(str, failed))} 页视觉解析未成功，请人工核对")
    if total_pages > len(images):
        warnings.append(f'扫描 PDF 共 {total_pages} 页，仅识别前 {len(images)} 页（上限 EXAM_DIAGNOSIS_SCANNED_PDF_MAX_PAGES）')
    return PageParseResult(
        filename=name,
        page_order=page_order,
        material_type=material_type,
        subject=next((result.subject for _, result in parsed if result.subject), ''),
        ocr_text='\n\n'.join(result.ocr_text for _, result in parsed if result.ocr_text),
        ocr_confidence=min(result.ocr_confidence for _, result in parsed),
        review_required=bool(failed) or total_pages > len(images) or not questions or any(result.review_required for _, result in parsed),
        questions=questions,
        warning='；'.join(warnings),
    )


def image_data_url(content: bytes) -> str:
    """Label the image by what its bytes actually are — rendered PDF pages are PNG, phone uploads
    are usually JPEG, and some vision endpoints reject a mismatched MIME prefix."""
    mime = 'image/png' if content.startswith(b'\x89PNG') else 'image/jpeg'
    return f'data:{mime};base64,{base64.b64encode(content).decode()}'


@dataclass
class QuestionCandidate:
    question_number: str
    question_text: str
    confidence: float
    original_question_available: bool = True
    is_wrong_question: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PageParseResult:
    filename: str
    page_order: int
    material_type: str
    subject: str
    ocr_text: str
    ocr_confidence: float
    review_required: bool
    questions: list[QuestionCandidate] = field(default_factory=list)
    ocr_status: str = "completed"
    warning: str = ""
    subject_mismatch: bool = False

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["questions"] = [question.to_dict() for question in self.questions]
        return result


class LocalParser:
    """Deterministic offline parser used as the first adapter and test double.

    It intentionally does not claim to perform OCR. Real image/PDF/vision engines
    can implement the same PageParseResult contract later.
    """

    def parse_page(self, filename: str, content: str | bytes, page_order: int) -> PageParseResult:
        material_type = self.classify_material(filename)
        if isinstance(content, bytes):
            return PageParseResult(
                filename=Path(filename).name,
                page_order=page_order,
                material_type=material_type,
                subject="",
                ocr_text="",
                ocr_confidence=0.0,
                review_required=True,
                ocr_status="manual_confirmation_required",
                warning="未配置视觉解析器，无法声称已完成 OCR。请人工确认。",
            )
        text = content.strip()
        subject = "物理" if "物理" in text else ("数学" if "数学" in text or "math" in filename.lower() else "")
        questions = []
        for match in re.finditer(QUESTION_NUMBER_RE, text):
            number, question_text = match.groups()
            questions.append(QuestionCandidate(number, question_text.strip(), 0.85))
        return PageParseResult(
            filename=Path(filename).name,
            page_order=page_order,
            material_type=material_type,
            subject=subject,
            ocr_text=text,
            ocr_confidence=0.9 if text and questions else (0.4 if text else 0.0),
            review_required=not bool(text) or material_type == "unknown",
            questions=questions,
        )

    @staticmethod
    def classify_material(filename: str) -> str:
        lowered = Path(filename).name.lower()
        if any(token in lowered for token in ("answer", "答案", "solution")):
            return "answer_key"
        if any(token in lowered for token in ("student", "答题", "作答")):
            return "student_answer"
        if any(token in lowered for token in ("correct", "批改", "score")):
            return "correction"
        if any(token in lowered for token in ("paper", "试卷", "question")):
            return "paper"
        return "unknown"


class OpenAICompatibleVisionParser(LocalParser):
    DEFAULT_TIMEOUT = 60.0

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None, timeout: float | None = None, endpoint: str | None = None, on_call=None):
        self.base_url = base_url or endpoint or os.getenv('EXAM_DIAGNOSIS_VISION_BASE_URL')
        self.api_key = api_key or os.getenv('EXAM_DIAGNOSIS_VISION_API_KEY')
        self.model = model or os.getenv('EXAM_DIAGNOSIS_VISION_MODEL')
        # Vision models reading a full exam page take far longer than the 5s the first version
        # assumed; explicit argument > env > 60s default.
        if timeout is None:
            try:
                timeout = float(os.getenv('EXAM_DIAGNOSIS_VISION_TIMEOUT', self.DEFAULT_TIMEOUT))
            except ValueError:
                timeout = self.DEFAULT_TIMEOUT
        self.timeout = timeout
        # Same shape as llm.ChatClient's hook, so app.py can log vision calls into MODEL_CALLS
        # alongside the text roles (latency, success, attempts) without a second bookkeeping path.
        self.on_call = on_call

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def _record(self, **event):
        if self.on_call is None:
            return
        try:
            self.on_call({'role': 'vision', 'model': self.model, 'base_url': self.base_url, **event})
        except Exception:
            pass

    def parse_page(self, filename: str, content: str | bytes, page_order: int) -> PageParseResult:
        if not self.configured:
            return PageParseResult(filename=Path(filename).name, page_order=page_order, material_type=self.classify_material(filename), subject='', ocr_text='', ocr_confidence=0.0, review_required=True, ocr_status='manual_confirmation_required', warning='视觉解析未配置完整或调用 failed，请人工确认。')
        import time
        started = time.monotonic()
        attempts = 0
        try:
            image_url = image_data_url(content if isinstance(content, bytes) else content.encode())
            payload = {'model': self.model, 'temperature': 0, 'response_format': {'type': 'json_object'}, 'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': '请识别试卷页面，严格只返回 JSON：{"ocr_text":string,"subject":string,"questions":[{"question_number":string,"question_text":string,"confidence":number}]}'}, {'type': 'image_url', 'image_url': {'url': image_url}}]}]}
            endpoint = self.base_url.rstrip('/') + '/chat/completions'
            request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), method='POST', headers={'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'})
            last_error = None
            for attempt in range(2):
                attempts = attempt + 1
                try:
                    with urllib.request.urlopen(request, timeout=self.timeout) as response:
                        outer = json.loads(response.read().decode())
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt:
                        raise
                    time.sleep(0.05)
            else:
                raise last_error or RuntimeError('vision request failed')
            content_text = outer.get('choices', [{}])[0].get('message', {}).get('content', outer.get('ocr_text'))
            if isinstance(content_text, list):
                content_text = ''.join(part.get('text', '') for part in content_text if isinstance(part, dict) and part.get('type', 'text') == 'text')
            elif isinstance(content_text, str) and content_text.startswith('[') and content_text.endswith(']'):
                try:
                    parts = json.loads(content_text)
                    content_text = ''.join(part.get('text', '') for part in parts if isinstance(part, dict) and part.get('type', 'text') == 'text')
                except json.JSONDecodeError:
                    pass
            if isinstance(content_text, str):
                content_text = content_text.strip()
                if content_text.startswith('```'):
                    content_text = re.sub(r'^```(?:json)?\s*|\s*```$', '', content_text, flags=re.IGNORECASE).strip()
            parsed = json.loads(content_text) if isinstance(content_text, str) else content_text
            if not isinstance(parsed, dict) or not isinstance(parsed.get('ocr_text'), str) or not isinstance(parsed.get('subject'), str) or not isinstance(parsed.get('questions'), list):
                raise ValueError('vision response schema invalid')
            questions = []
            for q in parsed['questions']:
                if not isinstance(q, dict) or not isinstance(q.get('question_number'), (str, int)) or not isinstance(q.get('question_text'), str) or not isinstance(q.get('confidence'), (int, float)):
                    raise ValueError('vision question schema invalid')
                questions.append(QuestionCandidate(str(q['question_number']), q['question_text'], float(q['confidence'])))
            usage = outer.get('usage') if isinstance(outer, dict) and isinstance(outer.get('usage'), dict) else {}
            self._record(success=True, attempts=attempts, latency_ms=int((time.monotonic() - started) * 1000), error='', prompt_tokens=usage.get('prompt_tokens'), completion_tokens=usage.get('completion_tokens'), total_tokens=usage.get('total_tokens'))
            return PageParseResult(filename=Path(filename).name, page_order=page_order, material_type=self.classify_material(filename), subject=parsed.get('subject', ''), ocr_text=parsed['ocr_text'], ocr_confidence=max((q.confidence for q in questions), default=0), review_required=False, questions=questions)
        except Exception as exc:
            self._record(success=False, attempts=attempts, latency_ms=int((time.monotonic() - started) * 1000), error=f'{type(exc).__name__}: {exc}', prompt_tokens=None, completion_tokens=None, total_tokens=None)
            return PageParseResult(
                filename=Path(filename).name, page_order=page_order,
                material_type=self.classify_material(filename), subject="",
                ocr_text="", ocr_confidence=0.0, review_required=True,
                ocr_status="manual_confirmation_required",
                warning=f"视觉解析调用 failed: {exc}",
            )

    @staticmethod
    def classify_material(filename: str) -> str:
        lowered = Path(filename).name.lower()
        if any(token in lowered for token in ("answer", "答案", "solution")):
            return "answer_key"
        if any(token in lowered for token in ("student", "答题", "作答")):
            return "student_answer"
        if any(token in lowered for token in ("correct", "批改", "score")):
            return "correction"
        if any(token in lowered for token in ("paper", "试卷", "question")):
            return "paper"
        return "unknown"


class ModelTextSplitter:
    """Splits already-extracted text (PDF text layer, plain-text uploads) into question
    candidates via the `split` model role instead of the fixed-format QUESTION_NUMBER_RE regex.

    Real exam papers mix too many incompatible numbering conventions for a regex to cover
    reliably. This is the text-content counterpart to OpenAICompatibleVisionParser and follows
    the same never-fake-success rule: no client, or a failed/malformed model call, falls back to
    LocalParser's regex — but that fallback's output is always marked review_required, because a
    best-effort regex match is not the same claim as a model actually reading the page.
    """

    def __init__(self, client: Any = None):
        self.client = client

    def parse_page(self, filename: str, text: str, page_order: int) -> PageParseResult:
        material_type = LocalParser.classify_material(filename)
        if self.client is not None:
            split = self._split_with_model(text)
            if split is not None:
                questions = split['questions']
                return PageParseResult(
                    filename=Path(filename).name, page_order=page_order, material_type=material_type,
                    subject=split['subject'], ocr_text=text,
                    ocr_confidence=max((q.confidence for q in questions), default=0.0),
                    review_required=not questions or material_type == "unknown",
                    questions=questions,
                )
        fallback = LocalParser().parse_page(filename, text, page_order)
        fallback.review_required = True
        if not fallback.warning:
            fallback.warning = (
                '题目切分模型未识别出题目，退回关键词规则，请人工确认。' if not fallback.questions else
                '题目切分模型未配置或调用失败，退回关键词规则切分，请人工确认切分是否准确、完整。'
            )
        return fallback

    def _split_with_model(self, text: str) -> dict[str, Any] | None:
        system = (
            '你是试卷版面理解助手。给定一页试卷提取出的纯文本，按语义切分出每一道独立的题目。'
            '不要把同一道题内部的子问题（如(1)(2)）拆成不同的题目，也不要把选项当成题目，不要编造文本中没有的内容。'
            '严格只返回 JSON，不要输出其它文字：{"subject": 试卷科目（如数学/物理，识别不出留空字符串）, '
            '"questions": [{"question_number": 题号字符串, "question_text": 题干全文, "confidence": 0到1之间的置信度}]}'
        )
        result = self.client.complete_json(system, text[:20000])
        if not isinstance(result, dict) or not isinstance(result.get('questions'), list) or not isinstance(result.get('subject', ''), str):
            return None
        questions = []
        for item in result['questions']:
            if not isinstance(item, dict) or not isinstance(item.get('question_number'), (str, int)) or not isinstance(item.get('question_text'), str) or not isinstance(item.get('confidence'), (int, float)):
                return None
            questions.append(QuestionCandidate(str(item['question_number']), item['question_text'], float(item['confidence'])))
        return {'subject': result.get('subject', ''), 'questions': questions}
