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
    try:
        import fitz
        document = fitz.open(str(path))
        text = '\n'.join(page.get_text() for page in document).strip()
        return {'status': 'completed', 'text': text, 'warning': ''}
    except Exception as exc:
        return {'status': 'manual_confirmation_required', 'text': '', 'warning': f'未能提取 PDF 文本，请人工确认：{exc}'}


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
    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None, timeout: float = 5.0, endpoint: str | None = None):
        self.base_url = base_url or endpoint or os.getenv('EXAM_DIAGNOSIS_VISION_BASE_URL')
        self.api_key = api_key or os.getenv('EXAM_DIAGNOSIS_VISION_API_KEY')
        self.model = model or os.getenv('EXAM_DIAGNOSIS_VISION_MODEL')
        self.timeout = timeout

    def parse_page(self, filename: str, content: str | bytes, page_order: int) -> PageParseResult:
        if not self.base_url or not self.api_key or not self.model:
            return PageParseResult(filename=Path(filename).name, page_order=page_order, material_type=self.classify_material(filename), subject='', ocr_text='', ocr_confidence=0.0, review_required=True, ocr_status='manual_confirmation_required', warning='视觉解析未配置完整或调用 failed，请人工确认。')
        try:
            encoded = base64.b64encode(content if isinstance(content, bytes) else content.encode()).decode()
            payload = {'model': self.model, 'temperature': 0, 'response_format': {'type': 'json_object'}, 'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': '请识别试卷页面，严格只返回 JSON：{"ocr_text":string,"subject":string,"questions":[{"question_number":string,"question_text":string,"confidence":number}]}'}, {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{encoded}'}}]}]}
            endpoint = self.base_url.rstrip('/') + '/chat/completions'
            request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), method='POST', headers={'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'})
            last_error = None
            for attempt in range(2):
                try:
                    with urllib.request.urlopen(request, timeout=self.timeout) as response:
                        outer = json.loads(response.read().decode())
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt:
                        raise
                    import time
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
            return PageParseResult(filename=Path(filename).name, page_order=page_order, material_type=self.classify_material(filename), subject=parsed.get('subject', ''), ocr_text=parsed['ocr_text'], ocr_confidence=max((q.confidence for q in questions), default=0), review_required=False, questions=questions)
        except Exception as exc:
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
