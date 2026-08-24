import json
from pathlib import Path

from exam_diagnosis.parser import LocalParser, PageParseResult


def test_parser_classifies_material_and_extracts_question_candidates_from_fixture():
    parser = LocalParser()
    result = parser.parse_page(
        filename="math-paper-page-1.txt",
        content="数学 第17题 函数 f(x)=x^2，求定义域。\n第18题 已知数列，求通项。",
        page_order=1,
    )
    assert isinstance(result, PageParseResult)
    assert result.material_type == "paper"
    assert result.subject == "数学"
    assert result.ocr_text.startswith("数学 第17题")
    assert [q.question_number for q in result.questions] == ["17", "18"]
    assert result.questions[0].confidence > 0


def test_parser_keeps_unknown_image_as_low_confidence_review_item():
    result = LocalParser().parse_page(
        filename="IMG_001.jpg",
        content=b"binary-image-placeholder",
        page_order=3,
    )
    assert result.material_type == "unknown"
    assert result.review_required is True
    assert result.ocr_text == ""
    assert result.questions == []


def test_parser_result_is_serializable():
    result = LocalParser().parse_page(
        filename="answer-key.txt",
        content="数学答案 第17题：定义域为全体实数",
        page_order=2,
    )
    payload = result.to_dict()
    assert json.loads(json.dumps(payload))["material_type"] == "answer_key"
    assert payload["page_order"] == 2
