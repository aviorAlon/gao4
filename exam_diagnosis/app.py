from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
import os
import re
import hashlib
import sys
import threading
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field, ValidationError
import yaml
from .parser import LocalParser, ModelTextSplitter, OpenAICompatibleVisionParser, PageParseResult, extract_pdf_text, is_scanned_pdf_result, parse_scanned_pdf
from .knowledge import match_knowledge_points
from . import knowledge
from .diagnosis import diagnose_records, model_grade_attempt
from .storage import SQLiteMapping
from .tasks import TaskQueue
from .trends import build_trends
from . import auth
from .knowledge import KnowledgePartitionNotFound, available_partitions, find_partition
from .review import APPROVED, NEEDS_REVIEW, review_report, withhold_unreviewed_answer
from .llm import ROLE_DEFAULTS, client_from_env, role_status as llm_role_status
from .reporting import generate_report_summary
from .env_file import load_env_file
from . import paths
from . import settings as app_settings

# Loaded once at import time, before anything below reads os.environ — lets model-role API keys
# live in a gitignored .env.local file instead of needing `$env:...` set by hand every session.
# Real environment variables still win if both are set (see env_file.load_env_file).
#
# Never under pytest, though: this repo's own .env.local (or anyone else's, sitting in whatever
# directory tests happen to run from) must never reach a test process. A real key loaded here
# means client_from_env() stops returning None in tests, and every test that reaches report
# generation silently makes real, paid calls to the model provider instead of exercising the
# fallback path it was written to test — discovered the hard way as a "hung" test suite that was
# actually just waiting tens of seconds per test on real DeepSeek responses. `importlib.reload`
# of this module (see test_app.py's restart test) re-runs this exact line, so this guard has to
# live here, not just in a conftest fixture that only runs once per session.
if 'pytest' not in sys.modules:
    load_env_file()
    # Then <data_dir>/config.json (the settings page's storage) — same guard, same reason, and it
    # only fills in what the environment/.env.local didn't set (see settings.apply_config).
    app_settings.apply_config(app_settings.load_config())

LOCAL_USER_ID = 'local'
USER_HEADER = 'X-User-Id'

# Two identity modes, chosen by EXAM_DIAGNOSIS_AUTH_REQUIRED:
#   off (default) — the original single-user MVP behaviour: X-User-Id or 'local'. A bearer token,
#     if one is presented, still wins, and a *bad* token is a 401 rather than a silent fallback.
#   on — every request outside PUBLIC_PATHS must carry a valid bearer token; X-User-Id is ignored
#     entirely (it's a plain header anyone can set, so it can't be an identity once there's more
#     than one real person).
AUTH_REQUIRED = os.getenv('EXAM_DIAGNOSIS_AUTH_REQUIRED', '').strip().lower() in ('1', 'true', 'yes', 'on')
SESSION_TTL_HOURS = float(os.getenv('EXAM_DIAGNOSIS_SESSION_TTL_HOURS') or 24 * 30)
PUBLIC_PATHS = frozenset({'/api/auth/register', '/api/auth/login', '/api/health'})
LOGIN_THROTTLE = auth.LoginThrottle()


def _session_for_token(token: str) -> dict | None:
    session = AUTH_SESSIONS.get(auth.token_key(token))
    if not session or session.get('revoked') or auth.is_expired(session):
        return None
    return session


def resolve_user_id(request: Request) -> str:
    header = request.headers.get('Authorization')
    if header:
        token = auth.bearer_token(header)
        session = _session_for_token(token) if token else None
        if session is None:
            raise HTTPException(status_code=401, detail={'error': 'invalid_token'})
        return session['user_id']
    if AUTH_REQUIRED:
        raise HTTPException(status_code=401, detail={'error': 'authentication_required'})
    return request.headers.get(USER_HEADER) or LOCAL_USER_ID


def current_user_id(request: Request) -> str:
    """Every resource carries its owner; this is where a request's owner comes from."""
    return resolve_user_id(request)

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
TASKS = SQLiteMapping('tasks')
USERS = SQLiteMapping('users')
AUTH_SESSIONS = SQLiteMapping('auth_sessions')
USER_KNOWLEDGE = SQLiteMapping('user_knowledge')

_PERSISTENT_MAPPINGS = (SUBMISSIONS, EXAMS, ASSETS, RUNS, UPLOAD_SESSIONS, JOBS, PAGES, QUESTION_RECORDS, VERIFICATION_QUESTIONS, MASTERY_RECORDS, MODEL_CALLS, TASKS, USERS, AUTH_SESSIONS, USER_KNOWLEDGE)

# The knowledge registry resolves "public partition, overlaid by this user's own" — it gets the
# user's records from here so knowledge.py needn't know about SQLite.
knowledge.set_user_partition_provider(lambda user_id: [item for item in USER_KNOWLEDGE.values() if item.get('user_id') == user_id])


def _flush_all_state() -> None:
    for mapping in _PERSISTENT_MAPPINGS:
        mapping.flush()


# Background execution for the two slow operations (exam parsing, report generation). One worker
# by default: the work is dominated by model calls, and running several tasks at once mostly
# means racing the shared token budget; raise EXAM_DIAGNOSIS_TASK_WORKERS if that's wanted.
TASK_WORKERS = int(os.getenv('EXAM_DIAGNOSIS_TASK_WORKERS') or 1)
TASK_QUEUE = TaskQueue(TASKS, workers=TASK_WORKERS, after_task=_flush_all_state)

MODEL_CALLS_MAX = 500

# FastAPI runs sync `def` route handlers in a thread pool, so two near-simultaneous GET requests
# for the same not-yet-generated report both run get_analysis_report concurrently. Report
# generation now makes real, paid model calls and can take tens of seconds (deepseek-v4-pro's
# thinking mode is not fast) — a wide enough window that both requests can pass the
# `status == 'queued'` check before either has flipped it, and both pay to regenerate the same
# report. One process-wide lock serializes just the check-and-claim step (not the generation
# itself), which is enough for a local single-user MVP where true concurrent report generation
# is rare in the first place.
_REPORT_GENERATION_LOCK = threading.Lock()


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


# Cost control on top of the observability above — seeing what a call cost after the fact doesn't
# stop the next one from happening. Both are opt-in via env vars and off (unlimited) by default,
# matching every other model-related knob in this app: nothing changes for an operator who hasn't
# configured it. Global across all four roles and the current user, not per-role — a single
# combined ceiling is what "don't overspend" actually means for a local single-user MVP; splitting
# it per role would just mean picking four numbers that have to individually add up right anyway.
MODEL_DAILY_TOKEN_BUDGET: int | None = None
MODEL_MAX_CALLS_PER_MINUTE: int | None = None


def _refresh_budget_from_env() -> None:
    """Re-read the two budget knobs — at import, and again whenever the settings page changes
    them (settings.apply_config writes the environment; these globals are what the gate reads)."""
    global MODEL_DAILY_TOKEN_BUDGET, MODEL_MAX_CALLS_PER_MINUTE
    MODEL_DAILY_TOKEN_BUDGET = int(os.getenv('EXAM_DIAGNOSIS_MODEL_DAILY_TOKEN_BUDGET') or 0) or None
    MODEL_MAX_CALLS_PER_MINUTE = int(os.getenv('EXAM_DIAGNOSIS_MODEL_MAX_CALLS_PER_MINUTE') or 0) or None


_refresh_budget_from_env()


def _budget_block_reason(user_id: str) -> str | None:
    """None if a real model call may proceed; otherwise the reason it's blocked. Checked before a
    client is even constructed, so a blocked call never reaches the network and costs nothing."""
    if not MODEL_DAILY_TOKEN_BUDGET and not MODEL_MAX_CALLS_PER_MINUTE:
        return None
    now = datetime.utcnow()
    calls = [c for c in MODEL_CALLS.values() if c.get('user_id', LOCAL_USER_ID) == user_id]
    if MODEL_MAX_CALLS_PER_MINUTE:
        recent = sum(1 for c in calls if now - datetime.fromisoformat(c['created_at'].rstrip('Z')) < timedelta(minutes=1))
        if recent >= MODEL_MAX_CALLS_PER_MINUTE:
            return 'rate_limited'
    if MODEL_DAILY_TOKEN_BUDGET:
        today = now.date().isoformat()
        used_today = sum(c.get('total_tokens') or 0 for c in calls if c.get('success') and c['created_at'].startswith(today))
        if used_today >= MODEL_DAILY_TOKEN_BUDGET:
            return 'daily_token_budget_exceeded'
    return None


def guarded_client_from_env(role: str, user_id: str | None, **context: Any):
    """Same contract as llm.client_from_env: a ChatClient, or None if the role isn't usable right
    now — a budget/rate-limit block is recorded exactly like a failed call (visible in
    /api/model-calls with a `blocked` field), so it shows up instead of the role just silently
    going quiet with no trace of why."""
    uid = user_id or LOCAL_USER_ID
    reason = _budget_block_reason(uid)
    if reason is not None:
        record_model_call(uid, **context)({
            'role': role, 'model': None, 'base_url': None, 'latency_ms': 0, 'attempts': 0,
            'success': False, 'error': reason, 'blocked': True,
            'prompt_tokens': None, 'completion_tokens': None, 'total_tokens': None,
        })
        return None
    return client_from_env(role, on_call=record_model_call(uid, **context))


def vision_parser_for(user_id: str | None, **context: Any) -> OpenAICompatibleVisionParser | None:
    """The vision parser is configured by its own EXAM_DIAGNOSIS_VISION_* variables rather than
    through llm.client_from_env (it sends images, not a chat transcript), but it costs money per
    page just the same — so it goes through the same budget gate and logs into MODEL_CALLS under
    role 'vision'. None means 'don't call it': unconfigured, or blocked by the budget."""
    uid = user_id or LOCAL_USER_ID
    reason = _budget_block_reason(uid)
    if reason is not None:
        record_model_call(uid, **context)({
            'role': 'vision', 'model': None, 'base_url': None, 'latency_ms': 0, 'attempts': 0,
            'success': False, 'error': reason, 'blocked': True,
            'prompt_tokens': None, 'completion_tokens': None, 'total_tokens': None,
        })
        return None
    parser = OpenAICompatibleVisionParser(on_call=record_model_call(uid, **context))
    return parser if parser.configured else None


OWNED_BY_PATH_PARAM = {
    'sid': SUBMISSIONS, 'eid': EXAMS, 'asset_id': ASSETS, 'rid': RUNS,
    'session_id': UPLOAD_SESSIONS, 'job_id': JOBS, 'page_id': PAGES,
    'record_id': QUESTION_RECORDS, 'question_id': VERIFICATION_QUESTIONS, 'task_id': TASKS,
    'kb_id': USER_KNOWLEDGE,
}

def enforce_resource_owner(request: Request):
    """One chokepoint for the isolation boundary, so no route can forget to check it. Also where
    authentication is enforced: register/login are the only routes reachable without one."""
    if request.url.path in PUBLIC_PATHS:
        return
    user_id = resolve_user_id(request)
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
    # The desktop shell's webview is an origin too: http://tauri.localhost on Windows (WebView2),
    # tauri://localhost elsewhere. Missed the first time round — every request from the packaged
    # app was refused by the browser layer while curl from a terminal (no Origin header) passed.
    allow_origin_regex=r'(https?://(localhost|127\.0\.0\.1|tauri\.localhost)(:\d+)?|tauri://localhost)',
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
    allowed_root = paths.uploads_dir()
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
    _flush_all_state()
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
    partition = find_partition(body.stage, body.subject, body.province, body.curriculum_year, user_id=user_id)
    if partition is None:
        return JSONResponse({'error': 'knowledge_partition_not_found', 'requested': {'stage': body.stage, 'subject': body.subject, 'province': body.province, 'curriculum_year': body.curriculum_year}, 'available_partitions': available_partitions(user_id=user_id)}, status_code=422)
    if not partition.covers_grade(body.school_grade):
        return JSONResponse({'error': 'grade_outside_partition_scope', 'school_grade': body.school_grade, 'academic_year_range': list(partition.academic_year_range)}, status_code=422)
    sid=str(uuid4())
    item={'id':sid, 'user_id': user_id, **body.model_dump(), **span, 'curriculum_year': partition.curriculum_year, 'status':'subject_confirmed', 'exam_count':0, 'max_exam_count':10, 'knowledge_partition': partition.describe(), 'learning_scope':{'stage':body.stage,'subject':body.subject,'province':body.province,'school_grade':body.school_grade,'academic_year_label':body.academic_year_label, **span}}
    SUBMISSIONS[sid]=item
    return item

@app.get('/api/knowledge-partitions')
def list_knowledge_partitions(user_id: str = Depends(current_user_id)):
    """Public partitions plus this user's own; where both cover a scope, the user's is the one
    listed (it's the one that would be used), with `source: 'user'`."""
    return {'partitions': available_partitions(user_id=user_id)}


@app.get('/api/knowledge-partitions/points')
def knowledge_partition_points(stage: str, subject: str, province: str, curriculum_year: int | None = None, user_id: str = Depends(current_user_id)):
    """The effective knowledge points for a scope as this user sees them (public base merged with
    their own partition, each point tagged with its origin)."""
    partition = find_partition(stage, subject, province, curriculum_year, user_id=user_id)
    if partition is None:
        return JSONResponse({'error': 'knowledge_partition_not_found', 'requested': {'stage': stage, 'subject': subject, 'province': province, 'curriculum_year': curriculum_year}, 'available_partitions': available_partitions(user_id=user_id)}, status_code=422)
    return {'partition': partition.describe(), 'points': [{**point, 'origin': point.get('origin', 'public')} for point in partition.points]}

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
    partition = find_partition(submission['stage'], submission['subject'], submission['province'], submission.get('curriculum_year'), user_id=submission.get('user_id', LOCAL_USER_ID))
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
    aid=str(uuid4()); data=paths.uploads_dir()/eid; data.mkdir(parents=True,exist_ok=True); filename=Path(file.filename).name; path=data/filename
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
    # Draft questions that came from this asset go with it. rebuild_draft_questions keeps any
    # draft whose source asset it doesn't know about, treating it as manually entered — so left
    # in place, a deleted scan's questions would survive every re-parse as phantom "manual" ones.
    exam = EXAMS.get(asset['exam_id'])
    if exam is not None and exam.get('draft_questions'):
        exam['draft_questions'] = [item for item in exam['draft_questions'] if (item.get('original_source') or {}).get('asset_id') != asset_id]
        EXAMS[exam['id']] = exam
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
    data = paths.uploads_dir() / exam_id / session_id
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
    job_id = _create_parse_job(session['exam_id'], session.get('user_id', LOCAL_USER_ID), upload_session_id=session_id)
    session['job_id'] = job_id
    UPLOAD_SESSIONS[session_id] = session
    return session

def _create_parse_job(exam_id: str, user_id: str, upload_session_id: str | None = None) -> str:
    """A parse job always works on every asset the exam currently has (not just one session's),
    so the same job shape serves both "finished uploading" and "parse these again"."""
    EXAMS[exam_id]['status'] = 'parsing'
    job_id = str(uuid4())
    JOBS[job_id] = {
        'id': job_id, 'job_type': 'parse_exam', 'exam_id': exam_id,
        'user_id': user_id,
        'upload_session_id': upload_session_id, 'status': 'queued', 'attempt': 1,
        'stages': [
            {'name': 'classify', 'status': 'queued', 'error': None},
            {'name': 'ocr', 'status': 'queued', 'error': None},
            {'name': 'question_split', 'status': 'queued', 'error': None},
        ],
    }
    return job_id


@app.post('/api/exams/{eid}/reparse', status_code=201)
def reparse_exam(eid: str):
    """Queue a fresh parse of the exam's current assets — after deleting a bad scan, adding the
    vision model, or changing an asset's type. A confirmed exam must be reopened first, since
    re-parsing replaces the pages and draft questions the confirmation was based on."""
    exam = EXAMS.get(eid)
    if not exam:
        return JSONResponse({'error': 'exam_not_found'}, status_code=404)
    if exam['status'] == 'confirmed':
        return JSONResponse({'error': 'exam_confirmed', 'hint': 'reopen the exam before re-parsing'}, status_code=409)
    if not assets_for_exam(eid):
        return JSONResponse({'error': 'exam_has_no_assets'}, status_code=422)
    job_id = _create_parse_job(eid, exam.get('user_id', LOCAL_USER_ID))
    return {'job_id': job_id, 'job': JOBS[job_id], 'exam_id': eid, 'asset_count': len(assets_for_exam(eid))}


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
    """Synchronous variant: parses in the request thread and returns the finished job. Kept for
    scripts and tests; interactive clients should POST /enqueue and poll instead."""
    job = JOBS.get(job_id)
    if not job:
        return JSONResponse({'error': 'job_not_found'}, status_code=404)
    active = TASK_QUEUE.active_for(job_id=job_id)
    if active is not None:
        return JSONResponse({'error': 'job_already_enqueued', 'task_id': active['id']}, status_code=409)
    if job['status'] != 'queued':
        return JSONResponse({'error': 'job_not_queued'}, status_code=409)
    return job if _execute_parse_job(job_id) else JSONResponse(job, status_code=500)


@app.post('/api/jobs/{job_id}/enqueue', status_code=202)
def enqueue_job(job_id: str):
    """Queue the parse on a worker thread and return at once with a task id to poll
    (GET /api/tasks/{task_id}, or GET /api/jobs/{job_id} for per-stage progress)."""
    job = JOBS.get(job_id)
    if not job:
        return JSONResponse({'error': 'job_not_found'}, status_code=404)
    active = TASK_QUEUE.active_for(job_id=job_id)
    if active is not None:
        return JSONResponse({'error': 'job_already_enqueued', 'task_id': active['id']}, status_code=409)
    if job['status'] != 'queued':
        return JSONResponse({'error': 'job_not_queued'}, status_code=409)
    task = TASK_QUEUE.submit('parse_exam', lambda: _run_parse_job_task(job_id), user_id=job.get('user_id', LOCAL_USER_ID), job_id=job_id, exam_id=job['exam_id'])
    job['task_id'] = task['id']
    JOBS[job_id] = job
    return {'job': job, 'task_id': task['id']}


def _run_parse_job_task(job_id: str) -> dict:
    if _execute_parse_job(job_id):
        return {'job_id': job_id, 'status': 'completed'}
    job = JOBS[job_id]
    raise RuntimeError(f"{job.get('failed_stage')}: {job.get('error')}")


def _execute_parse_job(job_id: str) -> bool:
    """Run every queued stage of a parse job in place. True on success; on failure the job and
    the failing stage carry the error (the shape /retry expects) and this returns False."""
    job = JOBS[job_id]
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
                result = None
                call_context = {'exam_id': job['exam_id'], 'asset_id': asset['id']}
                if asset.get('media_type', '') == 'application/pdf' or asset['filename'].lower().endswith('.pdf'):
                    pdf_result = extract_pdf_text(asset['storage_path'])
                    asset['ocr_warning'] = pdf_result.get('warning', '')
                    if is_scanned_pdf_result(pdf_result):
                        # No text layer: the pages are pictures, so the text splitter has nothing
                        # to work with. Render them and go through the vision parser instead.
                        asset['scanned_pdf'] = True
                        vision = vision_parser_for(exam.get('user_id'), **call_context)
                        if vision is None:
                            result = PageParseResult(filename=Path(asset['filename']).name, page_order=asset['page_order'], material_type=classify_filename(asset['filename']), subject='', ocr_text='', ocr_confidence=0.0, review_required=True, ocr_status='manual_confirmation_required', warning=f"该 PDF 为扫描件（共 {pdf_result['page_count']} 页，无文字层），未配置视觉解析（EXAM_DIAGNOSIS_VISION_*），请人工录入题目。")
                        else:
                            result = parse_scanned_pdf(vision, asset['filename'], asset['storage_path'], asset['page_order'])
                    else:
                        content = pdf_result['text'] if pdf_result['status'] == 'completed' else b''
                elif asset.get('media_type', '').startswith('text/'):
                    content = content.decode('utf-8', errors='replace')
                if result is None:
                    if isinstance(content, str):
                        parser = ModelTextSplitter(guarded_client_from_env('split', exam.get('user_id'), **call_context))
                    else:
                        # LocalParser on bytes is the honest "no vision configured / blocked" answer:
                        # manual confirmation, never a fabricated parse.
                        parser = vision_parser_for(exam.get('user_id'), **call_context) or LocalParser()
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
      return True
    except Exception as exc:
      current_stage['status'] = 'failed'
      current_stage['error'] = str(exc)
      job['status'] = 'failed'
      job['failed_stage'] = current_stage['name']
      job['error'] = str(exc)
      return False

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
    # SQLiteMapping iterates in set order, so without an explicit sort the parse order — and
    # with it the draft question order — varied from run to run.
    return sorted((asset for asset in ASSETS.values() if asset['exam_id'] == exam_id), key=lambda asset: (asset.get('page_order') or 0, asset.get('filename', ''), asset['id']))

def pages_for_exam(exam_id: str):
    return sorted((page for page in PAGES.values() if page['exam_id'] == exam_id), key=lambda page: (page.get('page_order') or 0, page['id']))

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
            # The parser's own explanation of why a page needs a human (scanned PDF with no
            # vision configured, pages the vision model couldn't read, ...) — without it the UI
            # can only say "please confirm" with no hint of what went wrong.
            'warning': page.get('warning', ''),
            'questions': page.get('questions', []),
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
    user_id = submission.get('user_id', LOCAL_USER_ID)
    matches = match_knowledge_points(
        stage=submission['stage'], subject=submission['subject'],
        province=submission['province'], question_text=record['question_text'],
        curriculum_year=submission.get('curriculum_year'), user_id=user_id,
        client=guarded_client_from_env('match', user_id, exam_id=exam['id'], record_id=record['id']),
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
        return JSONResponse({'error': 'knowledge_partition_not_found', 'requested': {'stage': missing.stage, 'subject': missing.subject, 'province': missing.province}, 'available_partitions': available_partitions(user_id=record.get('user_id'))}, status_code=422)
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

class ReportGenerationError(Exception):
    """A report couldn't be generated for a reason the client has to fix (missing knowledge
    partition, exam re-parsed since the run was created). Carries the HTTP shape so the
    synchronous route can answer with it directly and the background task can record it."""

    def __init__(self, status_code: int, payload: dict):
        super().__init__(payload.get('error', 'report_generation_failed'))
        self.status_code = status_code
        self.payload = payload


def _claim_report_generation(rid: str) -> tuple[dict | None, JSONResponse | None]:
    """Decide, under the lock, whether this caller gets to generate the report.

    Returns (run, None) when the caller may proceed (or the report already exists — callers
    check run['report'] first), or (None, response) with the answer to send instead: 404 for an
    unknown run, 202 'report_generating' while another request or a background task has it,
    409 for a run that was never confirm-started."""
    run = RUNS.get(rid)
    if not run:
        return None, JSONResponse({'error': 'run_not_found'}, status_code=404)
    with _REPORT_GENERATION_LOCK:
        if run['status'] == 'report_ready' and run.get('report'):
            return run, None
        if run['status'] == 'generating':
            # Someone else is paying for this generation right now; the client should poll, not
            # start a second one.
            return None, JSONResponse({'error': 'report_generating', 'status': 'generating', 'run_id': rid, 'task_id': run.get('task_id')}, status_code=202)
        if run['status'] != 'queued':
            return None, JSONResponse({'error': 'analysis_not_started'}, status_code=409)
        run['status'] = 'generating'
        RUNS[rid] = run
    return run, None


def _generate_report_for_run(rid: str, run: dict) -> dict:
    """The generation itself, for a run already claimed by _claim_report_generation."""
    try:
        submission = SUBMISSIONS[run['submission_id']]
        partition = find_partition(submission['stage'], submission['subject'], submission['province'], submission.get('curriculum_year'), user_id=submission.get('user_id', LOCAL_USER_ID))
        if partition is None:
            raise ReportGenerationError(422, {'error': 'knowledge_partition_not_found', 'requested': {'stage': submission['stage'], 'subject': submission['subject'], 'province': submission['province']}, 'available_partitions': available_partitions(user_id=submission.get('user_id'))})
        selected = run.get('exam_versions') or exam_versions(run['exam_ids'])
        current = exam_versions(run['exam_ids'])
        if current != selected:
            raise ReportGenerationError(409, {'error': 'exam_versions_changed', 'selected': selected, 'current': current})
        return _generate_report(rid, run, submission, partition, selected)
    finally:
        # Any exception above leaves run['status'] at 'generating' — revert to 'queued' so the
        # next attempt can retry instead of the run being permanently stuck unclaimed.
        if run['status'] == 'generating':
            run['status'] = 'queued'
            RUNS[rid] = run


@app.get('/api/analysis-runs/{rid}/report')
def get_analysis_report(rid: str):
    run, error = _claim_report_generation(rid)
    if error is not None:
        return error
    if run['status'] == 'report_ready' and run.get('report'):
        return run['report']
    try:
        return _generate_report_for_run(rid, run)
    except ReportGenerationError as exc:
        return JSONResponse(exc.payload, status_code=exc.status_code)


@app.post('/api/analysis-runs/{rid}/generate', status_code=202)
def generate_report_in_background(rid: str):
    """Start report generation on a worker thread. Poll GET /api/tasks/{task_id} (or just GET the
    report, which answers 202 until it's ready). Answers 200 if the report already exists."""
    run, error = _claim_report_generation(rid)
    if error is not None:
        return error
    if run['status'] == 'report_ready' and run.get('report'):
        return JSONResponse({'run_id': rid, 'status': 'report_ready', 'task_id': run.get('task_id')}, status_code=200)
    task = TASK_QUEUE.submit('generate_report', lambda: _generate_report_task(rid), user_id=run.get('user_id', LOCAL_USER_ID), run_id=rid)
    run['task_id'] = task['id']
    RUNS[rid] = run
    return {'run_id': rid, 'task_id': task['id'], 'status': 'generating'}


def _generate_report_task(rid: str) -> dict:
    try:
        _generate_report_for_run(rid, RUNS[rid])
    except ReportGenerationError as exc:
        raise RuntimeError(exc.payload.get('error', 'report_generation_failed')) from exc
    return {'run_id': rid, 'status': 'report_ready'}


def _generate_report(rid: str, run: dict, submission: dict, partition, selected: list[dict]) -> dict:
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
    analysis_client = guarded_client_from_env('analysis', run.get('user_id'), run_id=rid)
    verify_client = guarded_client_from_env('verify', run.get('user_id'), run_id=rid)
    report.update(diagnose_records(records, client=analysis_client, verify_client=verify_client))
    report['review'] = review_report(report, partition)
    if report['review']['status'] != APPROVED:
        report['quality_flags'].append({'code': 'review_pending', 'severity': 'warning', 'detail': [item['code'] for item in report['review']['findings']], 'message': f"独立审查发现 {len(report['review']['findings'])} 处问题，{report['review']['pending_verification_questions']} 道验证题的参考答案在复核通过前不会展示"})
    report_client = guarded_client_from_env('report', run.get('user_id'), run_id=rid)
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
    RUNS[rid] = run
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
    attempt = {'id': str(uuid4()), **body.model_dump(), 'passed': body.score / body.max_score >= 0.8, 'graded_by': 'self'}
    # Auto-grade only against a reference answer that has passed independent review — the same
    # gate that decides whether the answer is shown to the student at all. Marking against an
    # unreviewed (possibly wrong or empty) reference would just launder a bad answer into a
    # confident-looking score. The self-reported score above stays as the fallback either way.
    if question.get('review_status') == APPROVED and str(question.get('answer') or '').strip():
        grade_client = guarded_client_from_env('grade', question.get('user_id'), verification_question_id=question_id)
        if grade_client is not None:
            graded = model_grade_attempt(grade_client, question['question_text'], question['answer'], body.answer)
            if graded is not None:
                attempt['auto_graded_score_ratio'] = graded['score_ratio']
                attempt['auto_graded_feedback'] = graded['feedback']
                attempt['passed'] = graded['score_ratio'] >= 0.8
                attempt['graded_by'] = 'model'
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
    from .export import EXPORT_MEDIA_TYPES, render_docx, render_pdf
    from .reporting import render_html, render_markdown
    if format == 'html':
        return HTMLResponse(render_html(run['report']))
    if format == 'md':
        return PlainTextResponse(render_markdown(run['report']), media_type='text/markdown')
    if format in EXPORT_MEDIA_TYPES:
        content = render_pdf(run['report']) if format == 'pdf' else render_docx(run['report'])
        # Binary formats are downloads, not pages: an ASCII-only filename keeps the header valid
        # everywhere without RFC 5987 encoding games, and the run id makes it findable later.
        filename = f'exam-diagnosis-report-{rid[:8]}.{format}'
        return Response(content, media_type=EXPORT_MEDIA_TYPES[format], headers={'Content-Disposition': f'attachment; filename="{filename}"'})
    return JSONResponse({'error': 'unsupported_format'}, status_code=422)

@app.get('/api/model-calls')
def list_model_calls(user_id: str = Depends(current_user_id), role: str | None = None, limit: int = 100):
    """Observability for the analysis/report/split/verify model roles: every call this user's
    actions triggered, success or not, with latency and token usage where the provider reported
    it. There was previously no way to answer "is the model actually being called, and is it
    working" short of reading server stdout."""
    limit = max(1, min(limit, MODEL_CALLS_MAX))
    all_calls = [item for item in MODEL_CALLS.values() if item.get('user_id', LOCAL_USER_ID) == user_id]
    calls = [item for item in all_calls if not role or item.get('role') == role]
    calls.sort(key=lambda item: item['created_at'], reverse=True)
    calls = calls[:limit]
    total = len(calls)
    succeeded = sum(1 for item in calls if item.get('success'))
    now = datetime.utcnow()
    today = now.date().isoformat()
    tokens_used_today = sum(item.get('total_tokens') or 0 for item in all_calls if item.get('success') and item['created_at'].startswith(today))
    calls_last_minute = sum(1 for item in all_calls if now - datetime.fromisoformat(item['created_at'].rstrip('Z')) < timedelta(minutes=1))
    return {
        'calls': calls,
        'summary': {
            'total': total, 'succeeded': succeeded, 'failed': total - succeeded,
            'avg_latency_ms': round(sum(item.get('latency_ms', 0) for item in calls) / total) if total else 0,
            'total_tokens': sum(item.get('total_tokens') or 0 for item in calls),
        },
        'budget': {
            'daily_token_budget': MODEL_DAILY_TOKEN_BUDGET,
            'tokens_used_today': tokens_used_today,
            'daily_tokens_remaining': (MODEL_DAILY_TOKEN_BUDGET - tokens_used_today) if MODEL_DAILY_TOKEN_BUDGET else None,
            'max_calls_per_minute': MODEL_MAX_CALLS_PER_MINUTE,
            'calls_in_last_minute': calls_last_minute,
        },
    }


@app.get('/api/tasks')
def list_tasks(user_id: str = Depends(current_user_id), status: str | None = None, limit: int = 50):
    tasks = [item for item in TASKS.values() if item.get('user_id', LOCAL_USER_ID) == user_id and (not status or item.get('status') == status)]
    tasks.sort(key=lambda item: item['created_at'], reverse=True)
    return {'tasks': tasks[:limit], 'workers': TASK_QUEUE.workers}


@app.get('/api/tasks/{task_id}')
def get_task(task_id: str):
    task = TASKS.get(task_id)
    if not task:
        return JSONResponse({'error': 'task_not_found'}, status_code=404)
    return task


def recover_orphaned_tasks() -> list[dict]:
    """Startup reconciliation: a task that was queued/running when the process last exited never
    finished, and the job or run it was working on is still marked as if it had. Mark the task
    failed (`process_restarted`) and put the job/run back into a state the client can act on —
    a running job becomes a failed one so /retry works, a generating run goes back to queued."""
    recovered = TASK_QUEUE.recover_orphaned()
    for task in recovered:
        if task.get('kind') == 'parse_exam':
            job = JOBS.get(task.get('job_id') or '')
            if job and job.get('status') == 'running':
                stage = next((item for item in job['stages'] if item['status'] == 'running'), None) or next((item for item in job['stages'] if item['status'] != 'completed'), None)
                if stage is not None:
                    stage['status'] = 'failed'
                    stage['error'] = 'process_restarted'
                    job['failed_stage'] = stage['name']
                job['status'] = 'failed'
                job['error'] = 'process_restarted'
                JOBS[job['id']] = job
        elif task.get('kind') == 'generate_report':
            run = RUNS.get(task.get('run_id') or '')
            if run and run.get('status') == 'generating':
                run['status'] = 'queued'
                RUNS[run['id']] = run
    if recovered:
        _flush_all_state()
    return recovered


recover_orphaned_tasks()


# --- Accounts -----------------------------------------------------------------------------------


class RegisterIn(BaseModel):
    username: str
    password: str
    display_name: str = ''


class LoginIn(BaseModel):
    username: str
    password: str


def public_user(user: dict) -> dict:
    return {'id': user['id'], 'username': user['username'], 'display_name': user.get('display_name') or user['username'], 'created_at': user.get('created_at')}


def _find_user(username: str) -> dict | None:
    return next((item for item in USERS.values() if item.get('username') == username), None)


def _open_session(user_id: str) -> tuple[str, dict]:
    token = auth.new_token()
    session = {'id': auth.token_key(token), 'user_id': user_id, 'created_at': datetime.utcnow().isoformat() + 'Z', 'expires_at': auth.expiry(SESSION_TTL_HOURS), 'revoked': False}
    AUTH_SESSIONS[session['id']] = session
    return token, session


def _session_response(user: dict, token: str, session: dict, status_code: int = 200):
    return JSONResponse({'user': public_user(user), 'token': token, 'expires_at': session['expires_at']}, status_code=status_code)


@app.post('/api/auth/register', status_code=201)
def register(body: RegisterIn):
    username = auth.normalize_username(body.username)
    problem = auth.validate_credentials(username, body.password)
    if problem:
        return JSONResponse({'error': problem}, status_code=422)
    if _find_user(username) is not None:
        return JSONResponse({'error': 'username_taken'}, status_code=409)
    user_id = str(uuid4())
    USERS[user_id] = {
        'id': user_id, 'username': username, 'display_name': body.display_name.strip() or username,
        'password_hash': auth.hash_password(body.password), 'created_at': datetime.utcnow().isoformat() + 'Z',
    }
    token, session = _open_session(user_id)
    return _session_response(USERS[user_id], token, session, status_code=201)


@app.post('/api/auth/login')
def login(body: LoginIn):
    username = auth.normalize_username(body.username)
    if LOGIN_THROTTLE.blocked(username):
        return JSONResponse({'error': 'too_many_attempts'}, status_code=429)
    user = _find_user(username)
    # One error for both "no such user" and "wrong password" — the split is exactly what an
    # enumeration attempt is fishing for.
    if user is None or not auth.verify_password(body.password, user.get('password_hash', '')):
        LOGIN_THROTTLE.record_failure(username)
        return JSONResponse({'error': 'invalid_credentials'}, status_code=401)
    LOGIN_THROTTLE.clear(username)
    token, session = _open_session(user['id'])
    return _session_response(user, token, session)


@app.post('/api/auth/logout')
def logout(request: Request):
    token = auth.bearer_token(request.headers.get('Authorization'))
    session = AUTH_SESSIONS.get(auth.token_key(token)) if token else None
    if session is not None:
        session['revoked'] = True
        AUTH_SESSIONS[session['id']] = session
    return {'ok': True}


@app.get('/api/auth/me')
def me(user_id: str = Depends(current_user_id)):
    user = USERS.get(user_id)
    if user is not None:
        return {'user': public_user(user), 'anonymous': False, 'auth_required': AUTH_REQUIRED}
    # Auth is optional and no token was sent: the caller is the header-named (or default local)
    # pseudo-user, which is a real identity for data ownership even though nobody logged in.
    return {'user': {'id': user_id, 'username': user_id, 'display_name': '本地用户' if user_id == LOCAL_USER_ID else user_id, 'created_at': None}, 'anonymous': True, 'auth_required': AUTH_REQUIRED}


# --- User-maintained knowledge bases --------------------------------------------------------------
#
# The product ships public partitions (data/knowledge/*.yaml); each user can keep their own
# partition per scope, either extending the public one (its points as the base, the user's added
# on top / overriding by knowledge_id) or standing alone for a scope the product doesn't cover.
# Curating the *content* is a later job — this is the mechanism it will run on.


class KnowledgePointIn(BaseModel):
    knowledge_id: str
    name: str
    chapter: str
    keywords: list[str] = []
    confidence: float = 0.8
    parent: str | None = None
    dependencies: list[str] = []
    grade_scope: str
    textbook_chapter: str = ''


class KnowledgeBaseIn(BaseModel):
    stage: str
    subject: str
    province: str
    curriculum_year: int
    academic_year_range: list[str] = []
    extends_public: bool = True
    name: str = ''
    version: str = ''
    curriculum: str | None = None
    knowledge_points: list[KnowledgePointIn] = []


KNOWLEDGE_BASE_RECORD_FIELDS = ('stage', 'subject', 'province', 'curriculum_year', 'academic_year_range', 'extends_public', 'name', 'version', 'curriculum', 'knowledge_points')


def _knowledge_base_view(record: dict) -> dict:
    """The stored record plus what it resolves to (merged point count, base version) — or the
    validation problem, if the public base has changed underneath it."""
    view = {field: record.get(field) for field in ('id', 'user_id', 'created_at', 'updated_at', *KNOWLEDGE_BASE_RECORD_FIELDS)}
    try:
        view['resolved'] = knowledge.user_partition_from_record(record).describe()
        view['problem'] = None
    except ValueError as exc:
        view['resolved'] = None
        view['problem'] = str(exc)
    return view


def _validate_knowledge_base(record: dict):
    """422 payload if the record doesn't validate, else None."""
    try:
        knowledge.user_partition_from_record(record)
    except ValueError as exc:
        reason = str(exc)
        code = 'public_partition_not_found' if reason.startswith('public partition not found') else 'invalid_knowledge_base'
        return JSONResponse({'error': code, 'detail': reason}, status_code=422)
    return None


def _same_scope_conflict(user_id: str, record: dict, exclude_id: str | None = None) -> bool:
    key = (record['stage'], record['subject'], record['province'], int(record['curriculum_year']))
    return any(
        item['id'] != exclude_id and (item['stage'], item['subject'], item['province'], int(item['curriculum_year'])) == key
        for item in USER_KNOWLEDGE.values() if item.get('user_id') == user_id
    )


@app.get('/api/knowledge-bases')
def list_knowledge_bases(user_id: str = Depends(current_user_id)):
    return {'knowledge_bases': [_knowledge_base_view(item) for item in USER_KNOWLEDGE.values() if item.get('user_id') == user_id]}


@app.post('/api/knowledge-bases', status_code=201)
def create_knowledge_base(body: KnowledgeBaseIn, user_id: str = Depends(current_user_id)):
    record = {'id': str(uuid4()), 'user_id': user_id, **body.model_dump(), 'created_at': datetime.utcnow().isoformat() + 'Z', 'updated_at': datetime.utcnow().isoformat() + 'Z'}
    if _same_scope_conflict(user_id, record):
        return JSONResponse({'error': 'knowledge_base_exists_for_scope'}, status_code=409)
    problem = _validate_knowledge_base(record)
    if problem is not None:
        return problem
    USER_KNOWLEDGE[record['id']] = record
    return _knowledge_base_view(record)


@app.get('/api/knowledge-bases/{kb_id}')
def get_knowledge_base(kb_id: str):
    record = USER_KNOWLEDGE.get(kb_id)
    if not record:
        return JSONResponse({'error': 'knowledge_base_not_found'}, status_code=404)
    return _knowledge_base_view(record)


@app.put('/api/knowledge-bases/{kb_id}')
def replace_knowledge_base(kb_id: str, body: KnowledgeBaseIn):
    record = USER_KNOWLEDGE.get(kb_id)
    if not record:
        return JSONResponse({'error': 'knowledge_base_not_found'}, status_code=404)
    updated = {**record, **body.model_dump(), 'updated_at': datetime.utcnow().isoformat() + 'Z'}
    if _same_scope_conflict(record['user_id'], updated, exclude_id=kb_id):
        return JSONResponse({'error': 'knowledge_base_exists_for_scope'}, status_code=409)
    problem = _validate_knowledge_base(updated)
    if problem is not None:
        return problem
    USER_KNOWLEDGE[kb_id] = updated
    return _knowledge_base_view(updated)


@app.delete('/api/knowledge-bases/{kb_id}')
def delete_knowledge_base(kb_id: str):
    if kb_id not in USER_KNOWLEDGE:
        return JSONResponse({'error': 'knowledge_base_not_found'}, status_code=404)
    del USER_KNOWLEDGE[kb_id]
    return {'deleted': kb_id}


@app.post('/api/knowledge-bases/{kb_id}/points')
def upsert_knowledge_point(kb_id: str, body: KnowledgePointIn):
    """Add a point, or replace the one with the same knowledge_id (which is also how a user
    overrides a public point when the base extends a public partition)."""
    record = USER_KNOWLEDGE.get(kb_id)
    if not record:
        return JSONResponse({'error': 'knowledge_base_not_found'}, status_code=404)
    point = body.model_dump()
    points = [item for item in record.get('knowledge_points', []) if item['knowledge_id'] != point['knowledge_id']] + [point]
    updated = {**record, 'knowledge_points': points, 'updated_at': datetime.utcnow().isoformat() + 'Z'}
    problem = _validate_knowledge_base(updated)
    if problem is not None:
        return problem
    USER_KNOWLEDGE[kb_id] = updated
    return _knowledge_base_view(updated)


@app.delete('/api/knowledge-bases/{kb_id}/points/{knowledge_id}')
def delete_knowledge_point(kb_id: str, knowledge_id: str):
    record = USER_KNOWLEDGE.get(kb_id)
    if not record:
        return JSONResponse({'error': 'knowledge_base_not_found'}, status_code=404)
    points = record.get('knowledge_points', [])
    if all(item['knowledge_id'] != knowledge_id for item in points):
        return JSONResponse({'error': 'knowledge_point_not_found'}, status_code=404)
    updated = {**record, 'knowledge_points': [item for item in points if item['knowledge_id'] != knowledge_id], 'updated_at': datetime.utcnow().isoformat() + 'Z'}
    problem = _validate_knowledge_base(updated)
    if problem is not None:
        # e.g. another user point still depends on the one being removed
        return problem
    USER_KNOWLEDGE[kb_id] = updated
    return _knowledge_base_view(updated)


@app.post('/api/knowledge-bases/import', status_code=201)
def import_knowledge_base(file: UploadFile = File(...), user_id: str = Depends(current_user_id)):
    """Create a user knowledge base from a YAML file in the same schema as the public partitions
    (plus optional `extends_public` / `name`), so a base can be maintained in a file and re-imported."""
    try:
        payload = yaml.safe_load(file.file.read().decode('utf-8'))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        return JSONResponse({'error': 'invalid_yaml', 'detail': str(exc)}, status_code=422)
    if not isinstance(payload, dict):
        return JSONResponse({'error': 'invalid_yaml', 'detail': 'top level must be a mapping'}, status_code=422)
    try:
        body = KnowledgeBaseIn(**{key: payload[key] for key in KNOWLEDGE_BASE_RECORD_FIELDS if key in payload and key != 'extends_public'}, extends_public=bool(payload.get('extends_public', False)))
    except ValidationError as exc:
        return JSONResponse({'error': 'invalid_knowledge_base', 'detail': str(exc)}, status_code=422)
    return create_knowledge_base(body, user_id)


@app.get('/api/knowledge-bases/{kb_id}/export')
def export_knowledge_base(kb_id: str):
    record = USER_KNOWLEDGE.get(kb_id)
    if not record:
        return JSONResponse({'error': 'knowledge_base_not_found'}, status_code=404)
    payload = {field: record.get(field) for field in KNOWLEDGE_BASE_RECORD_FIELDS}
    payload['version'] = payload['version'] or '1.0.0'
    text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    return Response(text, media_type='application/x-yaml', headers={'Content-Disposition': f'attachment; filename="knowledge-base-{kb_id[:8]}.yaml"'})



# --- System status ------------------------------------------------------------------------------


@app.get('/api/system/status')
def system_status(user_id: str = Depends(current_user_id)):
    """One place to answer "is the model actually on, and what is this server running with" —
    previously that took a curl of /api/model-calls and reading the env by hand. Never includes
    a key, only whether each role has one."""
    vision = OpenAICompatibleVisionParser()
    roles = [llm_role_status(role) for role in ROLE_DEFAULTS]
    roles.append({
        'role': 'vision', 'purpose': '图片 / 扫描 PDF 视觉解析', 'configured': vision.configured,
        'base_url': vision.base_url, 'model': vision.model, 'timeout': vision.timeout, 'env_prefix': 'EXAM_DIAGNOSIS_VISION_',
    })
    my_tasks = [item for item in TASKS.values() if item.get('user_id', LOCAL_USER_ID) == user_id]
    return {
        'application': {'name': app.title, 'version': app.version},
        'auth': {'required': AUTH_REQUIRED, 'session_ttl_hours': SESSION_TTL_HOURS},
        'storage': {'data_dir': str(paths.data_dir()), 'database': str(SUBMISSIONS.path), 'uploads': str(paths.uploads_dir()), 'config': str(paths.config_path())},
        'model_roles': roles,
        'budget': {
            'daily_token_budget': MODEL_DAILY_TOKEN_BUDGET,
            'max_calls_per_minute': MODEL_MAX_CALLS_PER_MINUTE,
            'block_reason': _budget_block_reason(user_id),
        },
        'tasks': {
            'workers': TASK_QUEUE.workers,
            'active': sum(1 for item in my_tasks if item.get('status') in ('queued', 'running')),
            'failed': sum(1 for item in my_tasks if item.get('status') == 'failed'),
        },
        'knowledge': {'partitions': available_partitions(user_id=user_id)},
    }


# --- Health + runtime settings --------------------------------------------------------------------


@app.get('/api/health')
def health():
    """Liveness for whatever supervises this process (the desktop shell polls it after spawning
    the backend). Public: no auth, no user context."""
    return {'status': 'ok', 'application': app.title, 'version': app.version, 'data_dir': str(paths.data_dir()), 'auth_required': AUTH_REQUIRED}


class SettingsIn(BaseModel):
    roles: dict[str, dict[str, Any]] = {}
    budget: dict[str, Any] = {}


@app.get('/api/settings')
def get_settings():
    return {**app_settings.describe(), 'auth_required': AUTH_REQUIRED, 'writable': not AUTH_REQUIRED}


@app.put('/api/settings')
def update_settings(body: SettingsIn):
    """Change model credentials / budget from the UI. Refused in multi-user mode: these are
    server-wide, and a shared server is configured by its operator through the environment, not
    by whichever account happens to be logged in."""
    if AUTH_REQUIRED:
        return JSONResponse({'error': 'settings_locked_in_multi_user_mode'}, status_code=403)
    locked = app_settings.locked_fields(body.roles, body.budget)
    if locked:
        return JSONResponse({'error': 'locked_by_environment', 'fields': locked}, status_code=409)
    try:
        config = app_settings.merge_changes(app_settings.load_config(), body.model_dump())
    except ValueError as exc:
        return JSONResponse({'error': 'invalid_settings', 'detail': str(exc)}, status_code=422)
    app_settings.save_config(config)
    app_settings.apply_config(config)
    _refresh_budget_from_env()
    return get_settings()


@app.get('/api/submissions/{sid}/trends')
def submission_trends(sid: str):
    """How this student's exams compare over time. One report says what went wrong once; this
    follows each knowledge point across every exam in the submission."""
    submission = SUBMISSIONS.get(sid)
    if not submission:
        return JSONResponse({'error': 'submission_not_found'}, status_code=404)
    exams = [item for item in EXAMS.values() if item['submission_id'] == sid]
    records_by_exam: dict[str, list[dict]] = {exam['id']: [] for exam in exams}
    for record in QUESTION_RECORDS.values():
        if record['status'] == 'confirmed' and record['exam_id'] in records_by_exam:
            records_by_exam[record['exam_id']].append(record)
    # Knowledge points are mapped lazily (report generation does the same): a confirmed record
    # has none until something asks. Cached on the record, so this costs at most one mapping per
    # question no matter how often the trend is opened.
    try:
        for records in records_by_exam.values():
            for record in records:
                if not record.get('knowledge_points'):
                    apply_knowledge_mapping(record)
    except KnowledgePartitionNotFound as missing:
        return JSONResponse({'error': 'knowledge_partition_not_found', 'requested': {'stage': missing.stage, 'subject': missing.subject, 'province': missing.province}}, status_code=422)
    reports = [run['report'] for run in RUNS.values() if run.get('submission_id') == sid and run.get('report')]
    return {'submission_id': sid, **build_trends(exams, records_by_exam, reports)}
