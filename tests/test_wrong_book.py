"""The wrong-question book: what it collects, how it filters, and the two printable editions."""
import io

import pymupdf
from docx import Document
from fastapi.testclient import TestClient

import exam_diagnosis.app as app_module
from exam_diagnosis.wrong_book import PRACTICE_NOTE, REVIEW_NOTE, build_wrong_book, wrong_book_blocks

SUBMISSION = {'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'}


def _exam(eid, seq, name, date=None):
    return {'id': eid, 'submission_id': 's', 'sequence_no': seq, 'name': name, 'exam_date': date}


def _record(rid, exam_id, number, text, wrong=True, answer='', knowledge=('k1',), comment=''):
    return {
        'id': rid, 'exam_id': exam_id, 'status': 'confirmed', 'question_number': number,
        'question_text': text, 'is_wrong_question': wrong,
        'student_answer': {'text': answer}, 'correction': {'teacher_comment': comment},
        'score': {'obtained': None, 'total': None},
        'knowledge_points': [{'knowledge_id': k, 'name': f'点{k}', 'chapter': '函数'} for k in knowledge],
    }


EXAMS = [_exam('e1', 1, '9月月考', '2026-09-01'), _exam('e2', 2, '期中', '2026-11-01')]
RECORDS = {
    'e1': [_record('r1', 'e1', '10', '第十题', answer='错的'), _record('r2', 'e1', '2', '第二题'), _record('r3', 'e1', '3', '做对了', wrong=False)],
    'e2': [_record('r4', 'e2', '1', '期中第一题', knowledge=('k2',))],
}
REPORTS = [{
    'analysis_run_id': 'run1',
    'wrong_question_collection': [
        {'question_id': 'r1', 'root_cause': '计算错误', 'explanation': '符号搞错', 'avoidance_advice': '重算一遍'},
        {'question_id': 'r2', 'root_cause': '条件遗漏', 'explanation': '', 'avoidance_advice': '先列条件'},
    ],
}]


# --- collecting ------------------------------------------------------------------------------------


def test_book_is_newest_exam_first_and_questions_numerically_ordered():
    book = build_wrong_book(EXAMS, RECORDS, REPORTS)
    assert [item['question_id'] for item in book['items']] == ['r4', 'r2', 'r1']  # e2 first; 2 before 10
    assert book['summary'] == {'total': 3, 'analysed': 2, 'exam_count': 2}


def test_correct_answers_are_not_in_the_book():
    assert 'r3' not in {item['question_id'] for item in build_wrong_book(EXAMS, RECORDS, REPORTS)['items']}


def test_unanalysed_questions_are_included_with_empty_diagnosis():
    item = next(i for i in build_wrong_book(EXAMS, RECORDS, REPORTS)['items'] if i['question_id'] == 'r4')
    assert item['analysed'] is False
    assert item['root_cause'] == '' and item['explanation'] == '' and item['avoidance_advice'] == ''
    analysed = next(i for i in build_wrong_book(EXAMS, RECORDS, REPORTS)['items'] if i['question_id'] == 'r1')
    assert analysed['analysed'] is True and analysed['root_cause'] == '计算错误' and analysed['analysis_run_id'] == 'run1'
    assert analysed['student_answer'] == '错的'


def test_a_later_report_supersedes_an_earlier_verdict():
    reports = REPORTS + [{'analysis_run_id': 'run2', 'wrong_question_collection': [{'question_id': 'r1', 'root_cause': '概念不清', 'explanation': '', 'avoidance_advice': ''}]}]
    item = next(i for i in build_wrong_book(EXAMS, RECORDS, reports)['items'] if i['question_id'] == 'r1')
    assert item['root_cause'] == '概念不清' and item['analysis_run_id'] == 'run2'


# --- filtering -------------------------------------------------------------------------------------


def test_filters_narrow_by_knowledge_exam_and_cause():
    by_knowledge = build_wrong_book(EXAMS, RECORDS, REPORTS, knowledge_id='k2')
    assert [i['question_id'] for i in by_knowledge['items']] == ['r4']
    assert by_knowledge['filters']['knowledge_id'] == 'k2'
    by_exam = build_wrong_book(EXAMS, RECORDS, REPORTS, exam_id='e1')
    assert [i['question_id'] for i in by_exam['items']] == ['r2', 'r1']
    by_cause = build_wrong_book(EXAMS, RECORDS, REPORTS, root_cause='计算错误')
    assert [i['question_id'] for i in by_cause['items']] == ['r1']
    assert build_wrong_book(EXAMS, RECORDS, REPORTS, root_cause='不存在的错因')['items'] == []


def test_facets_describe_only_what_the_book_actually_contains():
    facets = build_wrong_book(EXAMS, RECORDS, REPORTS)['facets']
    assert [f['exam_id'] for f in facets['exams']] == ['e2', 'e1']
    assert {f['exam_id']: f['count'] for f in facets['exams']} == {'e1': 2, 'e2': 1}
    assert {f['knowledge_id']: f['count'] for f in facets['knowledge_points']} == {'k1': 2, 'k2': 1}
    assert {f['root_cause'] for f in facets['root_causes']} == {'计算错误', '条件遗漏'}
    # facets ignore the current filter, so the user can always widen again
    assert build_wrong_book(EXAMS, RECORDS, REPORTS, exam_id='e1')['facets']['exams'] == facets['exams']


# --- printable editions -------------------------------------------------------------------------------


def test_review_edition_carries_the_diagnosis():
    blocks = wrong_book_blocks(build_wrong_book(EXAMS, RECORDS, REPORTS))
    flat = ' '.join(str(b) for b in blocks)
    assert REVIEW_NOTE in flat
    assert '上次作答：错的' in flat and '错因：计算错误' in flat and '规避建议：重算一遍' in flat
    assert '9月月考（2026-09-01）' in flat  # exams get dated headings
    assert '这道题还没有经过分析' in flat  # the unanalysed one says so rather than looking complete


def test_practice_edition_withholds_the_answer_and_the_diagnosis():
    blocks = wrong_book_blocks(build_wrong_book(EXAMS, RECORDS, REPORTS), practice=True)
    flat = ' '.join(str(b) for b in blocks)
    assert PRACTICE_NOTE in flat
    assert '第十题' in flat and '期中第一题' in flat  # stems are there to redo
    assert '错的' not in flat and '计算错误' not in flat and '重算一遍' not in flat
    assert '解答：' in flat


def test_both_editions_render_to_pdf_and_word():
    from exam_diagnosis.export import render_blocks_docx, render_blocks_pdf
    book = build_wrong_book(EXAMS, RECORDS, REPORTS)
    pdf = render_blocks_pdf(wrong_book_blocks(book))
    assert pdf.startswith(b'%PDF')
    text = ''.join(page.get_text() for page in pymupdf.open(stream=pdf, filetype='pdf'))
    assert '错题本' in text and '第十题' in text and '计算错误' in text
    practice_pdf = render_blocks_pdf(wrong_book_blocks(book, practice=True))
    practice_text = ''.join(page.get_text() for page in pymupdf.open(stream=practice_pdf, filetype='pdf'))
    assert '第十题' in practice_text and '计算错误' not in practice_text
    docx = render_blocks_docx(wrong_book_blocks(book))
    assert any('第十题' in p.text for p in Document(io.BytesIO(docx)).paragraphs)


def test_empty_book_still_renders():
    book = build_wrong_book([], {})
    assert book['items'] == [] and book['summary']['total'] == 0
    blocks = wrong_book_blocks(book)
    assert blocks[0] == ('heading', 1, '错题本')


# --- API ---------------------------------------------------------------------------------------------


def _seed(client):
    submission = client.post('/api/submissions', json=SUBMISSION).json()
    exam = client.post(f"/api/submissions/{submission['id']}/exams", json={'name': '月考'}).json()
    client.post(f"/api/exams/{exam['id']}/confirm", json={'questions': [
        {'question_number': '1', 'question_text': '求函数的定义域', 'is_wrong_question': True, 'original_question_available': True, 'student_answer': 'x>0'},
        {'question_number': '2', 'question_text': '判断单调性', 'is_wrong_question': False, 'original_question_available': True},
    ]})
    return submission, exam


def test_endpoint_lists_wrong_questions_with_knowledge_tags():
    client = TestClient(app_module.app)
    submission, exam = _seed(client)
    body = client.get(f"/api/submissions/{submission['id']}/wrong-questions").json()
    assert body['summary']['total'] == 1
    item = body['items'][0]
    assert item['question_text'] == '求函数的定义域' and item['student_answer'] == 'x>0'
    assert item['analysed'] is False  # no report generated yet
    # knowledge mapping is applied lazily by the endpoint, so filters work before any analysis
    assert any(p['knowledge_id'] == 'high.math.function.domain' for p in item['knowledge_points'])
    filtered = client.get(f"/api/submissions/{submission['id']}/wrong-questions", params={'knowledge_id': 'high.math.function.domain'}).json()
    assert filtered['summary']['total'] == 1
    assert client.get(f"/api/submissions/{submission['id']}/wrong-questions", params={'exam_id': 'nope'}).json()['summary']['total'] == 0


def test_endpoint_picks_up_the_diagnosis_once_a_report_exists():
    client = TestClient(app_module.app)
    submission, exam = _seed(client)
    run = client.post(f"/api/submissions/{submission['id']}/analysis-runs", json={'exam_ids': [exam['id']]}).json()
    client.post(f"/api/analysis-runs/{run['id']}/confirm-start")
    client.get(f"/api/analysis-runs/{run['id']}/report")
    item = client.get(f"/api/submissions/{submission['id']}/wrong-questions").json()['items'][0]
    assert item['analysed'] is True and item['root_cause'] and item['analysis_run_id'] == run['id']


def test_export_serves_both_editions_and_rejects_bad_arguments():
    client = TestClient(app_module.app)
    submission, _ = _seed(client)
    base = f"/api/submissions/{submission['id']}/wrong-questions/export"
    pdf = client.get(base, params={'format': 'pdf', 'edition': 'review'})
    assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF')
    assert 'wrong-book-review' in pdf.headers['content-disposition']
    text = ''.join(page.get_text() for page in pymupdf.open(stream=pdf.content, filetype='pdf'))
    assert '求函数的定义域' in text and 'x>0' in text
    practice = client.get(base, params={'format': 'pdf', 'edition': 'practice'})
    practice_text = ''.join(page.get_text() for page in pymupdf.open(stream=practice.content, filetype='pdf'))
    assert '求函数的定义域' in practice_text and 'x>0' not in practice_text
    docx = client.get(base, params={'format': 'docx'})
    assert docx.status_code == 200 and docx.content.startswith(b'PK')
    assert client.get(base, params={'format': 'xlsx'}).status_code == 422
    assert client.get(base, params={'edition': 'cheatsheet'}).status_code == 422
    assert client.get('/api/submissions/nope/wrong-questions/export').status_code == 404


def test_wrong_book_is_private_to_its_owner():
    client = TestClient(app_module.app)
    submission, _ = _seed(client)
    other = TestClient(app_module.app, headers={'X-User-Id': 'someone-else'})
    assert other.get(f"/api/submissions/{submission['id']}/wrong-questions").status_code == 404
    assert other.get(f"/api/submissions/{submission['id']}/wrong-questions/export").status_code == 404
