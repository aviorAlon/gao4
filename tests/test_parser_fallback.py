from exam_diagnosis.parser import LocalParser, ManualConfirmationRequired, OpenAICompatibleVisionParser


def test_binary_image_without_configured_vision_requires_manual_confirmation():
    result = LocalParser().parse_page('page.jpg', b'image', 1)
    assert result.review_required is True
    assert result.ocr_status == 'manual_confirmation_required'


def test_vision_parser_failure_explicitly_downgrades_to_manual_confirmation():
    parser = OpenAICompatibleVisionParser(endpoint='http://127.0.0.1:1')
    result = parser.parse_page('page.jpg', b'image', 1)
    assert result.review_required is True
    assert result.ocr_status == 'manual_confirmation_required'
    assert 'failed' in result.warning
