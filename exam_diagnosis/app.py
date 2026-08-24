from datetime import datetime
from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from .parser import LocalParser
from .knowledge import match_knowledge_points

app = FastAPI(title="Exam Diagnosis", version="0.1.0")
SUBMISSIONS = {}
EXAMS = {}
ASSETS = {}
QUESTIONS = {}
RUNS = {}
UPLOAD_SESSIONS = {}
JOBS = {}
PAGES = {}
QUESTION_RECORDS = {}

class SubmissionIn(BaseModel):
    stage: str
    subject: str
    province: str
    school_grade: str
    academic_year_label: str
    is_repeating_student: bool = False
    target_exam_year: int | None = None

class ExamIn(BaseModel):
    name: str = "未命名考试"
    exam_date: str | None = None
    exam_type: str | None = None

class QuestionIn(BaseModel):
    question_number: str
    question_text: str
    is_wrong_question: bool
    original_question_available: bool
    student_answer: str | None = None

class ConfirmIn(BaseModel):
    questions: list[QuestionIn] = Field(default_factory=list)

class AnalysisIn(BaseModel):
    exam_ids: list[str]

class ParseDraftIn(BaseModel):
    questions: list[QuestionIn] = Field(default_factory=list)

class JobFailIn(BaseModel):
    stage: str
    error: str

class QuestionEditIn(BaseModel):
    question_number: str | None = None
    question_text: str | None = None
    is_wrong_question: bool | None = None
    original_question_available: bool | None = None
    student_answer: str | None = None

class OriginalLinkIn(BaseModel):
    asset_id: str

class QuestionRecordEditIn(BaseModel):
    score_obtained: float | None = None
    score_total: float | None = None
    teacher_comment: str | None = None

@app.post('/api/submissions', status_code=201)
def create_submission(body: SubmissionIn):
    if body.stage not in {'primary','middle','high'}:
        return JSONResponse({'error':'invalid_stage'}, status_code=422)
    sid=str(uuid4())
    item={'id':sid, **body.model_dump(), 'status':'subject_confirmed', 'exam_count':0, 'max_exam_count':10, 'learning_scope':{'stage':body.stage,'subject':body.subject,'province':body.province,'school_grade':body.school_grade,'academic_year_label':body.academic_year_label}}
    SUBMISSIONS[sid]=item
    return item

@app.post('/api/submissions/{sid}/exams', status_code=201)
def create_exam(sid: str, body: ExamIn):
    sub=SUBMISSIONS.get(sid)
    if not sub: return JSONResponse({'error':'submission_not_found'},status_code=404)
    if sub['exam_count'] >= 10: return JSONResponse({'error':'exam_limit_reached','max_exam_count':10},status_code=409)
    eid=str(uuid4()); item={'id':eid,'submission_id':sid,'sequence_no':sub['exam_count']+1,**body.model_dump(),'status':'uploading','questions':[]}
    EXAMS[eid]=item; sub['exam_count']+=1; return item

@app.post('/api/exams/{eid}/assets')
def upload_asset(eid: str, file: UploadFile = File(...)):
    if eid not in EXAMS: return JSONResponse({'error':'exam_not_found'},status_code=404)
    aid=str(uuid4()); data=Path('data/uploads')/eid; data.mkdir(parents=True,exist_ok=True); filename=Path(file.filename).name; path=data/filename
    path.write_bytes(file.file.read()); item={'id':aid,'exam_id':eid,'filename':filename,'storage_path':str(path),'media_type':file.content_type,'status':'received'}; ASSETS[aid]=item; return item

@app.post('/api/exams/{eid}/upload-sessions')
def create_upload_session(eid: str):
    if eid not in EXAMS:
        return JSONResponse({'error': 'exam_not_found'}, status_code=404)
    session_id = str(uuid4())
    item = {'id': session_id, 'exam_id': eid, 'status': 'uploading', 'asset_ids': []}
    UPLOAD_SESSIONS[session_id] = item
    return item

@app.post('/api/upload-sessions/{session_id}/assets', status_code=201)
def upload_session_asset(session_id: str, file: UploadFile = File(...)):
    session = UPLOAD_SESSIONS.get(session_id)
    if not session:
        return JSONResponse({'error': 'upload_session_not_found'}, status_code=404)
    if session['status'] != 'uploading':
        return JSONResponse({'error': 'upload_session_not_open'}, status_code=409)
    exam_id = session['exam_id']
    aid = str(uuid4())
    data = Path('data/uploads') / exam_id / session_id
    data.mkdir(parents=True, exist_ok=True)
    filename = Path(file.filename).name
    path = data / filename
    path.write_bytes(file.file.read())
    item = {
        'id': aid, 'exam_id': exam_id, 'upload_session_id': session_id,
        'filename': filename, 'storage_path': str(path),
        'media_type': file.content_type, 'page_order': len(session['asset_ids']) + 1,
        'material_type': 'unknown', 'ocr_status': 'pending', 'status': 'received',
    }
    ASSETS[aid] = item
    session['asset_ids'].append(aid)
    return item

@app.post('/api/upload-sessions/{session_id}/finish')
def finish_upload_session(session_id: str):
    session = UPLOAD_SESSIONS.get(session_id)
    if not session:
        return JSONResponse({'error': 'upload_session_not_found'}, status_code=404)
    if not session['asset_ids']:
        return JSONResponse({'error': 'upload_session_empty'}, status_code=422)
    session['status'] = 'processing'
    session['asset_count'] = len(session['asset_ids'])
    EXAMS[session['exam_id']]['status'] = 'parsing'
    job_id = str(uuid4())
    JOBS[job_id] = {
        'id': job_id, 'job_type': 'parse_exam', 'exam_id': session['exam_id'],
        'upload_session_id': session_id, 'status': 'queued', 'attempt': 1,
        'stages': [
            {'name': 'classify', 'status': 'queued', 'error': None},
            {'name': 'ocr', 'status': 'queued', 'error': None},
            {'name': 'question_split', 'status': 'queued', 'error': None},
        ],
    }
    session['job_id'] = job_id
    return session

@app.get('/api/jobs/{job_id}')
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return JSONResponse({'error': 'job_not_found'}, status_code=404)
    return job

@app.post('/api/jobs/{job_id}/fail')
def fail_job(job_id: str, body: JobFailIn):
    job = JOBS.get(job_id)
    if not job:
        return JSONResponse({'error': 'job_not_found'}, status_code=404)
    stage = next((item for item in job['stages'] if item['name'] == body.stage), None)
    if not stage:
        return JSONResponse({'error': 'job_stage_not_found'}, status_code=422)
    stage['status'] = 'failed'
    stage['error'] = body.error
    job['status'] = 'failed'
    job['failed_stage'] = body.stage
    job['error'] = body.error
    return job

@app.post('/api/jobs/{job_id}/retry', status_code=202)
def retry_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return JSONResponse({'error': 'job_not_found'}, status_code=404)
    if job['status'] != 'failed':
        return JSONResponse({'error': 'job_not_failed'}, status_code=409)
    failed_stage = job.get('failed_stage')
    for stage in job['stages']:
        if stage['name'] == failed_stage:
            stage['status'] = 'queued'
            stage['error'] = None
    job['attempt'] += 1
    job['status'] = 'queued'
    job['error'] = None
    return job

@app.post('/api/jobs/{job_id}/run')
def run_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return JSONResponse({'error': 'job_not_found'}, status_code=404)
    if job['status'] != 'queued':
        return JSONResponse({'error': 'job_not_queued'}, status_code=409)
    exam = EXAMS[job['exam_id']]
    for stage in job['stages']:
        stage['status'] = 'running'
        if stage['name'] == 'classify':
            counts = {'paper': 0, 'answer_key': 0, 'student_answer': 0, 'correction': 0, 'unknown': 0}
            for asset in assets_for_exam(job['exam_id']):
                material_type = classify_filename(asset['filename'])
                asset['material_type'] = material_type
                counts[material_type] += 1
            stage['result'] = {'material_counts': counts}
        elif stage['name'] == 'ocr':
            parsed_pages = []
            for asset in assets_for_exam(job['exam_id']):
                content = Path(asset['storage_path']).read_bytes()
                if asset.get('media_type', '').startswith('text/'):
                    content = content.decode('utf-8', errors='replace')
                result = LocalParser().parse_page(asset['filename'], content, asset['page_order'])
                page = {'id': str(uuid4()), 'asset_id': asset['id'], 'exam_id': job['exam_id'], **result.to_dict()}
                PAGES[page['id']] = page
                asset['ocr_status'] = 'completed'
                parsed_pages.append(page)
            stage['result'] = {'pages_processed': len(parsed_pages), 'page_ids': [page['id'] for page in parsed_pages]}
        elif stage['name'] == 'question_split':
            exam['draft_questions'] = [
                {
                    **question,
                    'id': question.get('id', str(uuid4())),
                    'confirmation_status': 'draft',
                    'is_wrong_question': question.get('is_wrong_question', True),
                    'original_question_available': question.get('original_question_available', True),
                }
                for page in pages_for_exam(job['exam_id'])
                for question in page['questions']
            ]
            stage['result'] = {'questions_detected': len(exam['draft_questions'])}
        stage['status'] = 'completed'
    job['status'] = 'completed'
    exam = EXAMS[job['exam_id']]
    exam['status'] = 'waiting_confirmation'
    return job

def classify_filename(filename: str) -> str:
    lowered = filename.lower()
    if any(token in lowered for token in ('answer', '答案', 'solution')):
        return 'answer_key'
    if any(token in lowered for token in ('student', '答题', '作答')):
        return 'student_answer'
    if any(token in lowered for token in ('correct', '批改', 'score')):
        return 'correction'
    if any(token in lowered for token in ('paper', '试卷', 'question')):
        return 'paper'
    return 'unknown'


def assets_for_exam(exam_id: str):
    return [asset for asset in ASSETS.values() if asset['exam_id'] == exam_id]

def pages_for_exam(exam_id: str):
    return [page for page in PAGES.values() if page['exam_id'] == exam_id]

@app.get('/api/exams/{eid}/pages')
def list_exam_pages(eid: str):
    if eid not in EXAMS:
        return JSONResponse({'error': 'exam_not_found'}, status_code=404)
    pages = []
    for page in sorted(pages_for_exam(eid), key=lambda item: item.get('page_order', 0)):
        pages.append({
            'id': page['id'], 'asset_id': page['asset_id'], 'exam_id': eid,
            'page_order': page.get('page_order'),
            'material_type': page.get('material_type', 'unknown'),
            'subject': page.get('subject', ''),
            'ocr_text': page.get('ocr_text', ''),
            'ocr_confidence': page.get('ocr_confidence', 0),
            'ocr_status': 'completed',
        })
    return {'exam_id': eid, 'pages': pages}

@app.get('/api/exams/{eid}/parse-draft')
def get_parse_draft(eid: str):
    exam = EXAMS.get(eid)
    if not exam:
        return JSONResponse({'error': 'exam_not_found'}, status_code=404)
    return {'exam_id': eid, 'version': exam.get('version', 1), 'status': exam['status'], 'questions': exam.get('draft_questions', exam['questions'])}

@app.patch('/api/exams/{eid}/parse-draft')
def update_parse_draft(eid: str, body: ParseDraftIn):
    exam = EXAMS.get(eid)
    if not exam:
        return JSONResponse({'error': 'exam_not_found'}, status_code=404)
    if exam['status'] == 'confirmed':
        return JSONResponse({'error': 'exam_already_confirmed'}, status_code=409)
    exam['draft_questions'] = [
        {'id': str(uuid4()), **question.model_dump(), 'confirmation_status': 'draft'}
        for question in body.questions
    ]
    exam['status'] = 'waiting_confirmation'
    return {'exam_id': eid, 'version': exam.get('version', 1), 'status': exam['status'], 'questions': exam['draft_questions']}

@app.patch('/api/exams/{eid}/parse-draft/questions/{question_index}')
def edit_parse_question(eid: str, question_index: int, body: QuestionEditIn):
    exam = EXAMS.get(eid)
    if not exam:
        return JSONResponse({'error': 'exam_not_found'}, status_code=404)
    if exam['status'] == 'confirmed':
        return JSONResponse({'error': 'exam_already_confirmed'}, status_code=409)
    questions = exam.get('draft_questions', [])
    if question_index < 0 or question_index >= len(questions):
        return JSONResponse({'error': 'question_not_found'}, status_code=404)
    question = questions[question_index]
    provenance = question.setdefault('provenance', {})
    for field, value in body.model_dump(exclude_none=True).items():
        field_provenance = provenance.setdefault(field, {
            'machine_value': question.get(field), 'user_value': None,
            'final_value': question.get(field),
        })
        field_provenance['user_value'] = value
        field_provenance['final_value'] = value
        question[field] = value
    return {'exam_id': eid, 'version': exam.get('version', 1), 'status': exam['status'], 'questions': questions}

@app.post('/api/exams/{eid}/questions/{question_id}/link-original')
def link_original_source(eid: str, question_id: str, body: OriginalLinkIn):
    exam = EXAMS.get(eid)
    asset = ASSETS.get(body.asset_id)
    if not exam or not asset or asset['exam_id'] != eid:
        return JSONResponse({'error': 'original_source_not_found'}, status_code=404)
    question = next((item for item in exam.get('draft_questions', []) if item.get('id') == question_id), None)
    if not question:
        return JSONResponse({'error': 'question_not_found'}, status_code=404)
    question['original_source'] = {'asset_id': body.asset_id, 'filename': asset['filename']}
    question['original_question_available'] = True
    return {'exam_id': eid, 'question': question, 'original_source': question['original_source']}

@app.post('/api/exams/{eid}/reopen')
def reopen_exam(eid: str):
    exam = EXAMS.get(eid)
    if not exam:
        return JSONResponse({'error': 'exam_not_found'}, status_code=404)
    if exam['status'] != 'confirmed':
        return JSONResponse({'error': 'exam_not_confirmed'}, status_code=409)
    exam['version'] = exam.get('version', 1) + 1
    exam['status'] = 'uploading'
    exam['draft_questions'] = []
    return {'id': eid, 'status': exam['status'], 'version': exam['version']}

@app.post('/api/exams/{eid}/confirm')
def confirm_exam(eid: str, body: ConfirmIn):
    exam=EXAMS.get(eid)
    if not exam: return JSONResponse({'error':'exam_not_found'},status_code=404)
    questions = body.questions or [QuestionIn(**q) for q in exam.get('draft_questions', [])]
    wrong=[q for q in questions if q.is_wrong_question]
    if not wrong or any(not q.original_question_available for q in wrong): return JSONResponse({'error':'wrong_question_and_original_required'},status_code=422)
    draft_questions = exam.get('draft_questions', [])
    if not body.questions and any(
        q.is_wrong_question and not draft_questions[index].get('original_source')
        for index, q in enumerate(questions)
    ):
        return JSONResponse({'error': 'original_source_required'}, status_code=422)
    confirmed_questions = []
    for index, question in enumerate(questions):
        item = {'id': str(uuid4()), **question.model_dump(), 'confirmation_status': 'confirmed'}
        if index < len(draft_questions) and draft_questions[index].get('original_source'):
            item['original_source'] = draft_questions[index]['original_source']
        confirmed_questions.append(item)
    exam['questions'] = confirmed_questions
    exam['status'] = 'confirmed'
    exam.setdefault('version', 1)
    exam['completeness'] = {
        'has_wrong_questions': bool(wrong),
        'has_original_sources': all(
            bool(item.get('original_source'))
            for item in confirmed_questions if item['is_wrong_question']
        ),
    }
    records = []
    for item in confirmed_questions:
        record = {
            'id': str(uuid4()), 'exam_id': eid, 'version': exam['version'],
            'question_number': item['question_number'], 'question_text': item['question_text'],
            'is_wrong_question': item['is_wrong_question'],
            'original_source': item.get('original_source'),
            'student_answer': {'text': item.get('student_answer')},
            'score': {'obtained': None, 'total': None, 'lost_points': None},
            'correction': {'teacher_comment': ''},
            'status': 'confirmed',
        }
        QUESTION_RECORDS[record['id']] = record
        records.append(record)
    exam['question_records'] = records
    return exam

@app.get('/api/exams/{eid}/question-records')
def list_question_records(eid: str):
    if eid not in EXAMS:
        return JSONResponse({'error': 'exam_not_found'}, status_code=404)
    return {'exam_id': eid, 'records': [record for record in QUESTION_RECORDS.values() if record['exam_id'] == eid]}

@app.patch('/api/question-records/{record_id}')
def update_question_record(record_id: str, body: QuestionRecordEditIn):
    record = QUESTION_RECORDS.get(record_id)
    if not record:
        return JSONResponse({'error': 'question_record_not_found'}, status_code=404)
    score = body.model_dump(exclude_none=True)
    if 'score_obtained' in score:
        record['score']['obtained'] = score['score_obtained']
    if 'score_total' in score:
        record['score']['total'] = score['score_total']
    if record['score']['obtained'] is not None and record['score']['total'] is not None:
        record['score']['lost_points'] = record['score']['total'] - record['score']['obtained']
    if 'teacher_comment' in score:
        record['correction']['teacher_comment'] = score['teacher_comment']
    return record

@app.post('/api/question-records/{record_id}/knowledge-mapping')
def map_question_knowledge(record_id: str):
    record = QUESTION_RECORDS.get(record_id)
    if not record:
        return JSONResponse({'error': 'question_record_not_found'}, status_code=404)
    exam = EXAMS[record['exam_id']]
    submission = SUBMISSIONS[exam['submission_id']]
    matches = match_knowledge_points(
        stage=submission['stage'], subject=submission['subject'],
        province=submission['province'], question_text=record['question_text'],
    )
    record['knowledge_points'] = matches
    record['knowledge_point_ids'] = [item['knowledge_id'] for item in matches]
    return {'record_id': record_id, 'knowledge_points': matches}

@app.post('/api/submissions/{sid}/analysis-runs', status_code=201)
def create_run(sid: str, body: AnalysisIn):
    if sid not in SUBMISSIONS: return JSONResponse({'error':'submission_not_found'},status_code=404)
    if not body.exam_ids or any(e not in EXAMS or EXAMS[e]['submission_id']!=sid or EXAMS[e]['status']!='confirmed' for e in body.exam_ids): return JSONResponse({'error':'exams_not_confirmed'},status_code=422)
    rid=str(uuid4()); RUNS[rid]={'id':rid,'submission_id':sid,'exam_ids':body.exam_ids,'status':'awaiting_confirmation'}; return RUNS[rid]

@app.post('/api/analysis-runs/{rid}/confirm-start', status_code=202)
def confirm_start(rid: str):
    run=RUNS.get(rid)
    if not run: return JSONResponse({'error':'run_not_found'},status_code=404)
    run['status']='queued'; return run

@app.get('/api/analysis-runs/{rid}/report')
def get_analysis_report(rid: str):
    run = RUNS.get(rid)
    if not run:
        return JSONResponse({'error': 'run_not_found'}, status_code=404)
    if run['status'] != 'queued':
        return JSONResponse({'error': 'analysis_not_started'}, status_code=409)
    records = [
        record for exam_id in run['exam_ids']
        for record in QUESTION_RECORDS.values()
        if record['exam_id'] == exam_id and record['status'] == 'confirmed'
    ]
    coverage = {}
    unmapped = []
    for record in records:
        points = record.get('knowledge_points', [])
        if not points:
            unmapped.append(record['id'])
        for point in points:
            item = coverage.setdefault(point['knowledge_id'], {
                'knowledge_id': point['knowledge_id'], 'name': point['name'],
                'chapter': point['chapter'], 'question_count': 0, 'wrong_count': 0,
            })
            item['question_count'] += 1
            item['wrong_count'] += int(record['is_wrong_question'])
    report = {
        'analysis_run_id': rid, 'status': 'report_ready',
        'wrong_question_count': sum(record['is_wrong_question'] for record in records),
        'question_count': len(records), 'knowledge_coverage': list(coverage.values()),
        'unmapped_question_ids': unmapped,
        'source_question_ids': [record['id'] for record in records],
    }
    run['status'] = 'report_ready'
    run['report'] = report
    return report
