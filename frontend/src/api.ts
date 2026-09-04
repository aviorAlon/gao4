import axios from 'axios'

export const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8765'

export const api = axios.create({
  baseURL: API_BASE,
  // Identity when nobody is logged in and the backend runs with auth optional (the default local
  // single-user setup). Ignored by the backend whenever a bearer token is sent or auth is required.
  headers: { 'X-User-Id': 'local' },
})

// --- Session token ---

export const TOKEN_KEY = 'exam_diagnosis_token'

export function getToken(): string | null {
  try { return localStorage.getItem(TOKEN_KEY) } catch { return null }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch { /* storage unavailable (private mode) — session lasts for this page load only */ }
}

api.interceptors.request.use((config) => {
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error?.response?.status
    const url: string = error?.config?.url ?? ''
    // A 401 anywhere except the login/register calls themselves means our session is gone (or
    // the backend requires one we don't have): drop the stale token and go sign in.
    if (status === 401 && !url.startsWith('/api/auth/login') && !url.startsWith('/api/auth/register')) {
      setToken(null)
      if (window.location.pathname !== '/login') {
        window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`)
      }
    }
    return Promise.reject(error)
  },
)

export interface AuthUser { id: string; username: string; display_name: string; created_at: string | null }
export interface SessionResponse { user: AuthUser; token: string; expires_at: string }
export interface MeResponse { user: AuthUser; anonymous: boolean; auth_required: boolean }

export async function register(username: string, password: string, displayName?: string): Promise<SessionResponse> {
  const { data } = await api.post<SessionResponse>('/api/auth/register', { username, password, display_name: displayName ?? '' })
  setToken(data.token)
  return data
}

export async function login(username: string, password: string): Promise<SessionResponse> {
  const { data } = await api.post<SessionResponse>('/api/auth/login', { username, password })
  setToken(data.token)
  return data
}

export async function logout(): Promise<void> {
  try { await api.post('/api/auth/logout') } finally { setToken(null) }
}

export async function fetchMe(): Promise<MeResponse> {
  const { data } = await api.get<MeResponse>('/api/auth/me')
  return data
}

// --- Report types, matching the shape returned by GET /api/analysis-runs/{id}/report ---
// Kept as a hand-written mirror of the backend's dict shape (exam_diagnosis/app.py,
// diagnosis.py, reporting.py) rather than generated, since the backend has no OpenAPI schema
// export set up yet.

export interface KnowledgeCoverageItem {
  knowledge_id: string
  name: string
  chapter: string
  question_count: number
  wrong_count: number
}

export interface QualityFlag {
  code: string
  severity: 'warning' | 'info' | string
  message: string
  exam_id?: string
  exam_name?: string
  detail?: unknown
}

export interface ScopeCoverage {
  curriculum_point_count: number
  assessed_point_count: number
  not_assessed_point_count: number
  coverage_ratio: number
  not_assessed_chapters: string[]
  note: string
}

export interface ExamVersionRef {
  exam_id: string
  name: string
  version: number
}

export interface QuestionExample {
  question_id: string
  question_text: string
  student_answer: string
}

export interface ErrorCluster {
  cluster_id: string
  knowledge_id: string
  root_cause: string
  question_ids: string[]
  occurrence_count: number
  priority: number
  avoidance_advice: string
  question_examples: QuestionExample[]
  evidence: Array<{ question_id: string; original_source: unknown }>
}

export interface KnowledgeTreeNode {
  question_count: number
  wrong_count: number
  knowledge_id: string
  error_rate: number
  review_priority: 'high' | 'medium' | 'low' | 'none' | string
  priority_score: number
}

export type KnowledgeTree = Record<string, Record<string, KnowledgeTreeNode>>

export interface ReviewOrderItem extends KnowledgeTreeNode {
  chapter: string
  name: string
}

export interface VerificationQuestion {
  knowledge_id: string
  question_text: string
  answer: string | null
  target_error: string
  source_question_ids: string[]
  review_status: 'approved' | 'needs_review' | string
  review_findings: string[]
  answer_withheld: boolean
  withheld_reason?: string[]
}

export interface WrongQuestion {
  question_id: string
  question_text: string
  student_answer: string
  original_source: unknown
  knowledge_points: unknown[]
  root_cause: string
  avoidance_advice: string
  explanation?: string
}

export interface ReviewSummary {
  status: 'approved' | 'needs_review' | string
  checked: { clusters: number; verification_questions: number }
  pending_verification_questions: number
  findings: Array<{ code: string; target: string; message: string }>
}

export interface AnalysisReport {
  analysis_run_id: string
  status: string
  wrong_question_count: number
  question_count: number
  knowledge_coverage: KnowledgeCoverageItem[]
  unmapped_question_ids: string[]
  knowledge_base: {
    stage: string; subject: string; province: string; curriculum_year: number
    academic_year_range: string[]; version: string; curriculum: string
    knowledge_point_count: number; resource: string
  }
  scope_coverage: ScopeCoverage
  exam_versions: ExamVersionRef[]
  quality_flags: QualityFlag[]
  root_cause_counts: Record<string, number>
  clusters: ErrorCluster[]
  knowledge_tree: KnowledgeTree
  review_order: ReviewOrderItem[]
  verification_questions: VerificationQuestion[]
  wrong_question_collection: WrongQuestion[]
  review: ReviewSummary
  summary?: string
}

export type ReportFetchResult =
  | { kind: 'ready'; report: AnalysisReport }
  | { kind: 'generating'; task_id?: string }

/** GET the report. 202 means a generation is in flight (ours or another request's) — poll. */
export async function fetchReport(runId: string): Promise<ReportFetchResult> {
  const res = await api.get(`/api/analysis-runs/${runId}/report`)
  if (res.status === 202) return { kind: 'generating', task_id: res.data?.task_id }
  return { kind: 'ready', report: res.data as AnalysisReport }
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

/** Generate on the backend's worker thread and poll until the report exists. Report generation
 * makes several model calls and can take tens of seconds; holding one GET open that long was
 * exactly how browser/proxy timeouts turned a finished report into "加载失败". Errors the user
 * has to act on (run never started, knowledge partition missing, exam re-parsed) still reject. */
export async function loadReport(runId: string, onWaiting?: () => void): Promise<AnalysisReport> {
  await api.post(`/api/analysis-runs/${runId}/generate`)
  for (;;) {
    const result = await fetchReport(runId)
    if (result.kind === 'ready') return result.report
    onWaiting?.()
    await sleep(2000)
  }
}

// --- Knowledge partitions ---

export interface KnowledgePartition {
  stage: string
  subject: string
  province: string
  curriculum_year: number
  academic_year_range: string[]
  version: string
  curriculum: string
  knowledge_point_count: number
  resource: string
}

export async function listKnowledgePartitions(): Promise<KnowledgePartition[]> {
  const { data } = await api.get<{ partitions: KnowledgePartition[] }>('/api/knowledge-partitions')
  return data.partitions
}

// --- Submissions (任务) ---

export interface Submission {
  id: string
  stage: string
  subject: string
  province: string
  school_grade: string
  academic_year_label: string
  curriculum_year: number
  status: string
  exam_count: number
  max_exam_count: number
  knowledge_partition: KnowledgePartition
}

export interface CreateSubmissionInput {
  stage: string
  subject: string
  province: string
  school_grade: string
  academic_year_label: string
  curriculum_year?: number
}

export async function listSubmissions(): Promise<Submission[]> {
  const { data } = await api.get<Submission[]>('/api/submissions')
  return data
}

export async function createSubmission(input: CreateSubmissionInput): Promise<Submission> {
  const { data } = await api.post<Submission>('/api/submissions', input)
  return data
}

export async function getSubmission(id: string): Promise<Submission> {
  const { data } = await api.get<Submission>(`/api/submissions/${id}`)
  return data
}

export async function deleteSubmission(id: string): Promise<void> {
  await api.delete(`/api/submissions/${id}`)
}

// --- Exams (考试) ---

export interface DraftQuestion {
  id: string
  question_number: string
  question_text: string
  is_wrong_question: boolean
  original_question_available: boolean
  student_answer?: string | null
  original_source?: { asset_id: string; page_id?: string; filename: string } | null
  confirmation_status: string
}

export interface Exam {
  id: string
  submission_id: string
  sequence_no: number
  name: string
  exam_date: string | null
  exam_type: string | null
  status: string
  version?: number
  questions: unknown[]
  draft_questions: DraftQuestion[]
  excluded_pages: Array<{ page_id: string; filename: string; reason: string; decided_by: string; questions_excluded: number }>
}

export async function listExams(submissionId: string): Promise<Exam[]> {
  const { data } = await api.get<Exam[]>(`/api/submissions/${submissionId}/exams`)
  return data
}

export async function createExam(submissionId: string, name: string): Promise<Exam> {
  const { data } = await api.post<Exam>(`/api/submissions/${submissionId}/exams`, { name })
  return data
}

export async function getExam(examId: string): Promise<Exam> {
  const { data } = await api.get<Exam>(`/api/exams/${examId}`)
  return data
}

export async function confirmExam(examId: string): Promise<Exam> {
  const { data } = await api.post<Exam>(`/api/exams/${examId}/confirm`, {})
  return data
}

export async function reopenExam(examId: string): Promise<{ id: string; status: string; version: number }> {
  const { data } = await api.post(`/api/exams/${examId}/reopen`)
  return data
}

// --- Assets ---

export interface Asset { id: string; exam_id: string; filename: string; media_type: string; material_type?: string }

export async function listExamAssets(examId: string): Promise<{ assets: Asset[] }> {
  const { data } = await api.get(`/api/exams/${examId}/assets`)
  return data
}

export async function linkOriginalSource(examId: string, questionId: string, assetId: string) {
  const { data } = await api.post(`/api/exams/${examId}/questions/${questionId}/link-original`, { asset_id: assetId })
  return data
}

// --- Upload + parse pipeline ---

export interface UploadSession { id: string; exam_id: string; status: string; asset_ids: string[]; job_id?: string }

export async function createUploadSession(examId: string): Promise<UploadSession> {
  const { data } = await api.post<UploadSession>(`/api/exams/${examId}/upload-sessions`)
  return data
}

export async function uploadSessionAsset(sessionId: string, file: File) {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post(`/api/upload-sessions/${sessionId}/assets`, form)
  return data
}

export async function finishUploadSession(sessionId: string): Promise<UploadSession> {
  const { data } = await api.post<UploadSession>(`/api/upload-sessions/${sessionId}/finish`)
  return data
}

export interface JobStage { name: string; status: string; error: string | null }
export interface Job { id: string; exam_id: string; status: string; stages: JobStage[]; failed_stage?: string; error?: string }

export async function runJob(jobId: string): Promise<Job> {
  const { data } = await api.post<Job>(`/api/jobs/${jobId}/run`)
  return data
}

export async function getJob(jobId: string): Promise<Job> {
  const { data } = await api.get<Job>(`/api/jobs/${jobId}`)
  return data
}

export interface TaskRecord {
  id: string
  kind: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  error: string | null
  result: unknown
  job_id?: string
  run_id?: string
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export async function enqueueJob(jobId: string): Promise<{ job: Job; task_id: string }> {
  const { data } = await api.post(`/api/jobs/${jobId}/enqueue`)
  return data
}

export async function getTask(taskId: string): Promise<TaskRecord> {
  const { data } = await api.get<TaskRecord>(`/api/tasks/${taskId}`)
  return data
}

/** Enqueue the parse and poll the job until it settles, reporting per-stage progress on the way.
 * Rejects with the job's own error (and `.job` attached) when a stage fails, so the retry UI can
 * keep working exactly as it did with the synchronous /run. */
export async function runJobInBackground(jobId: string, onProgress?: (job: Job) => void): Promise<Job> {
  await enqueueJob(jobId)
  for (;;) {
    const job = await getJob(jobId)
    onProgress?.(job)
    if (job.status === 'completed') return job
    if (job.status === 'failed') {
      const err = new Error(job.error || '解析失败') as Error & { job: Job }
      err.job = job
      throw err
    }
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
}

export async function retryJob(jobId: string): Promise<Job> {
  const { data } = await api.post<Job>(`/api/jobs/${jobId}/retry`)
  return data
}

// --- Parsed pages ---

export interface ExamPage {
  id: string
  asset_id: string
  page_order: number
  material_type: string
  subject: string
  ocr_text: string
  ocr_confidence: number
  ocr_status: string
  subject_mismatch: boolean
  include_in_analysis: boolean
  exclusion_reason: string
  inclusion_decided_by: string
  review_required: boolean
}

export async function listPages(examId: string): Promise<{ pages: ExamPage[] }> {
  const { data } = await api.get(`/api/exams/${examId}/pages`)
  return data
}

export async function updatePage(pageId: string, changes: Partial<Pick<ExamPage, 'material_type' | 'include_in_analysis' | 'page_order'>>) {
  const { data } = await api.patch(`/api/pages/${pageId}`, changes)
  return data
}

// --- Draft questions ---

export async function getParseDraft(examId: string): Promise<{ questions: DraftQuestion[]; status: string; version: number }> {
  const { data } = await api.get(`/api/exams/${examId}/parse-draft`)
  return data
}

export async function updateParseQuestion(examId: string, index: number, changes: Partial<Pick<DraftQuestion, 'question_text' | 'is_wrong_question' | 'student_answer' | 'original_question_available'>>) {
  const { data } = await api.patch(`/api/exams/${examId}/parse-draft/questions/${index}`, changes)
  return data
}

export interface QuestionInput {
  question_number: string
  question_text: string
  is_wrong_question: boolean
  original_question_available: boolean
  student_answer?: string | null
  original_source?: DraftQuestion['original_source']
}

// Replaces the *entire* draft_questions list — that's the backend contract for this route, not
// a per-item patch — so callers must resend every existing question alongside whatever changed.
// Used for the one thing single-question PATCH can't do: adding a row the parser never produced.
export async function replaceParseDraft(examId: string, questions: QuestionInput[]) {
  const { data } = await api.patch(`/api/exams/${examId}/parse-draft`, { questions })
  return data
}

// --- Question records (scoring) ---

export interface QuestionRecord {
  id: string
  exam_id: string
  question_number: string
  question_text: string
  is_wrong_question: boolean
  student_answer: { text?: string }
  score: { obtained: number | null; total: number | null; lost_points: number | null }
  correction: { teacher_comment: string }
}

export async function listQuestionRecords(examId: string): Promise<{ records: QuestionRecord[] }> {
  const { data } = await api.get(`/api/exams/${examId}/question-records`)
  return data
}

export async function updateQuestionRecord(recordId: string, changes: { score_obtained?: number; score_total?: number; teacher_comment?: string }) {
  const { data } = await api.patch(`/api/question-records/${recordId}`, changes)
  return data
}

// --- Analysis runs ---

export interface AnalysisRunRef {
  id: string
  status: string
  submission_id: string
  exam_ids: string[]
  exam_versions?: ExamVersionRef[]
}

export async function createAnalysisRun(submissionId: string, examIds: string[]): Promise<AnalysisRunRef> {
  const { data } = await api.post(`/api/submissions/${submissionId}/analysis-runs`, { exam_ids: examIds })
  return data
}

export async function confirmStartRun(runId: string): Promise<{ status: string }> {
  const { data } = await api.post(`/api/analysis-runs/${runId}/confirm-start`)
  return data
}

export async function listAnalysisRuns(submissionId: string): Promise<{ runs: AnalysisRunRef[] }> {
  const { data } = await api.get(`/api/submissions/${submissionId}/analysis-runs`)
  return data
}

// --- Verification questions + mastery (the learning-loop close: answer, self-score, track mastery) ---

export interface VerificationAttempt {
  id: string
  answer: string
  score: number
  max_score: number
  duration_seconds: number
  passed: boolean
}

export interface VerificationQuestionEntity {
  id: string
  analysis_run_id: string
  submission_id: string
  knowledge_id: string
  question_text: string
  answer: string | null
  target_error: string
  source_question_ids: string[]
  review_status: 'approved' | 'needs_review' | string
  review_findings: string[]
  answer_withheld: boolean
  withheld_reason?: string[]
  attempts: VerificationAttempt[]
}

export async function listVerificationQuestions(runId: string): Promise<{ questions: VerificationQuestionEntity[] }> {
  const { data } = await api.get(`/api/analysis-runs/${runId}/verification-questions`)
  return data
}

export async function submitVerificationAttempt(
  questionId: string,
  payload: { answer: string; score: number; max_score: number; duration_seconds?: number },
): Promise<VerificationAttempt> {
  const { data } = await api.post(`/api/verification-questions/${questionId}/attempts`, payload)
  return data
}

export async function reviewVerificationQuestion(
  questionId: string,
  payload: { approved: boolean; answer?: string; note?: string },
): Promise<VerificationQuestionEntity> {
  const { data } = await api.post(`/api/verification-questions/${questionId}/review`, payload)
  return data
}

export interface MasteryRecord {
  id: string
  submission_id: string
  knowledge_id: string
  error_cluster: string
  status: 'not_started' | 'in_progress' | 'provisionally_resolved' | 'resolved' | string
  attempt_history: Array<{ verification_question_id: string; passed: boolean }>
}

export async function listMasteryRecords(submissionId: string): Promise<{ records: MasteryRecord[] }> {
  const { data } = await api.get(`/api/submissions/${submissionId}/mastery-records`)
  return data
}

// --- Personal data export ---
// Fetched via axios (so the X-User-Id header rides along, same as every other call) and then
// handed to the browser as a download — a plain <a href> to this endpoint would just navigate to
// raw JSON in the tab instead of saving a file, and would skip the auth header the API expects.
export async function downloadSubmissionExport(submissionId: string, filenameHint: string) {
  const { data } = await api.get(`/api/submissions/${submissionId}/export`)
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `exam-diagnosis-${filenameHint}-${submissionId.slice(0, 8)}.json`
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
