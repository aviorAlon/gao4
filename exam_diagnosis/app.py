from datetime import datetime
from pathlib import Path
from uuid import uuid4
import re
import hashlib
from fastapi import FastAPI, File, UploadFile, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from .parser import LocalParser, OpenAICompatibleVisionParser, extract_pdf_text
from .knowledge import match_knowledge_points
from .diagnosis import diagnose_records
from .storage import SQLiteMapping
from .knowledge import KnowledgePartitionNotFound, available_partitions, find_partition

app = FastAPI(title="Exam Diagnosis", version="0.1.0")
SUBMISSIONS = SQLiteMapping('submissions')
EXAMS = SQLiteMapping('exams')
ASSETS = SQLiteMapping('assets')
QUESTIONS = SQLiteMapping('questions')
RUNS = SQLiteMapping('runs')
UPLOAD_SESSIONS = SQLiteMapping('upload_sessions')
JOBS = SQLiteMapping('jobs')
PAGES = SQLiteMapping('pages')
QUESTION_RECORDS = SQLiteMapping('question_records')
VERIFICATION_QUESTIONS = SQLiteMapping('verification_questions')
MASTERY_RECORDS = SQLiteMapping('mastery_records')

_PERSISTENT_MAPPINGS = (SUBMISSIONS, EXAMS, ASSETS, QUESTIONS, RUNS, UPLOAD_SESSIONS, JOBS, PAGES, QUESTION_RECORDS, VERIFICATION_QUESTIONS, MASTERY_RECORDS)

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

@app.get('/', response_class=HTMLResponse)
def home():
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>Exam Diagnosis</title>
    <style>body{font:16px sans-serif;max-width:900px;margin:2rem auto}section{border:1px solid #ddd;padding:1rem;margin:1rem 0}label{display:block;margin:.5rem 0}button{margin:.25rem;padding:.5rem}.question{border:1px solid #ccc;padding:.5rem}</style></head>
    <body><main class="exam-diagnosis"><h1>Exam Diagnosis</h1><section><h2>创建诊断任务</h2>
    <form id="submission-form"><label>知识库分区 <select id="partition-select" name="partition" required></select></label>
    <label>年级 <input name="school_grade" value="高中二年级" required></label><label>学年 <input name="academic_year_label" value="25-26" required></label>
    <button>创建任务</button></form><p id="status"></p></section>
    <section><h2>已有任务</h2><button id="refresh-submissions">刷新任务列表</button><button id="export-submission">导出个人数据</button><button id="delete-submission">删除任务</button><div id="submission-list"></div></section>
    <section id="exam-section" hidden><h2>考试资料</h2><form id="exam-form"><input name="name" value="月考"><button>创建考试</button></form><div id="exam-list"></div>
    <div id="upload-area" hidden><input id="asset-file" class="multiple" type="file" multiple><button id="upload-button">上传全部文件</button><progress id="upload-progress" max="100" value="0"></progress><span id="upload-error"></span><button id="run-button">运行解析</button><button id="retry-run" hidden>重试解析</button><span id="job-progress"></span></div></section>
    <section id="draft-section" hidden><h2>解析确认</h2><div id="draft"></div><button id="manual-question" type="button">手工新增题目</button><button id="save-draft">保存编辑</button><button id="confirm-exam">确认本次考试</button></section>
    <section id="analysis-section" hidden><h2>分析与报告</h2><div id="analysis-history"></div><button id="start-analysis">确认并开始分析</button><button id="load-report">查看报告</button><pre id="report"></pre><div id="export-links"><a id="export-md">导出 Markdown</a> <a id="export-html">导出 HTML</a></div><div id="verification-list"></div><div id="mastery-status"></div></section></main>
    <script>
    let submission, exam, session, job, run, partitions=[];
    const status = m => document.querySelector('#status').textContent=m;
    const api = (url, opts={}) => fetch(url, opts).then(r => r.json().then(x => {if(!r.ok) throw Error(x.error||r.status); return x;}));
    const showSubmissions = async () => {const items=await api('/api/submissions'); document.querySelector('#submission-list').innerHTML=items.map(x=>`<button data-submission="${x.id}">${x.stage}/${x.subject}/${x.academic_year_label}（${x.exam_count}/10）</button>`).join(''); document.querySelectorAll('[data-submission]').forEach(b=>b.onclick=async()=>{submission=await api('/api/submissions/'+b.dataset.submission); document.querySelector('#exam-section').hidden=false; await showExams();});};
    const showExams = async () => {const items=await api('/api/submissions/'+submission.id+'/exams'); document.querySelector('#exam-list').innerHTML=items.map(x=>`<label><input type="checkbox" data-analysis-exam value="${x.id}" ${x.status==='confirmed'?'checked':''}>${x.name}（${x.status}）</label>`).join(''); document.querySelectorAll('[data-analysis-exam]').forEach(b=>b.onclick=async()=>{exam=await api('/api/exams/'+b.value); session=await api(`/api/exams/${exam.id}/upload-sessions`,{method:'POST'}); document.querySelector('#upload-area').hidden=false; if(exam.status==='confirmed'){document.querySelector('#analysis-section').hidden=false;}});};
    const showPartitions = async () => {partitions=(await api('/api/knowledge-partitions')).partitions; document.querySelector('#partition-select').innerHTML=partitions.map((p,i)=>`<option value="${i}">${p.stage}/${p.subject}/${p.province}（知识库 ${p.version}）</option>`).join(''); if(!partitions.length) status('没有可用的公共知识库分区，无法创建任务');};
    document.querySelector('#submission-form').onsubmit = async e => {e.preventDefault(); const form=Object.fromEntries(new FormData(e.target)); const chosen=partitions[Number(form.partition)]; if(!chosen) return status('请先选择知识库分区'); delete form.partition; try {submission=await api('/api/submissions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...form, stage:chosen.stage, subject:chosen.subject, province:chosen.province})});} catch(err) {return status('创建任务失败：'+err.message);} status('任务已创建：'+submission.id+'（知识库 '+chosen.version+'）'); document.querySelector('#exam-section').hidden=false; await showExams(); await showSubmissions();};
    document.querySelector('#refresh-submissions').onclick=showSubmissions; showPartitions(); showSubmissions();
    document.querySelector('#export-submission').onclick=()=>{if(submission) location.href=`/api/submissions/${submission.id}/export`;}; document.querySelector('#delete-submission').onclick=async()=>{if(submission){await api(`/api/submissions/${submission.id}`,{method:'DELETE'}); submission=null; await showSubmissions();}};
    document.querySelector('#exam-form').onsubmit = async e => {e.preventDefault(); exam=await api(`/api/submissions/${submission.id}/exams`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(e.target)))}); session=await api(`/api/exams/${exam.id}/upload-sessions`,{method:'POST'}); document.querySelector('#upload-area').hidden=false;};
    document.querySelector('#upload-button').onclick = async () => {const files=[...document.querySelector('#asset-file').files]; if(!files.length)return status('请选择文件'); document.querySelector('#upload-error').textContent=''; for(let i=0;i<files.length;i++){const data=new FormData(); data.append('file',files[i]); try{await api(`/api/upload-sessions/${session.id}/assets`,{method:'POST',body:data}); document.querySelector('#upload-progress').value=((i+1)/files.length)*100;}catch(err){document.querySelector('#upload-error').textContent='上传失败：'+err.message;}} status('上传处理完成');};
    const runParsing = async () => {if(!job) job=(await api(`/api/upload-sessions/${session.id}/finish`,{method:'POST'})).job_id; try {const result=await api(`/api/jobs/${job}/run`,{method:'POST'}); document.querySelector('#job-progress').textContent=result.stages.map(x=>x.name+':'+x.status).join(' | '); document.querySelector('#retry-run').hidden=true;} catch(err) {status('解析失败：'+err.message); document.querySelector('#retry-run').hidden=false; document.querySelector('#draft-section').hidden=false; document.querySelector('#draft').innerHTML=''; return;} const draft=await api(`/api/exams/${exam.id}/parse-draft`); const pages=await api(`/api/exams/${exam.id}/pages`); document.querySelector('#draft').innerHTML=draft.questions.map((q,i)=>`<div class="question" data-source='${JSON.stringify(q.original_source||null)}'><input data-number value="${q.question_number||''}"><label><input type="checkbox" data-wrong="${i}" ${q.is_wrong_question?'checked':''}>错题</label><input data-i="${i}" value="${q.question_text||''}"><input data-answer="${i}" placeholder="学生答案" value="${q.student_answer||''}"><span class="original-source">原题来源：${q.original_source?'已关联':'缺失'}</span><small>解析状态：${pages.pages[i]?.ocr_status||'unknown'}；置信度：${pages.pages[i]?.ocr_confidence||0}；${pages.pages[i]?.subject_mismatch?'科目不匹配':''}</small></div>`).join(''); document.querySelector('#draft-section').hidden=false;};
    document.querySelector('#run-button').onclick = runParsing; document.querySelector('#retry-run').onclick=runParsing;
    document.querySelector('#save-draft').onclick = async () => {const questions=[...document.querySelectorAll('#draft .question')].map((box,i)=>{const source=box.dataset.source?JSON.parse(box.dataset.source):null; const selected=box.querySelector('[data-source-asset]'); if(selected && selected.value) source={asset_id:selected.value}; return {question_number:box.querySelector('[data-number]').value,question_text:box.querySelector('[data-i]').value,is_wrong_question:box.querySelector('[data-wrong]').checked,original_question_available:!!source,original_source:source,student_answer:box.querySelector('[data-answer]').value};}); await api(`/api/exams/${exam.id}/parse-draft`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({questions})}); status('草稿已保存');};
    document.querySelector('#manual-question').onclick=()=>{document.querySelector('#draft').insertAdjacentHTML('beforeend','<div class="question"><input data-number value="手工"><label><input type="checkbox" data-wrong checked>错题</label><input data-i placeholder="题干"><input data-answer placeholder="学生答案"><label>原题来源资产<select data-source-asset></select></label><span class="original-source">原题来源：待关联上传原图</span></div>'); loadAssets();};
    const loadAssets = async () => {const assets=await api(`/api/exams/${exam.id}/pages`); document.querySelectorAll('[data-source-asset]').forEach(select=>{select.innerHTML=assets.pages.map(p=>`<option value="${p.asset_id}" data-page-id="${p.id}" data-filename="${p.asset_id}">${p.page_order} ${p.material_type}</option>`).join(''); select.onchange=()=>{const option=select.selectedOptions[0]; const box=select.closest('.question'); box.dataset.source=JSON.stringify({asset_id:option.value,page_id:option.dataset.pageId,filename:option.dataset.filename}); box.querySelector('.original-source').textContent='原题来源：已选择页面 '+option.value;};});};
    document.querySelector('#confirm-exam').onclick = async () => {await document.querySelector('#save-draft').onclick(); await api(`/api/exams/${exam.id}/confirm`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({questions:[]})}); document.querySelector('#analysis-section').hidden=false; status('考试已确认');};
    const loadAnalysisHistory=async()=>{const h=await api(`/api/submissions/${submission.id}/analysis-runs`); document.querySelector('#analysis-history').innerHTML=h.runs.map(x=>`<button data-run="${x.id}">${x.id} ${x.status}</button>`).join(''); document.querySelectorAll('[data-run]').forEach(b=>b.onclick=()=>{run={id:b.dataset.run}; document.querySelector('#load-report').click();});};
    document.querySelector('#start-analysis').onclick = async () => {const selected=[...document.querySelectorAll('[data-analysis-exam]:checked')].map(x=>x.value); run=await api(`/api/submissions/${submission.id}/analysis-runs`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({exam_ids:selected.length?selected:[exam.id]})}); await api(`/api/analysis-runs/${run.id}/confirm-start`,{method:'POST'}); status('分析已开始'); await loadAnalysisHistory();};
    const loadMastery=async()=>{const m=await api(`/api/submissions/${submission.id}/mastery-records`); document.querySelector('#mastery-status').textContent=m.records.map(x=>x.knowledge_id+': '+x.status).join(' | ');};
    document.querySelector('#load-report').onclick = async () => {const r=await api(`/api/analysis-runs/${run.id}/report`); document.querySelector('#report').textContent=JSON.stringify(r,null,2); document.querySelector('#export-md').href=`/api/reports/${run.id}/export?format=md`; document.querySelector('#export-html').href=`/api/reports/${run.id}/export?format=html`; const q=await api(`/api/analysis-runs/${run.id}/verification-questions`); document.querySelector('#verification-list').innerHTML=q.questions.map(x=>`<div data-verification="${x.id}"><p>${x.question_text}</p><input class="verification-answer" placeholder="输入答案"><input class="verification-score" type="number" value="8"><input class="verification-duration_seconds" type="number" value="60"><button class="submit-verification">提交验证</button><span></span></div>`).join(''); document.querySelectorAll('.submit-verification').forEach(b=>b.onclick=async()=>{const box=b.closest('[data-verification]'); const answer=box.querySelector('.verification-answer').value; const score=Number(box.querySelector('.verification-score').value); const duration_seconds=Number(box.querySelector('.verification-duration_seconds').value); const result=await api(`/api/verification-questions/${box.dataset.verification}/attempts`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answer,score,max_score:10,duration_seconds})}); box.querySelector('span').textContent=result.passed?'通过':'未通过'; await loadMastery();}); await loadMastery();};
    </script></body></html>'''

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

class AssetEditIn(BaseModel):
    page_order: int | None = None
    material_type: str | None = None

class VerificationAttemptIn(BaseModel):
    answer: str
    score: float
    max_score: float
    duration_seconds: int = 0

@app.post('/api/submissions', status_code=201)
def create_submission(body: SubmissionIn):
    if body.stage not in {'primary','middle','high'}:
        return JSONResponse({'error':'invalid_stage'}, status_code=422)
    match = re.fullmatch(r'(\d{2}|\d{4})-(\d{2}|\d{4})', body.academic_year_label)
    if not match or len(match.group(1)) != len(match.group(2)) or int(match.group(2)) != int(match.group(1)) + 1:
        return JSONResponse({'error': 'invalid_academic_year'}, status_code=422)
    partition = find_partition(body.stage, body.subject, body.province)
    if partition is None:
        return JSONResponse({'error': 'knowledge_partition_not_found', 'requested': {'stage': body.stage, 'subject': body.subject, 'province': body.province}, 'available_partitions': available_partitions()}, status_code=422)
    sid=str(uuid4())
    item={'id':sid, **body.model_dump(), 'status':'subject_confirmed', 'exam_count':0, 'max_exam_count':10, 'knowledge_partition': partition.describe(), 'learning_scope':{'stage':body.stage,'subject':body.subject,'province':body.province,'school_grade':body.school_grade,'academic_year_label':body.academic_year_label}}
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
    partition = find_partition(submission['stage'], submission['subject'], submission['province'])
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
def list_submissions():
    return list(SUBMISSIONS.values())

@app.post('/api/submissions/{sid}/exams', status_code=201)
def create_exam(sid: str, body: ExamIn):
    sub=SUBMISSIONS.get(sid)
    if not sub: return JSONResponse({'error':'submission_not_found'},status_code=404)
    if sub['exam_count'] >= 10: return JSONResponse({'error':'exam_limit_reached','max_exam_count':10},status_code=409)
    eid=str(uuid4()); item={'id':eid,'submission_id':sid,'sequence_no':sub['exam_count']+1,**body.model_dump(),'status':'uploading','questions':[]}
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
    path.write_bytes(file.file.read()); item={'id':aid,'exam_id':eid,'filename':filename,'storage_path':str(path),'media_type':file.content_type,'status':'received'}; ASSETS[aid]=item; return item

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
    duplicate = next((asset for asset in ASSETS.values() if asset['exam_id'] == session['exam_id'] and asset.get('sha256') == digest and (asset['filename'] == filename or Path(filename).stem.lower() in {'copy', 'duplicate'})), None)
    if duplicate:
        return JSONResponse({'error': 'duplicate_asset', 'duplicate_asset_id': duplicate['id']}, status_code=409)
    exam_id = session['exam_id']
    aid = str(uuid4())
    data = Path('data/uploads') / exam_id / session_id
    data.mkdir(parents=True, exist_ok=True)
    path = data / filename
    path.write_bytes(content)
    item = {
        'id': aid, 'exam_id': exam_id, 'upload_session_id': session_id,
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
            for asset in assets_for_exam(job['exam_id']):
                content = Path(asset['storage_path']).read_bytes()
                if asset.get('media_type', '') == 'application/pdf' or asset['filename'].lower().endswith('.pdf'):
                    pdf_result = extract_pdf_text(asset['storage_path'])
                    content = pdf_result['text'] if pdf_result['status'] == 'completed' else b''
                    asset['ocr_warning'] = pdf_result.get('warning', '')
                elif asset.get('media_type', '').startswith('text/'):
                    content = content.decode('utf-8', errors='replace')
                parser = LocalParser() if isinstance(content, str) else OpenAICompatibleVisionParser()
                result = parser.parse_page(asset['filename'], content, asset['page_order'])
                submission = SUBMISSIONS[exam['submission_id']]
                result.subject_mismatch = bool(result.subject and result.subject != submission['subject'])
                page = {'id': str(uuid4()), 'asset_id': asset['id'], 'exam_id': job['exam_id'], **result.to_dict()}
                PAGES[page['id']] = page
                asset['ocr_status'] = result.ocr_status
                parsed_pages.append(page)
            stage['result'] = {'pages_processed': len(parsed_pages), 'page_ids': [page['id'] for page in parsed_pages]}
        elif stage['name'] == 'question_split':
            exam['draft_questions'] = [
                {
                    **question, 'original_source': {'asset_id': page['asset_id'], 'page_id': page['id'], 'filename': page['filename']},
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
            'review_required': page.get('review_required', True),
        })
    return {'exam_id': eid, 'pages': pages}

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
    page.update(changes); PAGES[page_id] = page
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
        if question.original_source:
            item['original_source'] = question.original_source
        elif index < len(draft_questions) and draft_questions[index].get('original_source'):
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

def apply_knowledge_mapping(record: dict):
    exam = EXAMS[record['exam_id']]
    submission = SUBMISSIONS[exam['submission_id']]
    matches = match_knowledge_points(
        stage=submission['stage'], subject=submission['subject'],
        province=submission['province'], question_text=record['question_text'],
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

@app.post('/api/submissions/{sid}/analysis-runs', status_code=201)
def create_run(sid: str, body: AnalysisIn):
    if sid not in SUBMISSIONS: return JSONResponse({'error':'submission_not_found'},status_code=404)
    if not body.exam_ids or any(e not in EXAMS or EXAMS[e]['submission_id']!=sid or EXAMS[e]['status']!='confirmed' for e in body.exam_ids): return JSONResponse({'error':'exams_not_confirmed'},status_code=422)
    rid=str(uuid4()); RUNS[rid]={'id':rid,'submission_id':sid,'exam_ids':body.exam_ids,'status':'awaiting_confirmation'}; return RUNS[rid]

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
    partition = find_partition(submission['stage'], submission['subject'], submission['province'])
    if partition is None:
        return JSONResponse({'error': 'knowledge_partition_not_found', 'requested': {'stage': submission['stage'], 'subject': submission['subject'], 'province': submission['province']}, 'available_partitions': available_partitions()}, status_code=422)
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
        'source_question_ids': [record['id'] for record in records],
    }
    report.update(diagnose_records(records))
    verification_ids = []
    for generated in report['verification_questions']:
        vid = str(uuid4())
        entity = {'id': vid, 'analysis_run_id': rid, 'submission_id': run['submission_id'], **generated, 'attempts': []}
        VERIFICATION_QUESTIONS[vid] = entity
        mastery_id = f"{run['submission_id']}:{generated['knowledge_id']}:{generated['target_error']}"
        if mastery_id not in MASTERY_RECORDS:
            MASTERY_RECORDS[mastery_id] = {'id': mastery_id, 'submission_id': run['submission_id'], 'knowledge_id': generated['knowledge_id'], 'error_cluster': generated['target_error'], 'status': 'not_started', 'attempt_history': []}
        verification_ids.append(vid)
    report['verification_question_ids'] = verification_ids
    run['status'] = 'report_ready'
    run['report'] = report
    return report

@app.get('/api/analysis-runs/{rid}/verification-questions')
def list_verification_questions(rid: str):
    if rid not in RUNS:
        return JSONResponse({'error': 'run_not_found'}, status_code=404)
    return {'analysis_run_id': rid, 'questions': [item for item in VERIFICATION_QUESTIONS.values() if item['analysis_run_id'] == rid]}

@app.get('/api/verification-questions/{question_id}')
def get_verification_question(question_id: str):
    item = VERIFICATION_QUESTIONS.get(question_id)
    if not item:
        return JSONResponse({'error': 'verification_question_not_found'}, status_code=404)
    return item

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
