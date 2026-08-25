from pathlib import Path
from exam_diagnosis.parser import LocalParser, extract_pdf_text


def test_pdf_job_parser_uses_real_pdf_text():
    import fitz
    path = Path('tests/fixtures-real.pdf')
    path.parent.mkdir(exist_ok=True)
    doc = fitz.open(); page = doc.new_page(); page.insert_text((72,72), 'Math Question 17 Function Domain'); doc.save(path); doc.close()
    try:
        parsed = extract_pdf_text(path)
        assert parsed['status'] == 'completed'
        assert 'Function Domain' in parsed['text']
    finally:
        path.unlink(missing_ok=True)


def test_local_text_parser_remains_text_path():
    assert LocalParser().parse_page('paper.txt', 'Math Question 17 Function Domain', 1).ocr_status == 'completed'
