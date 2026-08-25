from pathlib import Path
from exam_diagnosis.parser import extract_pdf_text


def test_pdf_text_extraction_returns_manual_confirmation_when_dependency_missing(tmp_path):
    pdf = tmp_path / 'paper.pdf'
    pdf.write_bytes(b'%PDF-not-a-real-pdf')
    result = extract_pdf_text(pdf)
    assert result['status'] == 'manual_confirmation_required'
    assert result['text'] == ''
    assert '未能' in result['warning']
