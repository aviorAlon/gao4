import axios from 'axios'

export const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8765'

export const api = axios.create({
  baseURL: API_BASE,
  headers: { 'X-User-Id': 'local' },
})

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

export async function fetchReport(runId: string): Promise<AnalysisReport> {
  const { data } = await api.get<AnalysisReport>(`/api/analysis-runs/${runId}/report`)
  return data
}
