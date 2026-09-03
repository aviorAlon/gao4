from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
import re
import hashlib
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from .parser import ModelTextSplitter, OpenAICompatibleVisionParser, extract_pdf_text
from .knowledge import match_knowledge_points
from .diagnosis import diagnose_records
from .storage import SQLiteMapping
from .knowledge import KnowledgePartitionNotFound, available_partitions, find_partition
from .review import APPROVED, NEEDS_REVIEW, review_report, withhold_unreviewed_answer
from .llm import client_from_env
from .reporting import generate_report_summary
from .env_file import load_env_file

# Loaded once at import time, before anything below reads os.environ — lets model-role API keys
# live in a gitignored .env.local file instead of needing `$env:...` set by hand every session.
# Real environment variables still win if both are set (see env_file.load_env_file).
load_env_file()

LOCAL_USER_ID = 'local'
USER_HEADER = 'X-User-Id'

def current_user_id(request: Request) -> str:
    """MVP runs as a single local user, but every resource still carries its owner."""
    return request.headers.get(USER_HEADER) or LOCAL_USER_ID

SUBMISSIONS = SQLiteMapping('submissions')
EXAMS = SQLiteMapping('exams')
ASSETS = SQLiteMapping('assets')
RUNS = SQLiteMapping('runs')
UPLOAD_SESSIONS = SQLiteMapping('upload_sessions')
JOBS = SQLiteMapping('jobs')
PAGES = SQLiteMapping('pages')
QUESTION_RECORDS = SQLiteMapping('question_records')
VERIFICATION_QUESTIONS = SQLiteMapping('verification_questions')
MASTERY_RECORDS = SQLiteMapping('mastery_records')
MODEL_CALLS = SQLiteMapping('model_calls')

_PERSISTENT_MAPPINGS = (SUBMISSIONS, EXAMS, ASSETS, RUNS, UPLOAD_SESSIONS, JOBS, PAGES, QUESTION_RECORDS, VERIFICATION_QUESTIONS, MASTERY_RECORDS, MODEL_CALLS)

MODEL_CALLS_MAX = 500


def record_model_call(user_id: str | None = None, **context: Any):
    """Bind a llm.ChatClient.on_call callback that logs into MODEL_CALLS.

    Every call is logged regardless of success — a string of failures is exactly the thing
    observability exists to surface, not something to hide by only recording the good ones.
    `context` carries whatever identifies what the call was for (exam_id, run_id, ...); it costs
    nothing when nobody looks at it and saves a log-diving session when something needs debugging.
    """
    def on_call(record: dict[str, Any]) -> None:
        call_id = str(uuid4())
        MODEL_CALLS[call_id] = {
            'id': call_id, 'user_id': user_id or LOCAL_USER_ID,
            'created_at': datetime.utcnow().isoformat() + 'Z',
            **context, **record,
        }
        # Bound the log instead of growing the table forever — this is a debugging aid, not an
        # audit trail with retention requirements, so trimming the oldest rows is the right call.
        if len(MODEL_CALLS) > MODEL_CALLS_MAX:
            oldest = sorted(MODEL_CALLS.values(), key=lambda item: item['created_at'])[:len(MODEL_CALLS) - MODEL_CALLS_MAX]
            for item in oldest:
                del MODEL_CALLS[item['id']]
    return on_call

OWNED_BY_PATH_PARAM = {
    'sid': SUBMISSIONS, 'eid': EXAMS, 'asset_id': ASSETS, 'rid': RUNS,
    'session_id': UPLOAD_SESSIONS, 'job_id': JOBS, 'page_id': PAGES,
    'record_id': QUESTION_RECORDS, 'question_id': VERIFICATION_QUESTIONS,
}

def enforce_resource_owner(request: Request, user_id: str = Depends(current_user_id)):
    """One chokepoint for the isolation boundary, so no route can forget to check it."""
    for name, value in request.path_params.items():
        entity = OWNED_BY_PATH_PARAM.get(name, {}).get(value) if name in OWNED_BY_PATH_PARAM else None
        if entity and entity.get('user_id', LOCAL_USER_ID) != user_id:
            raise HTTPException(status_code=404, detail={'error': 'resource_not_found'})

app = FastAPI(title="Exam Diagnosis", version="0.1.0", dependencies=[Depends(enforce_resource_owner)])

# Frontend now lives in its own project (frontend/, React + Vite) instead of the inline HTML this
# app used to serve — it calls this API from a different origin (Vite's dev server port, and later
# whatever origin the packaged desktop client uses), so the browser needs CORS to allow it. This
# is a local single-user MVP behind no auth beyond X-User-Id, so the practical exposure of a wide
# localhost allowlist is low; tighten this before this ever listens on more than 127.0.0.1.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r'https?://(localhost|127\.0\.0\.1)(:\d+)?',
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

MIN_ACADEMIC_YEAR_RULE = '学年范围形如 25-26，且至少覆盖一整学年'

def parse_academic_year_range(label: str) -> dict | None:
    """A formal diagnosis needs at least one full academic year; longer spans stay legal."""
    match = re.fullmatch(r'(\d{2}|\d{4})-(\d{2}|\d{4})', label or '')
    if not match or len(match.group(1)) != len(match.group(2)):
        return None
    start, end = int(match.group(1)), int(match.group(2))
    if end - start < 1:
        return None
    return {'academic_year_start': start, 'academic_year_end': end, 'academic_year_span': end - start}

def safe_remove_asset_file(asset: dict):
    path = Path(asset.get('storage_path', '')).resolve()
    allowed_root = (Path.cwd() / 'data' / 'uploads').resolve()
    try:
        path.relative_to(allowed_root)
    except ValueError:
        return False
    if path.is_file():
        path.unlink()
    parent = path.parent
    while parent != allowed_root and parent.is_dir():
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
    return True

@app.middleware('http')
async def persist_state_after_request(request: Request, call_next):
    response = await call_next(request)
    for mapping in _PERSISTENT_MAPPINGS:
        mapping.flush()
    return response

class SubmissionIn(BaseModel):
    stage: str
    subject: str
    province: str
    school_grade: str
    academic_year_label: str
    curriculum_year: int | None = None
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
    original_source: dict | None = None

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

class PageEditIn(BaseModel):
    page_order: int | None = None
    material_type: str | None = None
    include_in_analysis: bool | None = None

class AssetEditIn(BaseModel):
    page_order: int | None = None
    material_type: str | None = None

class VerificationAttemptIn(BaseModel):
    answer: str
    score: float
    max_score: float
    duration_seconds: int = 0

class VerificationReviewIn(BaseModel):
    approved: bool
    answer: str | None = None
    note: str = ''

@app.post('/api/submissions', status_code=201)
def create_submission(body: SubmissionIn, user_id: str = Depends(current_user_id)):
    if body.stage not in {'primary','middle','high'}:
        return JSONResponse({'error':'invalid_stage'}, status_code=422)
    span = parse_academic_year_range(body.academic_year_label)
    if not span:
        return JSONResponse({'error': 'invalid_academic_year', 'rule': MIN_ACADEMIC_YEAR_RULE}, status_code=422)
    partition = find_partition(body.stage, body.subject, body.province, body.curriculum_year)
    if partition is None:
        return JSONResponse({'error': 'knowledge_partition_not_found', 'requested': {'stage': body.stage, 'subject': body.subject, 'province': body.province, 'curriculum_year': body.curriculum_year}, 'available_partitions': available_partitions()}, status_code=422)
    if not partition.covers_grade(body.school_grade):
        return JSONResponse({'error': 'grade_outside_partition_scope', 'school_grade': body.school_grade, 'academic_year_range': list(partition.academic_year_range)}, status_code=422)
    sid=str(uuid4())
    item={'id':sid, 'user_id': user_id, **body.model_dump(), **span, 'curriculum_year': partition.curriculum_year, 'status':'subject_confirmed', 'exam_count':0, 'max_exam_count':10, 'knowledge_partition': partition.describe(), 'learning_scope':{'stage':body.stage,'subject':body.subject,'province':body.province,'school_grade':body.school_grade,'academic_year_label':body.academic_year_label, **span}}
    SUBMISSIONS[sid]=item
    return item

@app.get('/api/knowledge-partitions')
def list_knowledge_partitions():
    return {'partitions': available_partitions()}

@app.get('/api/submissions/{sid}')
def get_submission(sid: str):
    item = SUBMISSIONS.get(sid)
    if not item:
        return JSONResponse({'error': 'submission_not_found'}, status_code=404)
    return item

@app.get('/api/submissions/{sid}/export')
def export_submission(sid: str):
    submission = SUBMISSIONS.get(sid)
    if not submission:
        return JSONResponse({'error': 'submission_not_found'}, status_code=404)
    exam_ids = [item['id'] for item in EXAMS.values() if item['submission_id'] == sid]
    partition = find_partition(submission['stage'], submission['subject'], submission['province'], submission.get('curriculum_year'))
    knowledge_version = partition.version if partition else None
    return {'submission': submission, 'knowledge_partition': partition.describe() if partition else None, 'exams': [EXAMS[eid] for eid in exam_ids], 'assets': [item for item in ASSETS.values() if item['exam_id'] in exam_ids], 'pages': [item for item in PAGES.values() if item['exam_id'] in exam_ids], 'question_records': [item for item in QUESTION_RECORDS.values() if item['exam_id'] in exam_ids], 'analysis_runs': [item for item in RUNS.values() if item['submission_id'] == sid], 'verification_questions': [item for item in VERIFICATION_QUESTIONS.values() if item['submission_id'] == sid], 'mastery_records': [item for item in MASTERY_RECORDS.values() if item['submission_id'] == sid], 'versions': {'export_schema': '1.0.0', 'knowledge_base': knowledge_version, 'application': app.version}}

@app.delete('/api/submissions/{sid}')
def delete_submission(sid: str):
    if sid not in SUBMISSIONS:
        return JSONResponse({'error': 'submission_not_found'}, status_code=404)
    exam_ids = [item['id'] for item in EXAMS.values() if item['submission_id'] == sid]
    for asset in [item for item in ASSETS.values() if item.get('exam_id') in exam_ids]:
        safe_remove_asset_file(asset)
    for mapping in (QUESTION_RECORDS, PAGES, ASSETS):
        for key, item in list(mapping.items()):
            if item.get('exam_id') in exam_ids:
                del mapping[key]
    for eid in exam_ids:
        del EXAMS[eid]
    for mapping in (RUNS, VERIFICATION_QUESTIONS, MASTERY_RECORDS):
        for key, item in list(mapping.items()):
            if item.get('submission_id') == sid:
                del mapping[key]
    del SUBMISSIONS[sid]
    return {'deleted': True, 'submission_id': sid}

@app.get('/api/submissions')
def list_submissions(user_id: str = Depends(current_user_id)):
    return [item for item in SUBMISSIONS.values() if item.get('user_id', LOCAL_USER_ID) == user_id]

@app.post('/api/submissions/{sid}/exams', status_code=201)
def create_exam(sid: str, body: ExamIn):
    sub=SUBMISSIONS.get(sid)
    if not sub: return JSONResponse({'error':'submission_not_found'},status_code=404)
    if sub['exam_count'] >= 10: return JSONResponse({'error':'exam_limit_reached','max_exam_count':10},status_code=409)
    eid=str(uuid4()); item={'id':eid,'submission_id':sid,'user_id':sub.get('user_id',LOCAL_USER_ID),'sequence_no':sub['exam_count']+1,**body.model_dump(),'status':'uploading','questions':[]}
    EXAMS[eid]=item; sub['exam_count']+=1; SUBMISSIONS[sid] = sub; return item

@app.get('/api/submissions/{sid}/exams')
def list_submission_exams(sid: str):
    if sid not in SUBMISSIONS:
        return JSONResponse({'error': 'submission_not_found'}, status_code=404)
    return [exam for exam in EXAMS.values() if exam['submission_id'] == sid]

@app.post('/api/exams/{eid}/assets')
def upload_asset(eid: str, file: UploadFile = File(...)):
    if eid not in EXAMS: return JSONResponse({'error':'exam_not_found'},status_code=404)
    aid=str(uuid4()); data=Path('data/uploads')/eid; data.mkdir(parents=True,exist_ok=True); filename=Path(file.filename).name; path=data/filename
    path.write_bytes(file.file.read()); item={'id':aid,'exam_id':eid,'user_id':EXAMS[eid].get('user_id',LOCAL_USER_ID),'filename':filename,'storage_path':str(path),'media_type':file.content_type,'status':'received'}; ASSETS[aid]=item; return item

@app.get('/api/exams/{eid}/assets')
def list_exam_assets(eid: str):
    if eid not in EXAMS:
        return JSONResponse({'error': 'exam_not_found'}, status_code=404)
    return {'exam_id': eid, 'assets': assets_for_exam(eid)}

@app.patch('/api/assets/{asset_id}')
def update_asset(asset_id: str, body: AssetEditIn):
    asset = ASSETS.get(asset_id)
    if not asset:
        return JSONResponse({'error': 'asset_not_found'}, status_code=404)
    changes = body.model_dump(exclude_none=True)
    if changes.get('page_order', 1) < 1:
        return JSONResponse({'error': 'invalid_page_order'}, status_code=422)
    if 'material_type' in changes and changes['material_type'] not in {'paper','answer_key','student_answer','correction','unknown'}:
        return JSONResponse({'error': 'invalid_material_type'}, status_code=422)
    asset.update(changes); ASSETS[asset_id] = asset
    for page in PAGES.values():
        if page['asset_id'] == asset_id:
            page.update(changes); PAGES[page['id']] = page
    return asset

@app.delete('/api/assets/{asset_id}')
def delete_asset(asset_id: str):
    asset = ASSETS.get(asset_id)
    if not asset:
        return JSONResponse({'error': 'asset_not_found'}, status_code=404)
    safe_remove_asset_file(asset)
    for page_id, page in list(PAGES.items()):
        if page['asset_id'] == asset_id:
            del PAGES[page_id]
    del ASSETS[asset_id]
    return {'deleted': True, 'asset_id': asset_id}

@app.post('/api/exams/{eid}/upload-sessions')
def create_upload_session(eid: str):
    if eid not in EXAMS:
        return JSONResponse({'error': 'exam_not_found'}, status_code=404)
    session_id = str(uuid4())
    item = {'id': session_id, 'exam_id': eid, 'user_id': EXAMS[eid].get('user_id', LOCAL_USER_ID), 'status': 'uploading', 'asset_ids': []}
    UPLOAD_SESSIONS[session_id] = item
    return item

@app.post('/api/upload-sessions/{session_id}/assets', status_code=201)
def upload_session_asset(session_id: str, file: UploadFile = File(...)):
    session = UPLOAD_SESSIONS.get(session_id)
    if not session:
        return JSONResponse({'error': 'upload_session_not_found'}, status_code=404)
    if session['status'] != 'uploading':
        return JSONResponse({'error': 'upload_session_not_open'}, status_code=409)
    filename = Path(file.filename or '').name
    if not filename:
        return JSONResponse({'error': 'empty_filename'}, status_code=422)
    allowed = {'image/jpeg', 'image/png', 'image/webp', 'application/pdf', 'text/plain'}
    if file.content_type not in allowed:
        return JSONResponse({'error': 'unsupported_media_type', 'allowed': sorted(allowed)}, status_code=415)
    content = file.file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        return JSONResponse({'error': 'file_too_large', 'max_bytes': 10 * 1024 * 1024}, status_code=413)
    digest = hashlib.sha256(content).hexdigest()
    duplicate = next((asset for asset in ASSETS.values() if asset['exam_id'] == session['exam_id'] and asset.get('sha256') == digest), None)
    if duplicate:
        return JSONResponse({'error': 'duplicate_asset', 'duplicate_asset_id': duplicate['id'], 'duplicate_filename': duplicate['filename'], 'sha256': digest}, status_code=409)
    exam_id = session['exam_id']
    aid = str(uuid4())
    data = Path('data/uploads') / exam_id / session_id
    data.mkdir(parents=True, exist_ok=True)
    path = data / filename
    path.write_bytes(content)
    item = {
        'id': aid, 'exam_id': exam_id, 'upload_session_id': session_id,
        'user_id': session.get('user_id', LOCAL_USER_ID),
        'filename': filename, 'storage_path': str(path),
        'media_type': file.content_type, 'page_order': len(session['asset_ids']) + 1,
        'material_type': 'unknown', 'ocr_status': 'pending', 'status': 'received', 'sha256': digest,
    }
    ASSETS[aid] = item
    session['asset_ids'].append(aid)
    UPLOAD_SESSIONS[session_id] = session
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
        'user_id': session.get('user_id', LOCAL_USER_ID),
        'upload_session_id': session_id, 'status': 'queued', 'attempt': 1,
        'stages': [
            {'name': 'classify', 'status': 'queued', 'error': None},
            {'name': 'ocr', 'status': 'queued', 'error': None},
            {'name': 'question_split', 'status': 'queued', 'error': None},
        ],
    }
    session['job_id'] = job_id
    UPLOAD_SESSIONS[session_id] = session
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
    current_stage = None
    try:
      for stage in job['stages']:
        current_stage = stage
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
            user_decisions = {}
            for stale in pages_for_exam(job['exam_id']):
                if stale.get('inclusion_decided_by') == 'user':
                    user_decisions[stale['asset_id']] = (stale['include_in_analysis'], stale['exclusion_reason'])
                del PAGES[stale['id']]
            for asset in assets_for_exam(job['exam_id']):
                content = Path(asset['storage_path']).read_bytes()
                if asset.get('media_type', '') == 'application/pdf' or asset['filename'].lower().endswith('.pdf'):
                    pdf_result = extract_pdf_text(asset['storage_path'])
                    content = pdf_result['text'] if pdf_result['status'] == 'completed' else b''
                    asset['ocr_warning'] = pdf_result.get('warning', '')
                elif asset.get('media_type', '').startswith('text/'):
                    content = content.decode('utf-8', errors='replace')
                split_client = client_from_env('split', on_call=record_model_call(exam.get('user_id'), exam_id=job['exam_id'], asset_id=asset['id']))
                parser = ModelTextSplitter(split_client) if isinstance(content, str) else OpenAICompatibleVisionParser()
                result = parser.parse_page(asset['filename'], content, asset['page_order'])
                submission = SUBMISSIONS[exam['submission_id']]
                result.subject_mismatch = bool(result.subject and result.subject != submission['subject'])
                page = {'id': str(uuid4()), 'asset_id': asset['id'], 'exam_id': job['exam_id'], 'user_id': exam.get('user_id', LOCAL_USER_ID), **result.to_dict()}
                page['include_in_analysis'] = not page['subject_mismatch']
                page['exclusion_reason'] = 'subject_mismatch' if page['subject_mismatch'] else ''
                page['inclusion_decided_by'] = 'machine'
                if asset['id'] in user_decisions:
                    page['include_in_analysis'], page['exclusion_reason'] = user_decisions[asset['id']]
                    page['inclusion_decided_by'] = 'user'
                PAGES[page['id']] = page
                asset['ocr_status'] = result.ocr_status
                parsed_pages.append(page)
            stage['result'] = {'pages_processed': len(parsed_pages), 'page_ids': [page['id'] for page in parsed_pages]}
        elif stage['name'] == 'question_split':
            stage['result'] = rebuild_draft_questions(job['exam_id'])
        stage['status'] = 'completed'
      job['status'] = 'completed'
      exam = EXAMS[job['exam_id']]
      exam['status'] = 'waiting_confirmation'
      return job
    except Exception as exc:
      current_stage['status'] = 'failed'
      current_stage['error'] = str(exc)
      job['status'] = 'failed'
      job['failed_stage'] = current_stage['name']
      job['error'] = str(exc)
      return JSONResponse(job, status_code=500)

def classify_filename(filename: str) -> str:
    lowered = Path(filename).name.lower()
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

def rebuild_draft_questions(exam_id: str) -> dict:
    """Pages the user or the parser ruled out stay visible but never reach the draft."""
    exam = EXAMS[exam_id]
    kept = [page for page in pages_for_exam(exam_id) if page.get('include_in_analysis', True)]
    excluded = [page for page in pages_for_exam(exam_id) if not page.get('include_in_analysis', True)]
    def draft_key(question, asset_id):
        return (asset_id, str(question.get('question_number', '')))

    known_assets = {asset['id'] for asset in assets_for_exam(exam_id)}
    previous = {draft_key(item, (item.get('original_source') or {}).get('asset_id')): item for item in exam.get('draft_questions', [])}
    manual = [item for item in exam.get('draft_questions', []) if (item.get('original_source') or {}).get('asset_id') not in known_assets]
    rebuilt = []
    for page in kept:
        source = {'asset_id': page['asset_id'], 'page_id': page['id'], 'filename': page['filename']}
        for question in page['questions']:
            kept_draft = previous.get(draft_key(question, page['asset_id']))
            rebuilt.append({**kept_draft, 'original_source': source} if kept_draft else {
                **question, 'original_source': source,
                'id': question.get('id', str(uuid4())),
                'confirmation_status': 'draft',
                'is_wrong_question': question.get('is_wrong_question', True),
                'original_question_available': question.get('original_question_available', True),
            })
    exam['draft_questions'] = rebuilt + manual
    exam['excluded_pages'] = [
        {'page_id': page['id'], 'filename': page.get('filename', ''), 'subject': page.get('subject', ''), 'reason': page.get('exclusion_reason', ''), 'decided_by': page.get('inclusion_decided_by', 'machine'), 'questions_excluded': len(page['questions'])}
        for page in excluded
    ]
    EXAMS[exam_id] = exam
    return {'questions_detected': len(exam['draft_questions']), 'pages_excluded': len(excluded), 'questions_excluded': sum(item['questions_excluded'] for item in exam['excluded_pages'])}

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
            'ocr_status': page.get('ocr_status', 'manual_confirmation_required'),
            'subject_mismatch': page.get('subject_mismatch', False),
            'include_in_analysis': page.get('include_in_analysis', True),
            'exclusion_reason': page.get('exclusion_reason', ''),
            'inclusion_decided_by': page.get('inclusion_decided_by', 'machine'),
            'review_required': page.get('review_required', True),
        })
    return {'exam_id': eid, 'pages': pages, 'excluded_pages': EXAMS[eid].get('excluded_pages', [])}

@app.patch('/api/pages/{page_id}')
def update_page(page_id: str, body: PageEditIn):
    page = PAGES.get(page_id)
    if not page:
        return JSONResponse({'error': 'page_not_found'}, status_code=404)
    changes = body.model_dump(exclude_none=True)
    if 'page_order' in changes and changes['page_order'] < 1:
        return JSONResponse({'error': 'invalid_page_order'}, status_code=422)
    if 'material_type' in changes and changes['material_type'] not in {'paper','answer_key','student_answer','correction','unknown'}:
        return JSONResponse({'error': 'invalid_material_type'}, status_code=422)
    if 'include_in_analysis' in changes:
        changes['inclusion_decided_by'] = 'user'
        changes['exclusion_reason'] = '' if changes['include_in_analysis'] else 'user_excluded'
    page.update(changes); PAGES[page_id] = page
    if 'include_in_analysis' in changes:
        return {**page, 'draft_rebuild': rebuild_draft_questions(page['exam_id'])}
    return page

@app.get('/api/exams/{eid}')
def get_exam(eid: str):
    exam = EXAMS.get(eid)
    if not exam:
        return JSONResponse({'error': 'exam_not_found'}, status_code=404)
    return exam

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
    existing = exam.get('draft_questions', [])
    exam['draft_questions'] = [
        {'id': str(uuid4()), **question.model_dump(), 'original_source': question.original_source or (existing[index].get('original_source') if index < len(existing) else None), 'confirmation_status': 'draft'}
        for index, question in enumerate(body.questions)
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
    superseded_version = exam.get('version', 1)
    exam['version'] = superseded_version + 1
    exam['status'] = 'uploading'
    exam['draft_questions'] = []
    for record in [item for item in QUESTION_RECORDS.values() if item['exam_id'] == eid and item['status'] == 'confirmed']:
        record['status'] = 'superseded'
        record['superseded_by_version'] = exam['version']
        QUESTION_RECORDS[record['id']] = record
    return {'id': eid, 'status': exam['status'], 'version': exam['version'], 'superseded_version': superseded_version}

TRACEABLE_QUESTION_FIELDS = ('question_number', 'question_text', 'is_wrong_question', 'original_question_available', 'student_answer')

def confirmed_provenance(draft: dict, confirmed: dict) -> dict:
    """Close the machine -> user -> confirmed chain so it survives beyond the parse draft."""
    draft_provenance = draft.get('provenance', {})
    chain = {}
    for field in TRACEABLE_QUESTION_FIELDS:
        edited = draft_provenance.get(field, {})
        chain[field] = {
            'machine_value': edited.get('machine_value', draft.get(field)),
            'user_value': edited.get('user_value'),
            'confirmed_value': confirmed.get(field),
        }
    return chain

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
        draft = draft_questions[index] if index < len(draft_questions) else {}
        item = {'id': str(uuid4()), **question.model_dump(), 'confirmation_status': 'confirmed'}
        if question.original_source:
            item['original_source'] = question.original_source
        elif draft.get('original_source'):
            item['original_source'] = draft['original_source']
        item['provenance'] = confirmed_provenance(draft, item)
        confirmed_questions.append(item)
    exam['questions'] = confirmed_questions
    exam['status'] = 'confirmed'
    exam.setdefault('version', 1)
    material_types = {asset.get('material_type') for asset in assets_for_exam(eid)}
    completeness = {
        'has_wrong_questions': bool(wrong),
        'has_original_sources': all(
            bool(item.get('original_source'))
            for item in confirmed_questions if item['is_wrong_question']
        ),
        'has_answer_key': 'answer_key' in material_types,
        'has_student_answers': any(item.get('student_answer') for item in confirmed_questions),
        'has_corrections': 'correction' in material_types,
    }
    completeness['missing'] = sorted(key for key, present in completeness.items() if not present)
    completeness['excluded_pages'] = exam.get('excluded_pages', [])
    exam['completeness'] = completeness
    records = []
    for item in confirmed_questions:
        record = {
            'id': str(uuid4()), 'exam_id': eid, 'user_id': exam.get('user_id', LOCAL_USER_ID), 'version': exam['version'],
            'question_number': item['question_number'], 'question_text': item['question_text'],
            'is_wrong_question': item['is_wrong_question'],
            'original_source': item.get('original_source'),
            'student_answer': {'text': item.get('student_answer')},
            'score': {'obtained': None, 'total': None, 'lost_points': None},
            'correction': {'teacher_comment': ''},
            'provenance': item.get('provenance', {}),
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

def apply_knowledge_mapping(record: dict):
    exam = EXAMS[record['exam_id']]
    submission = SUBMISSIONS[exam['submission_id']]
    matches = match_knowledge_points(
        stage=submission['stage'], subject=submission['subject'],
        province=submission['province'], question_text=record['question_text'],
        curriculum_year=submission.get('curriculum_year'),
    )
    record['knowledge_points'] = matches
    record['knowledge_point_ids'] = [item['knowledge_id'] for item in matches]
    QUESTION_RECORDS[record['id']] = record
    return matches

@app.post('/api/question-records/{record_id}/knowledge-mapping')
def map_question_knowledge(record_id: str):
    record = QUESTION_RECORDS.get(record_id)
    if not record:
        return JSONResponse({'error': 'question_record_not_found'}, status_code=404)
    try:
        matches = apply_knowledge_mapping(record)
    except KnowledgePartitionNotFound as missing:
        return JSONResponse({'error': 'knowledge_partition_not_found', 'requested': {'stage': missing.stage, 'subject': missing.subject, 'province': missing.province}, 'available_partitions': available_partitions()}, status_code=422)
    return {'record_id': record_id, 'knowledge_points': matches}

def exam_versions(exam_ids: list[str]) -> list[dict]:
    return [{'exam_id': eid, 'name': EXAMS[eid]['name'], 'version': EXAMS[eid].get('version', 1)} for eid in exam_ids]

@app.post('/api/submissions/{sid}/analysis-runs', status_code=201)
def create_run(sid: str, body: AnalysisIn):
    if sid not in SUBMISSIONS: return JSONResponse({'error':'submission_not_found'},status_code=404)
    if not body.exam_ids or any(e not in EXAMS or EXAMS[e]['submission_id']!=sid or EXAMS[e]['status']!='confirmed' for e in body.exam_ids): return JSONResponse({'error':'exams_not_confirmed'},status_code=422)
    rid=str(uuid4()); RUNS[rid]={'id':rid,'submission_id':sid,'user_id':SUBMISSIONS[sid].get('user_id',LOCAL_USER_ID),'exam_ids':body.exam_ids,'status':'awaiting_confirmation','exam_versions':exam_versions(body.exam_ids)}; return RUNS[rid]

@app.get('/api/submissions/{sid}/analysis-runs')
def list_analysis_runs(sid: str):
    if sid not in SUBMISSIONS:
        return JSONResponse({'error': 'submission_not_found'}, status_code=404)
    return {'submission_id': sid, 'runs': [item for item in RUNS.values() if item['submission_id'] == sid]}

@app.post('/api/analysis-runs/{rid}/confirm-start', status_code=202)
def confirm_start(rid: str):
    run=RUNS.get(rid)
    if not run: return JSONResponse({'error':'run_not_found'},status_code=404)
    run['status']='queued'; return run

SCOPE_COVERAGE_NOTE = '未考查表示本次资料没有覆盖该知识点，不能据此判定未掌握。'

def build_scope_coverage(partition, coverage: dict):
    """Contrast the curriculum scope of the knowledge partition with what the uploaded exams actually assessed."""
    points = []
    for point in partition.points:
        assessed = coverage.get(point['knowledge_id'])
        points.append({
            'knowledge_id': point['knowledge_id'], 'name': point['name'], 'chapter': point['chapter'],
            'grade_scope': point['grade_scope'], 'textbook_chapter': point['textbook_chapter'],
            'status': 'not_assessed' if not assessed else ('assessed_with_errors' if assessed['wrong_count'] else 'assessed_without_errors'),
            'question_count': assessed['question_count'] if assessed else 0,
            'wrong_count': assessed['wrong_count'] if assessed else 0,
        })
    not_assessed = [item for item in points if item['status'] == 'not_assessed']
    return {
        'curriculum_point_count': len(points),
        'assessed_point_count': len(points) - len(not_assessed),
        'not_assessed_point_count': len(not_assessed),
        'coverage_ratio': round((len(points) - len(not_assessed)) / len(points), 4) if points else 0.0,
        'not_assessed_knowledge_ids': [item['knowledge_id'] for item in not_assessed],
        'not_assessed_chapters': sorted({item['chapter'] for item in not_assessed}),
        'note': SCOPE_COVERAGE_NOTE, 'points': points,
    }

LOW_CONFIDENCE_THRESHOLD = 0.8

def build_quality_flags(exam_ids: list[str], records: list[dict], unmapped: list[str]) -> list[dict]:
    """One report-level list so the reader never has to hunt for caveats across sections."""
    flags = []
    for exam_id in exam_ids:
        exam = EXAMS[exam_id]
        missing = (exam.get('completeness') or {}).get('missing', [])
        if missing:
            flags.append({'code': 'incomplete_materials', 'severity': 'warning', 'exam_id': exam_id, 'exam_name': exam['name'], 'detail': missing, 'message': f"考试「{exam['name']}」缺少：{'、'.join(missing)}，相关结论只能算部分成立"})
        for page in sorted(pages_for_exam(exam_id), key=lambda item: item.get('page_order', 0)):
            if page.get('ocr_status') == 'manual_confirmation_required':
                flags.append({'code': 'manual_confirmation_required', 'severity': 'warning', 'exam_id': exam_id, 'page_id': page['id'], 'message': f"页面 {page.get('filename', '')} 未能自动解析，内容以人工确认为准"})
            elif page.get('ocr_confidence', 0) < LOW_CONFIDENCE_THRESHOLD:
                flags.append({'code': 'low_confidence_page', 'severity': 'info', 'exam_id': exam_id, 'page_id': page['id'], 'detail': page.get('ocr_confidence', 0), 'message': f"页面 {page.get('filename', '')} 识别置信度 {page.get('ocr_confidence', 0)}，低于 {LOW_CONFIDENCE_THRESHOLD}"})
        for excluded in exam.get('excluded_pages', []):
            flags.append({'code': 'page_excluded', 'severity': 'warning', 'exam_id': exam_id, 'page_id': excluded['page_id'], 'detail': excluded['reason'], 'message': f"页面 {excluded['filename']} 因 {excluded['reason']} 未计入统计，少算 {excluded['questions_excluded']} 道题"})
    if unmapped:
        flags.append({'code': 'unmapped_questions', 'severity': 'warning', 'detail': unmapped, 'message': f'有 {len(unmapped)} 道题没有匹配到知识点，未进入知识树和覆盖率'})
    without_score = [record['id'] for record in records if record['score'].get('obtained') is None]
    if without_score:
        flags.append({'code': 'missing_scores', 'severity': 'info', 'detail': without_score, 'message': f'有 {len(without_score)} 道题没有录入得分，失分分析不完整'})
    return flags

@app.get('/api/analysis-runs/{rid}/report')
def get_analysis_report(rid: str):
    run = RUNS.get(rid)
    if not run:
        return JSONResponse({'error': 'run_not_found'}, status_code=404)
    if run['status'] == 'report_ready' and run.get('report'):
        return run['report']
    if run['status'] != 'queued':
        return JSONResponse({'error': 'analysis_not_started'}, status_code=409)
    submission = SUBMISSIONS[run['submission_id']]
    partition = find_partition(submission['stage'], submission['subject'], submission['province'], submission.get('curriculum_year'))
    if partition is None:
        return JSONResponse({'error': 'knowledge_partition_not_found', 'requested': {'stage': submission['stage'], 'subject': submission['subject'], 'province': submission['province']}, 'available_partitions': available_partitions()}, status_code=422)
    selected = run.get('exam_versions') or exam_versions(run['exam_ids'])
    current = exam_versions(run['exam_ids'])
    if current != selected:
        return JSONResponse({'error': 'exam_versions_changed', 'selected': selected, 'current': current}, status_code=409)
    records = [
        record for exam_id in run['exam_ids']
        for record in QUESTION_RECORDS.values()
        if record['exam_id'] == exam_id and record['status'] == 'confirmed'
    ]
    for record in records:
        if not record.get('knowledge_points'):
            apply_knowledge_mapping(record)
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
        'unmapped_question_ids': unmapped, 'knowledge_base': partition.describe(),
        'scope_coverage': build_scope_coverage(partition, coverage),
        'exam_versions': selected,
        'quality_flags': build_quality_flags(run['exam_ids'], records, unmapped),
        'source_question_ids': [record['id'] for record in records],
    }
    analysis_client = client_from_env('analysis', on_call=record_model_call(run.get('user_id'), run_id=rid))
    verify_client = client_from_env('verify', on_call=record_model_call(run.get('user_id'), run_id=rid))
    report.update(diagnose_records(records, client=analysis_client, verify_client=verify_client))
    report['review'] = review_report(report, partition)
    if report['review']['status'] != APPROVED:
        report['quality_flags'].append({'code': 'review_pending', 'severity': 'warning', 'detail': [item['code'] for item in report['review']['findings']], 'message': f"独立审查发现 {len(report['review']['findings'])} 处问题，{report['review']['pending_verification_questions']} 道验证题的参考答案在复核通过前不会展示"})
    report_client = client_from_env('report', on_call=record_model_call(run.get('user_id'), run_id=rid))
    if report_client is not None:
        summary = generate_report_summary(report_client, report)
        if summary:
            report['summary'] = summary
    verification_ids = []
    for generated in report['verification_questions']:
        vid = str(uuid4())
        entity = {'id': vid, 'analysis_run_id': rid, 'submission_id': run['submission_id'], 'user_id': run.get('user_id', LOCAL_USER_ID), **generated, 'attempts': []}
        VERIFICATION_QUESTIONS[vid] = entity
        mastery_id = f"{run['submission_id']}:{generated['knowledge_id']}:{generated['target_error']}"
        if mastery_id not in MASTERY_RECORDS:
            MASTERY_RECORDS[mastery_id] = {'id': mastery_id, 'submission_id': run['submission_id'], 'user_id': run.get('user_id', LOCAL_USER_ID), 'knowledge_id': generated['knowledge_id'], 'error_cluster': generated['target_error'], 'status': 'not_started', 'attempt_history': []}
        verification_ids.append(vid)
    report['verification_question_ids'] = verification_ids
    report['verification_questions'] = [withhold_unreviewed_answer(item) for item in report['verification_questions']]
    run['status'] = 'report_ready'
    run['report'] = report
    return report

def refresh_report_verification_questions(rid: str):
    run = RUNS.get(rid)
    if not run or not run.get('report'):
        return
    entities = {item['id']: item for item in VERIFICATION_QUESTIONS.values() if item['analysis_run_id'] == rid}
    report = run['report']
    report['verification_questions'] = [withhold_unreviewed_answer(entities[vid]) for vid in report.get('verification_question_ids', []) if vid in entities]
    pending = [item for item in entities.values() if item.get('review_status') == NEEDS_REVIEW]
    unresolved = [item for item in report['review']['findings'] if item['code'].startswith('cluster_')]
    report['review']['pending_verification_questions'] = len(pending)
    report['review']['status'] = NEEDS_REVIEW if (pending or unresolved) else APPROVED
    RUNS[rid] = run

@app.get('/api/analysis-runs/{rid}/verification-questions')
def list_verification_questions(rid: str):
    if rid not in RUNS:
        return JSONResponse({'error': 'run_not_found'}, status_code=404)
    return {'analysis_run_id': rid, 'questions': [withhold_unreviewed_answer(item) for item in VERIFICATION_QUESTIONS.values() if item['analysis_run_id'] == rid]}

@app.get('/api/verification-questions/{question_id}')
def get_verification_question(question_id: str):
    item = VERIFICATION_QUESTIONS.get(question_id)
    if not item:
        return JSONResponse({'error': 'verification_question_not_found'}, status_code=404)
    return withhold_unreviewed_answer(item)

@app.post('/api/verification-questions/{question_id}/review')
def review_verification_answer(question_id: str, body: VerificationReviewIn):
    question = VERIFICATION_QUESTIONS.get(question_id)
    if not question:
        return JSONResponse({'error': 'verification_question_not_found'}, status_code=404)
    answer = (body.answer if body.answer is not None else question.get('answer')) or ''
    if body.approved and not answer.strip():
        return JSONResponse({'error': 'cannot_approve_empty_answer'}, status_code=422)
    question['answer'] = answer
    question['review_status'] = APPROVED if body.approved else NEEDS_REVIEW
    question['review_findings'] = [] if body.approved else question.get('review_findings', []) or ['answer_missing']
    question['reviewed_by'] = 'user'
    question['review_note'] = body.note
    VERIFICATION_QUESTIONS[question_id] = question
    refresh_report_verification_questions(question['analysis_run_id'])
    return withhold_unreviewed_answer(question)

@app.post('/api/verification-questions/{question_id}/attempts', status_code=201)
def submit_verification_attempt(question_id: str, body: VerificationAttemptIn):
    question = VERIFICATION_QUESTIONS.get(question_id)
    if not question:
        return JSONResponse({'error': 'verification_question_not_found'}, status_code=404)
    if body.max_score <= 0 or body.score < 0 or body.score > body.max_score:
        return JSONResponse({'error': 'invalid_score'}, status_code=422)
    attempt = {'id': str(uuid4()), **body.model_dump(), 'passed': body.score / body.max_score >= 0.8}
    question['attempts'].append(attempt)
    VERIFICATION_QUESTIONS[question_id] = question
    mastery_id = f"{question['submission_id']}:{question['knowledge_id']}:{question['target_error']}"
    mastery = MASTERY_RECORDS[mastery_id]
    mastery['attempt_history'].append({'verification_question_id': question_id, **attempt})
    passes = [item for item in mastery['attempt_history'] if item['passed']]
    if not attempt['passed']:
        mastery['status'] = 'in_progress'
    elif len(passes) >= 2:
        mastery['status'] = 'resolved'
    else:
        mastery['status'] = 'provisionally_resolved'
    MASTERY_RECORDS[mastery_id] = mastery
    return attempt

@app.get('/api/submissions/{sid}/mastery-records')
def list_mastery_records(sid: str):
    if sid not in SUBMISSIONS:
        return JSONResponse({'error': 'submission_not_found'}, status_code=404)
    return {'submission_id': sid, 'records': [item for item in MASTERY_RECORDS.values() if item['submission_id'] == sid]}

@app.get('/api/reports/{rid}/export')
def export_report(rid: str, format: str = 'md'):
    run = RUNS.get(rid)
    if not run or 'report' not in run:
        return JSONResponse({'error': 'report_not_found'}, status_code=404)
    from .reporting import render_html, render_markdown
    if format == 'html':
        return HTMLResponse(render_html(run['report']))
    if format == 'md':
        return PlainTextResponse(render_markdown(run['report']), media_type='text/markdown')
    return JSONResponse({'error': 'unsupported_format'}, status_code=422)

@app.get('/api/model-calls')
def list_model_calls(user_id: str = Depends(current_user_id), role: str | None = None, limit: int = 100):
    """Observability for the analysis/report/split model roles: every call this user's actions
    triggered, success or not, with latency and token usage where the provider reported it. There
    was previously no way to answer "is the model actually being called, and is it working" short
    of reading server stdout."""
    limit = max(1, min(limit, MODEL_CALLS_MAX))
    calls = [item for item in MODEL_CALLS.values() if item.get('user_id', LOCAL_USER_ID) == user_id]
    if role:
        calls = [item for item in calls if item.get('role') == role]
    calls.sort(key=lambda item: item['created_at'], reverse=True)
    calls = calls[:limit]
    total = len(calls)
    succeeded = sum(1 for item in calls if item.get('success'))
    return {
        'calls': calls,
        'summary': {
            'total': total, 'succeeded': succeeded, 'failed': total - succeeded,
            'avg_latency_ms': round(sum(item.get('latency_ms', 0) for item in calls) / total) if total else 0,
            'total_tokens': sum(item.get('total_tokens') or 0 for item in calls),
        },
    }
