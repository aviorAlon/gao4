import { useEffect, useState } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import {
  Alert, Breadcrumb, Button, Card, Checkbox, Divider, Empty, Flex, Input, InputNumber, Popconfirm, Select, Spin,
  Steps, Table, Tag, Typography, Upload, message,
} from 'antd'
import type { UploadFile } from 'antd'
import { InboxOutlined, CheckCircleOutlined, UndoOutlined, LinkOutlined, PlusOutlined, DeleteOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  confirmExam, createAnalysisRun, createUploadSession, confirmStartRun, deleteAsset, finishUploadSession,
  getExam, getParseDraft, linkOriginalSource, listExamAssets, listPages, listQuestionRecords, reparseExam,
  reopenExam, replaceParseDraft, retryJob, runJobInBackground, updateAsset, updatePage, updateParseQuestion, updateQuestionRecord,
  uploadSessionAsset,
  type Asset, type DraftQuestion, type Exam, type ExamPage as ExamPageData, type Job, type QuestionInput, type QuestionRecord,
} from '../api'
import { Masthead, RedPenMark, SectionBlock } from '../chart/Chart'
import MobileUploadPanel from '../components/MobileUploadPanel'
import '../chart/chart.css'

const { Text } = Typography
const { Dragger } = Upload

const MATERIAL_TYPE_OPTIONS = [
  { value: 'paper', label: '试卷' },
  { value: 'answer_key', label: '答案' },
  { value: 'student_answer', label: '学生作答' },
  { value: 'correction', label: '批改' },
  { value: 'unknown', label: '未知' },
]

// Framed as the exam's own path through the chart: gather the specimen, run the test, confirm
// the read, write the prescription — the same four words a real diagnostic workflow would use.
const STEP_FOR_STATUS: Record<string, number> = {
  uploading: 0, parsing: 1, waiting_confirmation: 2, confirmed: 3,
}
const STEPS = [
  { title: '资料采集' }, { title: '化验解析' }, { title: '诊断确认' }, { title: '处方与分析' },
]

// Parse-job stages as the backend names them, in the words the progress line shows.
const STAGE_LABELS: Record<string, string> = {
  classify: '识别资料类型', ocr: '读取页面内容', question_split: '切分题目',
}

function UploadStep({ examId, onDone }: { examId: string; onDone: () => void }) {
  const [fileList, setFileList] = useState<UploadFile[]>([])
  const [busy, setBusy] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState<string | null>(null)
  const [phoneUploads, setPhoneUploads] = useState(0)

  function describeProgress(job: Job) {
    const running = job.stages.find((s) => s.status === 'running')
    const done = job.stages.filter((s) => s.status === 'completed').length
    const label = running ? STAGE_LABELS[running.name] ?? running.name : '排队中'
    return `${label}（${done}/${job.stages.length} 步完成）`
  }

  async function upload() {
    setBusy(true)
    setError(null)
    setProgress('上传中…')
    try {
      const session = await createUploadSession(examId)
      const files = fileList.filter((f) => f.originFileObj)
      for (const [index, f] of files.entries()) {
        await uploadSessionAsset(session.id, f.originFileObj!, (percent) =>
          setProgress(`上传中 ${index + 1}/${files.length}：${f.name} ${percent}%`))
      }
      const finished = await finishUploadSession(session.id)
      if (!finished.job_id) throw new Error('未返回解析任务')
      setJobId(finished.job_id)
      await runJobInBackground(finished.job_id, (job) => setProgress(describeProgress(job)))
      message.success('解析完成')
      onDone()
    } catch (e: any) {
      setError(e?.response?.data?.error ?? e.message ?? '上传或解析失败')
    } finally {
      setBusy(false)
      setProgress(null)
    }
  }

  async function parsePhoneUploads() {
    setBusy(true)
    setError(null)
    try {
      const { job_id } = await reparseExam(examId)
      await runJobInBackground(job_id, (job) => setProgress(describeProgress(job)))
      message.success('解析完成')
      onDone()
    } catch (e: any) {
      setError(e?.response?.data?.error ?? e.message ?? '解析失败')
    } finally {
      setBusy(false)
      setProgress(null)
    }
  }

  async function retry() {
    if (!jobId) return
    setBusy(true)
    setError(null)
    try {
      await retryJob(jobId)
      await runJobInBackground(jobId, (job) => setProgress(describeProgress(job)))
      message.success('解析完成')
      onDone()
    } catch (e: any) {
      setError(e?.response?.data?.error ?? e.message ?? '重试仍然失败')
    } finally {
      setBusy(false)
      setProgress(null)
    }
  }

  return (
    <SectionBlock marker={error ? 'alert' : 'neutral'} title="上传考试资料">
      <Card>
        <Flex vertical gap="middle">
          <Dragger
            multiple fileList={fileList} beforeUpload={() => false}
            onChange={({ fileList: fl }) => setFileList(fl)}
            accept="image/jpeg,image/png,image/webp,application/pdf,text/plain"
            disabled={busy}
          >
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            <p>点击或拖拽图片 / PDF / 文本文件到此处</p>
          </Dragger>

          {error && (
            <Alert
              type="error" showIcon title="解析失败" description={error}
              action={jobId && <Button size="small" danger onClick={retry} loading={busy}>重试解析</Button>}
            />
          )}

          <Button type="primary" onClick={upload} loading={busy} disabled={!fileList.length}>
            上传全部并运行解析
          </Button>
          {progress && <Text type="secondary">{progress}</Text>}

          <Divider style={{ margin: '4px 0' }}>或者</Divider>
          {/* Deliberately not onDone: that reloads the exam and would unmount the panel, taking
              the QR with it after the very first photo. Count them and let the user say when. */}
          <MobileUploadPanel examId={examId} onUploaded={() => setPhoneUploads((n) => n + 1)} />
          {phoneUploads > 0 && (
            <Alert
              type="success" showIcon
              title={`手机已传来 ${phoneUploads} 份资料`}
              description={
                <Button size="small" type="primary" style={{ marginTop: 8 }} onClick={parsePhoneUploads} loading={busy}>
                  传完了，开始解析
                </Button>
              }
            />
          )}
        </Flex>
      </Card>
    </SectionBlock>
  )
}

/** Recovery path for an exam whose status is stuck at "parsing" from an earlier session that
 * never confirmed success or failure (tab closed, network drop) — there is no "find the job for
 * this exam" endpoint to resume the original job from a fresh page load, so the honest way out is
 * to let the same assets go through a brand new parse job. */
function StuckParsingRecovery({ examId, onDone }: { examId: string; onDone: () => void }) {
  const [showUpload, setShowUpload] = useState(false)
  if (showUpload) return <UploadStep examId={examId} onDone={onDone} />
  return (
    <SectionBlock marker="caution" title="解析中">
      <Card>
        <Flex vertical gap="middle" align="flex-start">
          <Spin />
          <Text type="secondary">
            如果这个状态卡住很久没有变化（比如上次上传时中途断网、关闭了页面），可以重新上传资料再解析一次。
          </Text>
          <Button onClick={() => setShowUpload(true)}>重新上传资料</Button>
        </Flex>
      </Card>
    </SectionBlock>
  )
}

function LinkOriginalControl({ examId, questionId, assets, onLinked }: {
  examId: string; questionId: string; assets: Asset[]; onLinked: () => void
}) {
  const [assetId, setAssetId] = useState<string | undefined>(undefined)
  const [busy, setBusy] = useState(false)

  async function onLink() {
    if (!assetId) return
    setBusy(true)
    try {
      await linkOriginalSource(examId, questionId, assetId)
      message.success('已关联原题')
      onLinked()
    } catch (e: any) {
      message.error(e?.response?.data?.error ?? '关联失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Alert
      type="warning" showIcon title="这道错题还没有关联原题来源"
      description={
        <Flex gap="small" style={{ marginTop: 8 }} wrap>
          <Select
            size="small" style={{ minWidth: 200 }} placeholder="选择一份已上传的资料作为原题来源"
            value={assetId} onChange={setAssetId}
            options={assets.map((a) => ({ value: a.id, label: a.filename }))}
          />
          <Button size="small" icon={<LinkOutlined />} onClick={onLink} loading={busy} disabled={!assetId}>关联</Button>
        </Flex>
      }
    />
  )
}

function PagesAndDraft({ exam, onConfirmed }: { exam: Exam; onConfirmed: () => void }) {
  const [pages, setPages] = useState<ExamPageData[]>([])
  const [assets, setAssets] = useState<Asset[]>([])
  const [questions, setQuestions] = useState(exam.draft_questions)
  const [loading, setLoading] = useState(true)
  const [confirming, setConfirming] = useState(false)
  const [reparsing, setReparsing] = useState(false)
  const [reparseProgress, setReparseProgress] = useState<string | null>(null)

  async function reloadQuestions() {
    const draft = await getParseDraft(exam.id)
    setQuestions(draft.questions)
  }

  useEffect(() => {
    Promise.all([listPages(exam.id), listExamAssets(exam.id)])
      .then(([p, a]) => { setPages(p.pages); setAssets(a.assets) })
      .finally(() => setLoading(false))
    setQuestions(exam.draft_questions)
  }, [exam.id])

  async function onPageChange(pageId: string, changes: Parameters<typeof updatePage>[1]) {
    await updatePage(pageId, changes)
    const r = await listPages(exam.id)
    setPages(r.pages)
  }

  async function onQuestionChange(index: number, changes: Parameters<typeof updateParseQuestion>[2]) {
    setQuestions((qs) => qs.map((q, i) => (i === index ? { ...q, ...changes } : q)))
    await updateParseQuestion(exam.id, index, changes)
  }

  async function onAddQuestion() {
    const toInput = (q: DraftQuestion): QuestionInput => ({
      question_number: q.question_number, question_text: q.question_text,
      is_wrong_question: q.is_wrong_question, original_question_available: q.original_question_available,
      student_answer: q.student_answer, original_source: q.original_source,
    })
    const blank: QuestionInput = {
      question_number: String(questions.length + 1), question_text: '',
      is_wrong_question: true, original_question_available: true, student_answer: '', original_source: null,
    }
    try {
      const result = await replaceParseDraft(exam.id, [...questions.map(toInput), blank])
      setQuestions(result.questions)
      message.success('已新增一道题目草稿，请补上题干')
    } catch (e: any) {
      message.error(e?.response?.data?.error ?? '新增失败')
    }
  }

  async function onConfirm() {
    setConfirming(true)
    try {
      await confirmExam(exam.id)
      message.success('考试已确认')
      onConfirmed()
    } catch (e: any) {
      const code = e?.response?.data?.error
      message.error(
        code === 'questions_required' ? '还没有任何题目，先补录题目再确认'
        : code === 'wrong_question_original_required' ? '标记为「错题」的题目需要有可用的原题，才能确认考试'
        : code === 'original_source_required' ? '有错题还没有关联原题来源，请在题目草稿里补上'
        : code ?? '确认失败',
      )
    } finally {
      setConfirming(false)
    }
  }

  async function reloadMaterials() {
    const [p, a] = await Promise.all([listPages(exam.id), listExamAssets(exam.id)])
    setPages(p.pages)
    setAssets(a.assets)
  }

  async function onAssetTypeChange(assetId: string, materialType: string) {
    await updateAsset(assetId, { material_type: materialType })
    await reloadMaterials()
  }

  async function onDeleteAsset(assetId: string) {
    await deleteAsset(assetId)
    await reloadMaterials()
    await reloadQuestions()
    message.success('已删除；点"重新解析"让题目草稿跟着更新')
  }

  async function onReparse() {
    setReparsing(true)
    setReparseProgress('排队中…')
    try {
      const { job_id } = await reparseExam(exam.id)
      await runJobInBackground(job_id, (job) => {
        const running = job.stages.find((s) => s.status === 'running')
        setReparseProgress(running ? STAGE_LABELS[running.name] ?? running.name : '排队中…')
      })
      message.success('重新解析完成')
      onConfirmed()
    } catch (e: any) {
      message.error(e?.response?.data?.error === 'exam_has_no_assets' ? '没有资料可以解析' : e?.response?.data?.error ?? e.message ?? '重新解析失败')
    } finally {
      setReparsing(false)
      setReparseProgress(null)
    }
  }

  if (loading) return <Spin />

  const pageByAsset = new Map(pages.map((p) => [p.asset_id, p]))
  const materialRows = [...assets].sort((a, b) => (a.page_order ?? 0) - (b.page_order ?? 0))
  const mediaLabel = (a: Asset) => a.media_type === 'application/pdf' ? (a.scanned_pdf ? '扫描 PDF' : 'PDF') : a.media_type.startsWith('image/') ? '图片' : '文本'

  return (
    <>
      <SectionBlock marker={materialRows.some((a) => pageByAsset.get(a.id)?.review_required) ? 'caution' : 'neutral'} title={`资料（${materialRows.length} 份）`}>
        <Card>
          <Flex vertical gap="middle">
            <Table
              rowKey="id" size="small" pagination={false} dataSource={materialRows}
              columns={[
                { title: '页', dataIndex: 'page_order', width: 56, render: (v) => <span className="data-figure">{v}</span> },
                { title: '文件', render: (_, a: Asset) => (
                  <Flex vertical gap={2}>
                    <Flex align="center" gap={6}><span>{a.filename}</span><Tag>{mediaLabel(a)}</Tag></Flex>
                    {(pageByAsset.get(a.id)?.warning || a.ocr_warning) && (
                      <Text type="warning" style={{ fontSize: 12 }}>{pageByAsset.get(a.id)?.warning || a.ocr_warning}</Text>
                    )}
                  </Flex>
                ) },
                { title: '材料类型', width: 150, render: (_, a: Asset) => (
                  <Select
                    size="small" value={pageByAsset.get(a.id)?.material_type ?? a.material_type ?? 'unknown'} style={{ width: 130 }}
                    options={MATERIAL_TYPE_OPTIONS}
                    onChange={(v) => onAssetTypeChange(a.id, v)}
                  />
                ) },
                { title: '计入解析统计', width: 110, render: (_, a: Asset) => {
                  const p = pageByAsset.get(a.id)
                  return p ? <Checkbox checked={p.include_in_analysis} onChange={(e) => onPageChange(p.id, { include_in_analysis: e.target.checked })} /> : <Text type="secondary">—</Text>
                } },
                { title: '状态', width: 110, render: (_, a: Asset) => {
                  const p = pageByAsset.get(a.id)
                  if (!p) return <Tag>未解析</Tag>
                  return p.review_required ? <Tag color="gold">需人工确认</Tag> : <Tag color="green">已解析</Tag>
                } },
                { title: '', width: 48, render: (_, a: Asset) => (
                  <Popconfirm title="删除这份资料？其页面和来自它的题目草稿会一起移除。" onConfirm={() => onDeleteAsset(a.id)}>
                    <Button danger type="text" size="small" icon={<DeleteOutlined />} />
                  </Popconfirm>
                ) },
              ]}
            />
            <MobileUploadPanel examId={exam.id} onUploaded={reloadMaterials} />
            <Flex align="center" gap="middle" wrap>
              <Button icon={<ReloadOutlined />} onClick={onReparse} loading={reparsing} disabled={!materialRows.length}>重新解析全部资料</Button>
              {reparseProgress && <Text type="secondary">{reparseProgress}</Text>}
              <Text type="secondary">删除资料、改材料类型或配好视觉解析之后，重新解析会重建页面和题目草稿（人工补录的题目保留）。</Text>
            </Flex>
          </Flex>
        </Card>
      </SectionBlock>

      <SectionBlock marker={questions.some((q) => q.is_wrong_question) ? 'alert' : 'neutral'} title={`题目草稿（${questions.length} 道）`}>
        <Card>
          <Flex vertical gap="middle">
            {questions.length === 0 && <Empty description="没有识别出题目，可能需要调整解析规则或人工补录" />}
            {questions.length > 0 && (
              <Flex vertical gap="middle">
              {questions.map((q, i) => (
                <Card key={q.id} size="small" type="inner" title={
                  <Flex align="center" gap={8}>
                    {q.is_wrong_question && <RedPenMark />}
                    <span>第 {q.question_number} 题</span>
                  </Flex>
                }>
                  <Flex vertical gap="small">
                    <Input.TextArea
                      value={q.question_text} autoSize
                      onChange={(e) => setQuestions((qs) => qs.map((x, idx) => idx === i ? { ...x, question_text: e.target.value } : x))}
                      onBlur={(e) => onQuestionChange(i, { question_text: e.target.value })}
                    />
                    <Flex gap="middle" align="center" wrap>
                      <Checkbox checked={q.is_wrong_question} onChange={(e) => onQuestionChange(i, { is_wrong_question: e.target.checked })}>
                        错题
                      </Checkbox>
                      <Input
                        placeholder="学生答案" style={{ maxWidth: 320 }}
                        value={q.student_answer ?? ''}
                        onChange={(e) => setQuestions((qs) => qs.map((x, idx) => idx === i ? { ...x, student_answer: e.target.value } : x))}
                        onBlur={(e) => onQuestionChange(i, { student_answer: e.target.value })}
                      />
                    </Flex>
                    {q.is_wrong_question && !q.original_source && (
                      <LinkOriginalControl examId={exam.id} questionId={q.id} assets={assets} onLinked={reloadQuestions} />
                    )}
                  </Flex>
                </Card>
              ))}
              </Flex>
            )}
            <Button icon={<PlusOutlined />} onClick={onAddQuestion} style={{ alignSelf: 'flex-start' }}>
              手工新增题目
            </Button>
          </Flex>
        </Card>
      </SectionBlock>

      <Button type="primary" size="large" icon={<CheckCircleOutlined />} onClick={onConfirm} loading={confirming}>
        确认本次考试
      </Button>
    </>
  )
}

function ScoringAndAnalysis({ exam, onReopened }: { exam: Exam; onReopened: () => void }) {
  const navigate = useNavigate()
  const [records, setRecords] = useState<QuestionRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [starting, setStarting] = useState(false)
  const [reopening, setReopening] = useState(false)

  useEffect(() => {
    listQuestionRecords(exam.id).then((r) => setRecords(r.records)).finally(() => setLoading(false))
  }, [exam.id])

  async function onScoreChange(record: QuestionRecord, changes: Parameters<typeof updateQuestionRecord>[1]) {
    const updated = await updateQuestionRecord(record.id, changes)
    setRecords((rs) => rs.map((r) => (r.id === record.id ? updated : r)))
  }

  async function onAnalyze() {
    setStarting(true)
    try {
      const run = await createAnalysisRun(exam.submission_id, [exam.id])
      await confirmStartRun(run.id)
      navigate(`/reports/${run.id}`)
    } catch (e: any) {
      message.error(e?.response?.data?.error ?? '创建分析任务失败')
    } finally {
      setStarting(false)
    }
  }

  async function onReopen() {
    setReopening(true)
    try {
      await reopenExam(exam.id)
      message.success('已撤销确认，可以重新解析确认')
      onReopened()
    } catch (e: any) {
      message.error(e?.response?.data?.error ?? '撤销失败')
    } finally {
      setReopening(false)
    }
  }

  return (
    <>
      <Alert
        type="success" showIcon title="考试已确认" description={`版本 v${exam.version ?? 1}`}
        style={{ marginBottom: 24 }}
        action={
          <Popconfirm
            title="撤销确认？" description="会把这次考试退回待确认状态，已确认的题目记录会标记为历史版本。"
            onConfirm={onReopen}
          >
            <Button size="small" icon={<UndoOutlined />} loading={reopening}>撤销确认</Button>
          </Popconfirm>
        }
      />

      <SectionBlock marker="caution" title="得分与批改">
        <Card>
          {loading ? <Spin /> : (
            <Table
              rowKey="id" size="small" pagination={false} dataSource={records}
              columns={[
                { title: '题号', dataIndex: 'question_number', width: 60, render: (v) => <span className="data-figure">{v}</span> },
                { title: '题干', dataIndex: 'question_text', ellipsis: true },
                { title: '错题', render: (_, r: QuestionRecord) => r.is_wrong_question
                  ? <Flex align="center" gap={4}><RedPenMark /><Tag color="red">错题</Tag></Flex>
                  : <Tag>正确</Tag> },
                { title: '得分', render: (_, r: QuestionRecord) => (
                  <InputNumber
                    size="small" value={r.score.obtained ?? undefined} style={{ width: 70 }}
                    onChange={(v) => onScoreChange(r, { score_obtained: v ?? undefined })}
                  />
                ) },
                { title: '满分', render: (_, r: QuestionRecord) => (
                  <InputNumber
                    size="small" value={r.score.total ?? undefined} style={{ width: 70 }}
                    onChange={(v) => onScoreChange(r, { score_total: v ?? undefined })}
                  />
                ) },
                { title: '教师批注', render: (_, r: QuestionRecord) => (
                  <Input
                    size="small" defaultValue={r.correction.teacher_comment}
                    onBlur={(e) => onScoreChange(r, { teacher_comment: e.target.value })}
                  />
                ) },
              ]}
            />
          )}
        </Card>
      </SectionBlock>

      <Button type="primary" size="large" onClick={onAnalyze} loading={starting}>
        确认并开始分析
      </Button>
    </>
  )
}

export default function ExamPage() {
  const { id = '' } = useParams()
  const [exam, setExam] = useState<Exam | null>(null)
  const [loading, setLoading] = useState(true)

  async function reload() {
    setLoading(true)
    try {
      setExam(await getExam(id))
    } catch {
      message.error('加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { reload() }, [id])

  if (loading) return <Spin style={{ margin: 40 }} />
  if (!exam) return <Empty description="考试不存在" style={{ margin: 40 }} />

  return (
    <div className="page-shell">
      <Breadcrumb items={[
        { title: <Link to="/">Exam Diagnosis</Link> },
        { title: <Link to={`/submissions/${exam.submission_id}`}>任务详情</Link> },
        { title: exam.name },
      ]} style={{ marginBottom: 20 }} />

      <Masthead kicker="Exam Diagnosis · 就诊记录" title={exam.name} />

      <div style={{ marginBottom: 28 }}>
        <Steps current={STEP_FOR_STATUS[exam.status] ?? 0} items={STEPS} />
      </div>

      {exam.status === 'uploading' && <UploadStep examId={exam.id} onDone={reload} />}
      {exam.status === 'parsing' && <StuckParsingRecovery examId={exam.id} onDone={reload} />}
      {exam.status === 'waiting_confirmation' && <PagesAndDraft exam={exam} onConfirmed={reload} />}
      {exam.status === 'confirmed' && <ScoringAndAnalysis exam={exam} onReopened={reload} />}
    </div>
  )
}
