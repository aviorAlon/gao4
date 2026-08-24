from dataclasses import asdict, dataclass, field
from pathlib import Path
import re
from typing import Any


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
            )
        text = content.strip()
        subject = "数学" if "数学" in text or "math" in filename.lower() else ""
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
            ocr_confidence=0.9 if text else 0.0,
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
