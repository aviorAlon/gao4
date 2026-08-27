import { useEffect, useState } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import {
  Alert, Breadcrumb, Button, Card, Checkbox, Empty, Flex, Input, InputNumber, Select, Spin,
  Steps, Table, Tag, Typography, Upload, message,
} from 'antd'
import type { UploadFile } from 'antd'
import { InboxOutlined, CheckCircleOutlined } from '@ant-design/icons'
import {
  confirmExam, createAnalysisRun, createUploadSession, confirmStartRun, finishUploadSession,
  getExam, listPages, listQuestionRecords, runJob, updatePage, updateParseQuestion,
  updateQuestionRecord, uploadSessionAsset,
  type Exam, type ExamPage as ExamPageData, type QuestionRecord,
} from '../api'

const { Title } = Typography
const { Dragger } = Upload

const MATERIAL_TYPE_OPTIONS = [
  { value: 'paper', label: '试卷' },
  { value: 'answer_key', label: '答案' },
  { value: 'student_answer', label: '学生作答' },
  { value: 'correction', label: '批改' },
  { value: 'unknown', label: '未知' },
]

const STEP_FOR_STATUS: Record<string, number> = {
  uploading: 0, parsing: 1, waiting_confirmation: 2, confirmed: 3,
}

function UploadStep({ examId, onDone }: { examId: string; onDone: () => void }) {
  const [fileList, setFileList] = useState<UploadFile[]>([])
  const [busy, setBusy] = useState(false)

  async function onSubmit() {
    if (!fileList.length) return
    setBusy(true)
    try {
      const session = await createUploadSession(examId)
      for (const f of fileList) {
        if (f.originFileObj) await uploadSessionAsset(session.id, f.originFileObj)
      }
      const finished = await finishUploadSession(session.id)
      if (finished.job_id) {
        await runJob(finished.job_id)
      }
      message.success('解析完成')
      onDone()
    } catch (e: any) {
      message.error(e?.response?.data?.error ?? '上传或解析失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="上传考试资料">
      <Flex vertical gap="middle">
        <Dragger
          multiple fileList={fileList} beforeUpload={() => false}
          onChange={({ fileList: fl }) => setFileList(fl)}
          accept="image/jpeg,image/png,image/webp,application/pdf,text/plain"
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p>点击或拖拽图片 / PDF / 文本文件到此处</p>
        </Dragger>
        <Button type="primary" onClick={onSubmit} loading={busy} disabled={!fileList.length}>
          上传全部并运行解析
        </Button>
      </Flex>
    </Card>
  )
}

function PagesAndDraft({ exam, onConfirmed }: { exam: Exam; onConfirmed: () => void }) {
  const [pages, setPages] = useState<ExamPageData[]>([])
  const [questions, setQuestions] = useState(exam.draft_questions)
  const [loading, setLoading] = useState(true)
  const [confirming, setConfirming] = useState(false)

  useEffect(() => {
    listPages(exam.id).then((r) => setPages(r.pages)).finally(() => setLoading(false))
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

  async function onConfirm() {
    setConfirming(true)
    try {
      await confirmExam(exam.id)
      message.success('考试已确认')
      onConfirmed()
    } catch (e: any) {
      message.error(e?.response?.data?.error === 'wrong_question_and_original_required'
        ? '至少需要一道题标记为「错题」并有可用的原题来源，才能确认考试'
        : (e?.response?.data?.error ?? '确认失败'))
    } finally {
      setConfirming(false)
    }
  }

  if (loading) return <Spin />

  return (
    <Flex vertical gap="large">
      <Card title="页面与材料">
        <Table
          rowKey="id" size="small" pagination={false} dataSource={pages}
          columns={[
            { title: '页', dataIndex: 'page_order', width: 60 },
            { title: '材料类型', render: (_, p: ExamPageData) => (
              <Select
                size="small" value={p.material_type} style={{ width: 130 }}
                options={MATERIAL_TYPE_OPTIONS}
                onChange={(v) => onPageChange(p.id, { material_type: v })}
              />
            ) },
            { title: '计入解析统计', render: (_, p: ExamPageData) => (
              <Checkbox checked={p.include_in_analysis} onChange={(e) => onPageChange(p.id, { include_in_analysis: e.target.checked })} />
            ) },
            { title: '状态', render: (_, p: ExamPageData) => p.review_required
              ? <Tag color="orange">需人工确认</Tag> : <Tag color="green">已解析</Tag> },
          ]}
        />
      </Card>

      <Card title={`题目草稿（${questions.length} 道）`}>
        {questions.length === 0 ? <Empty description="没有识别出题目，可能需要调整解析规则或人工补录" /> : (
          <Flex vertical gap="middle">
            {questions.map((q, i) => (
              <Card key={q.id} size="small" type="inner" title={`第 ${q.question_number} 题`}>
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
                </Flex>
              </Card>
            ))}
          </Flex>
        )}
      </Card>

      <Button type="primary" size="large" icon={<CheckCircleOutlined />} onClick={onConfirm} loading={confirming}>
        确认本次考试
      </Button>
    </Flex>
  )
}

function ScoringAndAnalysis({ exam }: { exam: Exam }) {
  const navigate = useNavigate()
  const [records, setRecords] = useState<QuestionRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [starting, setStarting] = useState(false)

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

  return (
    <Flex vertical gap="large">
      <Alert type="success" showIcon title="考试已确认" description={`版本 v${exam.version ?? 1}`} />

      <Card title="得分与批改">
        {loading ? <Spin /> : (
          <Table
            rowKey="id" size="small" pagination={false} dataSource={records}
            columns={[
              { title: '题号', dataIndex: 'question_number', width: 60 },
              { title: '题干', dataIndex: 'question_text', ellipsis: true },
              { title: '错题', render: (_, r: QuestionRecord) => r.is_wrong_question ? <Tag color="red">错题</Tag> : <Tag>正确</Tag> },
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

      <Button type="primary" size="large" onClick={onAnalyze} loading={starting}>
        确认并开始分析
      </Button>
    </Flex>
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
    <Flex vertical gap="large" style={{ maxWidth: 900, margin: '0 auto', padding: '24px 16px' }}>
      <Breadcrumb items={[
        { title: <Link to="/">Exam Diagnosis</Link> },
        { title: <Link to={`/submissions/${exam.submission_id}`}>任务详情</Link> },
        { title: exam.name },
      ]} />
      <Title level={2} style={{ marginBottom: 0 }}>{exam.name}</Title>

      <Steps
        current={STEP_FOR_STATUS[exam.status] ?? 0}
        items={[{ title: '上传' }, { title: '解析' }, { title: '确认' }, { title: '得分与分析' }]}
      />

      {exam.status === 'uploading' && <UploadStep examId={exam.id} onDone={reload} />}
      {exam.status === 'parsing' && <Spin tip="解析中…" />}
      {exam.status === 'waiting_confirmation' && <PagesAndDraft exam={exam} onConfirmed={reload} />}
      {exam.status === 'confirmed' && <ScoringAndAnalysis exam={exam} />}
    </Flex>
  )
}
