from dataclasses import asdict, dataclass, field
import base64
import json
import os
from pathlib import Path
import re
import urllib.request
import urllib.error
from typing import Any


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
        for match in re.finditer(r"(?:第\s*)?(\d{1,3})\s*题\s*([^\n]+)", text):
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


class ManualConfirmationRequired(Exception):
    pass


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
        return super().parse_page(filename, content, page_order)

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
