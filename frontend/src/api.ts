import axios from 'axios'

/** Where the API lives. Three sources, in order: the desktop shell (it spawns the backend on a
 * free port and tells us via a Tauri command), VITE_API_BASE at build time (the verify setup), or
 * the web deployment's fixed 127.0.0.1:8765. Mutable because the first source is only known at
 * runtime — call resolveApiBase() once before rendering. */
export let API_BASE: string = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8765'

export function apiUrl(path: string) {
  return `${API_BASE}${path}`
}

export function isDesktopShell(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

export async function resolveApiBase(): Promise<string> {
  if (isDesktopShell()) {
    // Loaded on demand so the web bundle never ships (or evaluates) the Tauri bridge.
    const { invoke } = await import('@tauri-apps/api/core')
    const base = await invoke<string>('api_base')
    if (base) {
      API_BASE = base
      api.defaults.baseURL = base
    }
  }
  return API_BASE
}

/** Poll /api/health until the backend answers — the desktop shell has already waited for the
 * process to bind, but the web deployment may be started before its backend. */
export async function waitForBackend(timeoutMs = 20000, onTick?: (attempt: number, lastError: string) => void): Promise<boolean> {
  const deadline = Date.now() + timeoutMs
  for (let attempt = 1; Date.now() < deadline; attempt++) {
    try {
      await api.get('/api/health', { timeout: 2000 })
      return true
    } catch (e: any) {
      // Surface the real failure: a CORS refusal, a connection error and a timeout all need
      // different fixes, and "no response" hides which one it was.
      const reason = e?.response ? `HTTP ${e.response.status}` : e?.code === 'ERR_NETWORK' ? '网络层拒绝（很可能是 CORS 或端口不通）' : e?.message ?? String(e)
      onTick?.(attempt, reason)
      await new Promise((resolve) => setTimeout(resolve, 800))
    }
  }
  return false
}

export const api = axios.create({
  baseURL: API_BASE,
  // Identity when nobody is logged in and the backend runs with auth optional (the default local
  // single-user setup). Ignored by the backend whenever a bearer token is sent or auth is required.
  headers: { 'X-User-Id': 'local' },
})

// --- Session token ---

export const TOKEN_KEY = 'exam_diagnosis_token'
/** Fired when a request came back 401: the session is gone, the router should show the login page. */
export const AUTH_LOST_EVENT = 'exam-diagnosis:auth-lost'

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
      // An in-app event, not window.location.assign: the desktop shell serves the SPA from its
      // own protocol and a full reload to /login is not something we can rely on there.
      window.dispatchEvent(new CustomEvent(AUTH_LOST_EVENT, { detail: { next: window.location.pathname + window.location.search } }))
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

export interface HealthResponse { status: string; application: string; version: string; data_dir: string; auth_required: boolean }

export async function fetchHealth(): Promise<HealthResponse> {
  const { data } = await api.get<HealthResponse>('/api/health')
  return data
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

/** How a question got tied to a knowledge point — the backend distinguishes a literal keyword
 * hit from the `match` model's semantic judgement, and the public registry from the user's own
 * knowledge base. Showing it lets the reader tell "the question literally says 定义域" from "a
 * model judged this to be about 定义域" — very different levels of evidence. */
export interface MatchedKnowledgePoint {
  knowledge_id: string
  name: string
  chapter: string
  confidence: number
  matched_keywords: string[]
  match_method: 'keyword' | 'model' | 'keyword+model' | string
  model_confidence?: number
  source: 'public_registry' | 'user_knowledge_base' | string
  knowledge_base_source?: 'public' | 'user' | string
  knowledge_base_version?: string
}

export interface WrongQuestion {
  question_id: string
  question_text: string
  student_answer: string
  original_source: unknown
  knowledge_points: MatchedKnowledgePoint[]
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

// --- System status / observability ---

export interface ModelRoleStatus {
  role: string
  purpose: string
  configured: boolean
  base_url: string | null
  model: string | null
  timeout: number
  env_prefix: string
}

export interface SystemStatus {
  application: { name: string; version: string }
  auth: { required: boolean; session_ttl_hours: number }
  storage: { database: string; uploads: string }
  model_roles: ModelRoleStatus[]
  budget: { daily_token_budget: number | null; max_calls_per_minute: number | null; block_reason: string | null }
  tasks: { workers: number; active: number; failed: number }
  knowledge: { partitions: KnowledgePartition[] }
}

export async function fetchSystemStatus(): Promise<SystemStatus> {
  const { data } = await api.get<SystemStatus>('/api/system/status')
  return data
}

export interface ModelCall {
  id: string
  role: string
  model: string | null
  base_url: string | null
  created_at: string
  latency_ms: number
  attempts: number
  success: boolean
  error: string
  blocked?: boolean
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  exam_id?: string
  run_id?: string
  record_id?: string
  asset_id?: string
}

export interface ModelCallsResponse {
  calls: ModelCall[]
  summary: { total: number; succeeded: number; failed: number; avg_latency_ms: number; total_tokens: number }
  budget: { daily_token_budget: number | null; tokens_used_today: number; daily_tokens_remaining: number | null; max_calls_per_minute: number | null; calls_in_last_minute: number }
}

export async function listModelCalls(limit = 50): Promise<ModelCallsResponse> {
  const { data } = await api.get<ModelCallsResponse>('/api/model-calls', { params: { limit } })
  return data
}

export async function listTasks(limit = 50): Promise<{ tasks: TaskRecord[]; workers: number }> {
  const { data } = await api.get('/api/tasks', { params: { limit } })
  return data
}

// --- Trends (longitudinal view across a submission's exams) ---

export type TrendDirection = 'improving' | 'worsening' | 'stable' | 'insufficient'

export interface TrendExamPoint {
  exam_id: string
  name: string
  sequence_no: number | null
  exam_date: string | null
  exam_type: string | null
  version: number
  question_count: number
  wrong_count: number
  error_rate: number
  root_cause_counts: Record<string, number>
  analysed: boolean
}

export interface TrendSeriesPoint {
  exam_id: string
  name: string
  question_count: number
  wrong_count: number
  error_rate: number
}

export interface KnowledgeTrend {
  knowledge_id: string
  name: string
  chapter: string
  points: TrendSeriesPoint[]
  exam_count: number
  total_question_count: number
  total_wrong_count: number
  first_error_rate: number
  last_error_rate: number
  delta: number | null
  direction: TrendDirection
  persistent: boolean
}

export interface SubmissionTrends {
  submission_id: string
  exams: TrendExamPoint[]
  knowledge_trends: KnowledgeTrend[]
  summary: {
    exam_count: number
    exams_with_questions: number
    analysed_exam_count: number
    total_question_count: number
    total_wrong_count: number
    first_error_rate: number | null
    last_error_rate: number | null
    overall_delta: number | null
    overall_direction: TrendDirection
    improving: string[]
    worsening: string[]
    persistent: string[]
    root_cause_counts: Record<string, number>
  }
  note: string
}

export async function fetchTrends(submissionId: string): Promise<SubmissionTrends> {
  const { data } = await api.get<SubmissionTrends>(`/api/submissions/${submissionId}/trends`)
  return data
}

// --- Backup: export a submission as a portable bundle, import it back ---

export interface ImportResult {
  submission_id: string
  imported: Record<string, number>
  restored_files: number
  missing_files: string[]
  source_versions: Record<string, unknown>
}

/** The zip carries the uploaded files as well as the metadata — this is the one that actually
 * moves a student's work to another machine. The JSON export stays available for inspection. */
export async function downloadSubmissionBackup(submissionId: string) {
  return downloadFromApi(`/api/submissions/${submissionId}/export?format=zip`, `exam-diagnosis-backup-${submissionId.slice(0, 8)}.zip`)
}

export async function importSubmission(file: File): Promise<ImportResult> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post<ImportResult>('/api/submissions/import', form)
  return data
}

// --- Wrong-question book ---

export interface WrongBookItem {
  question_id: string
  exam_id: string
  exam_name: string
  exam_date: string | null
  question_number: string
  question_text: string
  student_answer: string
  teacher_comment: string
  score: { obtained: number | null; total: number | null }
  original_source: unknown
  knowledge_points: Array<{ knowledge_id: string; name: string; chapter: string }>
  root_cause: string
  explanation: string
  avoidance_advice: string
  analysis_run_id: string | null
  analysed: boolean
}

export interface WrongBookFilters { knowledge_id: string | null; exam_id: string | null; root_cause: string | null }

export interface WrongBook {
  submission_id: string
  items: WrongBookItem[]
  filters: WrongBookFilters
  facets: {
    exams: Array<{ exam_id: string; name: string; count: number }>
    knowledge_points: Array<{ knowledge_id: string; name: string; chapter: string; count: number }>
    root_causes: Array<{ root_cause: string; count: number }>
  }
  summary: { total: number; analysed: number; exam_count: number }
}

export async function fetchWrongBook(submissionId: string, filters: Partial<WrongBookFilters> = {}): Promise<WrongBook> {
  const params = Object.fromEntries(Object.entries(filters).filter(([, v]) => v))
  const { data } = await api.get<WrongBook>(`/api/submissions/${submissionId}/wrong-questions`, { params })
  return data
}

/** Download a printable edition. `practice` prints stems and blank space only — see the backend
 * note: seeing last time's wrong answer while redoing a question rehearses the mistake. */
export async function downloadWrongBook(
  submissionId: string,
  options: { format: 'pdf' | 'docx'; edition: 'review' | 'practice' } & Partial<WrongBookFilters>,
) {
  const { format, edition, ...filters } = options
  const query = new URLSearchParams({ format, edition })
  for (const [key, value] of Object.entries(filters)) if (value) query.set(key, String(value))
  return downloadFromApi(
    `/api/submissions/${submissionId}/wrong-questions/export?${query}`,
    `错题本-${edition === 'practice' ? '练习版' : '复习版'}-${submissionId.slice(0, 8)}.${format}`,
  )
}

// --- Review plan (spaced repetition over mastery records) ---

export interface ReviewItem {
  mastery_id: string
  submission_id: string
  knowledge_id: string
  error_cluster: string
  status: string
  review_stage: number
  interval_days: number | null
  due_at: string | null
  last_reviewed_at: string | null
  attempt_count: number
  days_until_due: number | null
  overdue_days: number
  verification_question_id: string | null
  analysis_run_id: string | null
  question_text: string | null
}

export interface ReviewPlan {
  submission_id: string | null
  due: ReviewItem[]
  upcoming: ReviewItem[]
  completed: ReviewItem[]
  summary: {
    due_count: number
    overdue_count: number
    upcoming_count: number
    completed_count: number
    next_due_at: string | null
  }
  intervals_days: number[]
  note: string
}

export async function fetchReviewPlan(submissionId?: string): Promise<ReviewPlan> {
  const { data } = await api.get<ReviewPlan>('/api/review-plan', { params: submissionId ? { submission_id: submissionId } : undefined })
  return data
}

export async function getVerificationQuestion(questionId: string): Promise<VerificationQuestionEntity> {
  const { data } = await api.get<VerificationQuestionEntity>(`/api/verification-questions/${questionId}`)
  return data
}

// --- Runtime settings (model keys / budget, stored in <data_dir>/config.json) ---

export type SettingSource = 'env' | 'config' | null

export interface RoleSettings {
  role: string
  purpose: string
  api_key_set: boolean
  api_key_source: SettingSource
  base_url: string | null
  base_url_default: string | null
  base_url_source: SettingSource
  model: string | null
  model_default: string | null
  model_source: SettingSource
  timeout: number | null
  timeout_default: number
  timeout_source: SettingSource
  configured: boolean
  /** The key comes from the environment: config.json can't override it, so the UI must not offer to. */
  locked: boolean
}

export interface SettingsResponse {
  roles: RoleSettings[]
  budget: {
    daily_token_budget: number | null
    daily_token_budget_source: SettingSource
    max_calls_per_minute: number | null
    max_calls_per_minute_source: SettingSource
  }
  config_path: string
  auth_required: boolean
  writable: boolean
}

export interface SettingsUpdate {
  roles?: Record<string, { api_key?: string; base_url?: string | null; model?: string | null; timeout?: number | string | null }>
  budget?: { daily_token_budget?: number | null; max_calls_per_minute?: number | null }
}

export async function fetchSettings(): Promise<SettingsResponse> {
  const { data } = await api.get<SettingsResponse>('/api/settings')
  return data
}

export async function updateSettings(update: SettingsUpdate): Promise<SettingsResponse> {
  const { data } = await api.put<SettingsResponse>('/api/settings', update)
  return data
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
  resource: string | null
  source: 'public' | 'user'
  // user partitions only
  id?: string
  name?: string | null
  extends_public?: boolean
  base_version?: string | null
  user_point_count?: number
}

export async function listKnowledgePartitions(): Promise<KnowledgePartition[]> {
  const { data } = await api.get<{ partitions: KnowledgePartition[] }>('/api/knowledge-partitions')
  return data.partitions
}

// --- Knowledge bases (user-maintained partitions layered over the public registry) ---

export interface KnowledgePoint {
  knowledge_id: string
  name: string
  chapter: string
  keywords: string[]
  confidence: number
  parent: string | null
  dependencies: string[]
  grade_scope: string
  textbook_chapter: string
  origin?: 'public' | 'user'
}

export interface KnowledgeScope { stage: string; subject: string; province: string; curriculum_year: number }

export interface KnowledgeBase extends KnowledgeScope {
  id: string
  user_id: string
  academic_year_range: string[]
  extends_public: boolean
  name: string
  version: string
  curriculum: string | null
  knowledge_points: KnowledgePoint[]
  created_at: string
  updated_at: string
  resolved: KnowledgePartition | null
  problem: string | null
}

export interface KnowledgeBaseInput extends KnowledgeScope {
  academic_year_range?: string[]
  extends_public: boolean
  name?: string
  version?: string
  curriculum?: string | null
  knowledge_points?: Omit<KnowledgePoint, 'origin'>[]
}

export async function listKnowledgeBases(): Promise<KnowledgeBase[]> {
  const { data } = await api.get<{ knowledge_bases: KnowledgeBase[] }>('/api/knowledge-bases')
  return data.knowledge_bases
}

export async function createKnowledgeBase(input: KnowledgeBaseInput): Promise<KnowledgeBase> {
  const { data } = await api.post<KnowledgeBase>('/api/knowledge-bases', input)
  return data
}

export async function deleteKnowledgeBase(id: string): Promise<void> {
  await api.delete(`/api/knowledge-bases/${id}`)
}

export async function upsertKnowledgePoint(id: string, point: Omit<KnowledgePoint, 'origin'>): Promise<KnowledgeBase> {
  const { data } = await api.post<KnowledgeBase>(`/api/knowledge-bases/${id}/points`, point)
  return data
}

export async function deleteKnowledgePoint(id: string, knowledgeId: string): Promise<KnowledgeBase> {
  const { data } = await api.delete<KnowledgeBase>(`/api/knowledge-bases/${id}/points/${encodeURIComponent(knowledgeId)}`)
  return data
}

export async function importKnowledgeBase(file: File): Promise<KnowledgeBase> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post<KnowledgeBase>('/api/knowledge-bases/import', form)
  return data
}

export function downloadKnowledgeBase(id: string) {
  return downloadFromApi(`/api/knowledge-bases/${id}/export`, `knowledge-base-${id.slice(0, 8)}.yaml`)
}

/** Effective points for a scope as the current user sees them (public base + their own). */
export async function listPartitionPoints(scope: KnowledgeScope): Promise<{ partition: KnowledgePartition; points: KnowledgePoint[] }> {
  const { data } = await api.get('/api/knowledge-partitions/points', { params: scope })
  return data
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

export interface Asset {
  id: string
  exam_id: string
  filename: string
  media_type: string
  material_type?: string
  page_order?: number
  ocr_status?: string
  ocr_warning?: string
  scanned_pdf?: boolean
  upload_session_id?: string
}

export async function listExamAssets(examId: string): Promise<{ assets: Asset[] }> {
  const { data } = await api.get(`/api/exams/${examId}/assets`)
  return data
}

export async function updateAsset(assetId: string, changes: { material_type?: string; page_order?: number }): Promise<Asset> {
  const { data } = await api.patch<Asset>(`/api/assets/${assetId}`, changes)
  return data
}

export async function deleteAsset(assetId: string): Promise<void> {
  await api.delete(`/api/assets/${assetId}`)
}

/** Queue a fresh parse of the exam's current assets (after deleting/re-typing one). Returns the
 * job to enqueue; a confirmed exam must be reopened first (409 exam_confirmed). */
export async function reparseExam(examId: string): Promise<{ job_id: string; job: Job; asset_count: number }> {
  const { data } = await api.post(`/api/exams/${examId}/reparse`)
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

export async function uploadSessionAsset(sessionId: string, file: File, onProgress?: (percent: number) => void) {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post(`/api/upload-sessions/${sessionId}/assets`, form, {
    onUploadProgress: (event) => {
      if (onProgress && event.total) onProgress(Math.round((event.loaded / event.total) * 100))
    },
  })
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
  /** The parser's own reason a page needs a human (scanned PDF without vision, unreadable pages…). */
  warning?: string
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
/** Hand a file to the user. The desktop shell has no browser download manager and navigating
 * the webview to an attachment URL would replace the app with the raw response — so there it
 * goes through a native "另存为" dialog and a Rust command that writes the bytes; in a browser
 * it's the usual blob + <a download>. Returns false when the user cancelled the dialog. */
export async function saveBlob(blob: Blob, filename: string): Promise<boolean> {
  if (isDesktopShell()) {
    const { save } = await import('@tauri-apps/plugin-dialog')
    const { invoke } = await import('@tauri-apps/api/core')
    const extension = filename.includes('.') ? filename.slice(filename.lastIndexOf('.') + 1) : ''
    const path = await save({ defaultPath: filename, filters: extension ? [{ name: extension.toUpperCase(), extensions: [extension] }] : undefined })
    if (!path) return false
    const bytes = new Uint8Array(await blob.arrayBuffer())
    let binary = ''
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
    await invoke('save_file', { path, dataBase64: btoa(binary) })
    return true
  }
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
  return true
}

/** Download an API resource (export endpoints) with the session headers attached — a plain
 * <a href> would neither carry the bearer token nor work inside the desktop shell. */
export async function downloadFromApi(path: string, filename: string): Promise<boolean> {
  const response = await api.get(path, { responseType: 'blob' })
  return saveBlob(response.data as Blob, filename)
}

export async function downloadSubmissionExport(submissionId: string, filenameHint: string) {
  const { data } = await api.get(`/api/submissions/${submissionId}/export`)
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  return saveBlob(blob, `exam-diagnosis-${filenameHint}-${submissionId.slice(0, 8)}.json`)
}
