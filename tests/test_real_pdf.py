from pathlib import Path
import fitz
from exam_diagnosis.parser import extract_pdf_text


def test_real_pdf_text_is_extracted_successfully(tmp_path):
    path = tmp_path / 'fixture.pdf'
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), 'Math Question 17 Function Domain')
    document.save(path)
    document.close()
    result = extract_pdf_text(path)
    assert result['status'] == 'completed'
    assert 'Function Domain' in result['text']
