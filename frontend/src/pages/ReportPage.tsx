import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  Alert, Breadcrumb, Button, Card, Collapse, Descriptions, Divider, Empty, Flex, Input, InputNumber, Progress,
  Result, Space, Spin, Statistic, Table, Tag, Typography, Row, Col, message,
} from 'antd'
import {
  BulbOutlined, CheckCircleOutlined, ExclamationCircleOutlined,
} from '@ant-design/icons'
import {
  API_BASE, fetchReport, listMasteryRecords, listVerificationQuestions, reviewVerificationQuestion,
  submitVerificationAttempt, type AnalysisReport, type MasteryRecord, type ReviewOrderItem,
  type VerificationQuestionEntity, type WrongQuestion,
} from '../api'

const { Title, Text, Paragraph } = Typography

const PRIORITY_COLOR: Record<string, string> = {
  high: 'red', medium: 'orange', low: 'blue', none: 'default',
}
const PRIORITY_LABEL: Record<string, string> = {
  high: '高', medium: '中', low: '低', none: '暂无错题',
}
const SEVERITY_TO_ALERT_TYPE: Record<string, 'warning' | 'info' | 'error'> = {
  warning: 'warning', info: 'info',
}

function PriorityTag({ priority }: { priority: string }) {
  return <Tag color={PRIORITY_COLOR[priority] ?? 'default'}>{PRIORITY_LABEL[priority] ?? priority}</Tag>
}

// Ant Design v6 deprecated the `List` component outright ("will be removed in next major
// version") — rather than build new code on something the library says it's removing, rows here
// are plain Flex + Divider stacks.
function ItemStack({ children }: { children: React.ReactNode[] }) {
  return (
    <Flex vertical>
      {children.map((child, i) => (
        <div key={i}>
          {i > 0 && <Divider style={{ margin: '12px 0' }} />}
          {child}
        </div>
      ))}
    </Flex>
  )
}

function SummaryCard({ summary }: { summary?: string }) {
  if (summary) {
    return (
      <Card>
        <Flex gap="small" align="flex-start">
          <BulbOutlined style={{ fontSize: 20, color: '#faad14', marginTop: 4 }} />
          <Paragraph style={{ marginBottom: 0, fontSize: 15 }}>{summary}</Paragraph>
        </Flex>
      </Card>
    )
  }
  return (
    <Card>
      <Text type="secondary">
        本次报告没有生成模型概述（<code>report</code> 角色未配置或调用失败），以下内容均来自结构化统计。
      </Text>
    </Card>
  )
}

function QualityFlags({ flags }: { flags: AnalysisReport['quality_flags'] }) {
  if (!flags.length) return null
  return (
    <Card title="结论可信度提示">
      <Flex vertical gap="small">
        {flags.map((flag, i) => (
          <Alert key={i} type={SEVERITY_TO_ALERT_TYPE[flag.severity] ?? 'info'} title={flag.message} showIcon />
        ))}
      </Flex>
    </Card>
  )
}

function ScopeCoverageCard({ scope }: { scope: AnalysisReport['scope_coverage'] }) {
  return (
    <Card title="课程范围与资料覆盖范围">
      <Row gutter={24} align="middle">
        <Col flex="140px">
          <Progress
            type="circle" size={110}
            percent={Math.round(scope.coverage_ratio * 100)}
            format={(p) => `${p}%`}
          />
        </Col>
        <Col flex="auto">
          <Descriptions column={1} size="small">
            <Descriptions.Item label="课程范围知识点">{scope.curriculum_point_count}</Descriptions.Item>
            <Descriptions.Item label="本次资料考查到">{scope.assessed_point_count}</Descriptions.Item>
            <Descriptions.Item label="本次资料未考查">{scope.not_assessed_point_count}</Descriptions.Item>
            <Descriptions.Item label="未考查章节">
              {scope.not_assessed_chapters.length
                ? scope.not_assessed_chapters.map((c) => <Tag key={c}>{c}</Tag>)
                : <Text type="secondary">无</Text>}
            </Descriptions.Item>
          </Descriptions>
          <Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>{scope.note}</Paragraph>
        </Col>
      </Row>
    </Card>
  )
}

function ClustersCard({ clusters }: { clusters: AnalysisReport['clusters'] }) {
  if (!clusters.length) {
    return <Card title="错因专题"><Empty description="暂无错题，没有可分析的错因专题" /></Card>
  }
  const items = clusters.map((cluster) => ({
    key: cluster.cluster_id,
    label: (
      <Space>
        <Text strong>{cluster.root_cause}</Text>
        <Tag color="volcano">优先级 {cluster.priority}</Tag>
        <Text type="secondary">出现 {cluster.occurrence_count} 次</Text>
      </Space>
    ),
    children: (
      <>
        <Paragraph><Text strong>规避建议：</Text>{cluster.avoidance_advice}</Paragraph>
        <ItemStack>
          {cluster.question_examples.map((ex) => (
            <Flex vertical gap={0} key={ex.question_id}>
              <Text>{ex.question_text || '（题干未记录）'}</Text>
              {ex.student_answer && <Text type="secondary">学生作答：{ex.student_answer}</Text>}
            </Flex>
          ))}
        </ItemStack>
      </>
    ),
  }))
  return <Card title="错因专题"><Collapse items={items} /></Card>
}

function KnowledgeTreeCard({ tree }: { tree: AnalysisReport['knowledge_tree'] }) {
  const chapters = Object.entries(tree)
  if (!chapters.length) {
    return <Card title="知识树"><Empty description="暂无知识点数据" /></Card>
  }
  return (
    <Card title="知识树">
      <Flex vertical gap="large">
        {chapters.map(([chapter, points]) => (
          <div key={chapter}>
            <Title level={5}>{chapter}</Title>
            <ItemStack>
              {Object.entries(points).map(([name, stats]) => (
                <Flex justify="space-between" wrap key={name}>
                  <Text>{name}</Text>
                  <Space>
                    <Text type="secondary">错题 {stats.wrong_count}/{stats.question_count}</Text>
                    <Text type="secondary">错误率 {(stats.error_rate * 100).toFixed(0)}%</Text>
                    <PriorityTag priority={stats.review_priority} />
                  </Space>
                </Flex>
              ))}
            </ItemStack>
          </div>
        ))}
      </Flex>
    </Card>
  )
}

function ReviewOrderCard({ items }: { items: ReviewOrderItem[] }) {
  if (!items.length) return null
  return (
    <Card title="复习优先顺序">
      <Table
        rowKey={(r) => `${r.chapter}:${r.name}`}
        size="small"
        pagination={false}
        dataSource={items}
        columns={[
          { title: '#', render: (_, __, i) => i + 1, width: 48 },
          { title: '章节', dataIndex: 'chapter' },
          { title: '知识点', dataIndex: 'name' },
          { title: '错题', render: (_, r: ReviewOrderItem) => `${r.wrong_count}/${r.question_count}` },
          { title: '优先级', render: (_, r: ReviewOrderItem) => <PriorityTag priority={r.review_priority} /> },
        ]}
      />
    </Card>
  )
}

function WrongQuestionsCard({ items }: { items: WrongQuestion[] }) {
  if (!items.length) {
    return <Card title="错题集"><Empty description="暂无错题" /></Card>
  }
  return (
    <Card title="错题集">
      <ItemStack>
        {items.map((q) => (
          <Flex vertical gap={4} key={q.question_id}>
            <Text>{q.question_text || '（题干未记录）'}</Text>
            {q.student_answer && <Text type="secondary">学生作答：{q.student_answer}</Text>}
            <Space wrap>
              <Tag color="red">{q.root_cause}</Tag>
              {q.explanation && <Text type="secondary">{q.explanation}</Text>}
            </Space>
            <Text type="secondary">规避建议：{q.avoidance_advice}</Text>
          </Flex>
        ))}
      </ItemStack>
    </Card>
  )
}

const MASTERY_LABEL: Record<string, string> = {
  not_started: '未开始', in_progress: '练习中', provisionally_resolved: '初步掌握', resolved: '已掌握',
}
const MASTERY_COLOR: Record<string, string> = {
  not_started: 'default', in_progress: 'processing', provisionally_resolved: 'gold', resolved: 'success',
}

function MasteryTag({ status }: { status?: string }) {
  if (!status) return null
  return <Tag color={MASTERY_COLOR[status] ?? 'default'}>{MASTERY_LABEL[status] ?? status}</Tag>
}

/** Admin-side gate: approve (optionally correcting) a reference answer the review pass withheld. */
function ReviewGate({ question, onReviewed }: { question: VerificationQuestionEntity; onReviewed: () => void }) {
  const [draftAnswer, setDraftAnswer] = useState('')
  const [busy, setBusy] = useState(false)

  async function onApprove() {
    if (!draftAnswer.trim()) { message.error('答案不能为空'); return }
    setBusy(true)
    try {
      await reviewVerificationQuestion(question.id, { approved: true, answer: draftAnswer })
      message.success('已复核通过')
      onReviewed()
    } catch (e: any) {
      message.error(e?.response?.data?.error ?? '复核失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Alert
      type="warning" showIcon title="参考答案未通过复核，暂不展示给学生"
      description={
        <Flex vertical gap="small" style={{ marginTop: 8 }}>
          <Text type="secondary">{question.withheld_reason?.join('；')}</Text>
          <Input.TextArea
            placeholder="人工填写/修正参考答案后通过复核" autoSize
            value={draftAnswer} onChange={(e) => setDraftAnswer(e.target.value)}
          />
          <Button size="small" onClick={onApprove} loading={busy} style={{ alignSelf: 'flex-start' }}>
            复核通过
          </Button>
        </Flex>
      }
    />
  )
}

/** Student-side loop: reveal the answer on demand, self-score an attempt, see pass/fail immediately. */
function VerificationQuestionItem({ question, mastery, onChanged }: {
  question: VerificationQuestionEntity
  mastery?: MasteryRecord
  onChanged: () => void
}) {
  const [showAnswer, setShowAnswer] = useState(false)
  const [answer, setAnswer] = useState('')
  const [score, setScore] = useState<number | null>(null)
  const [maxScore, setMaxScore] = useState<number | null>(10)
  const [submitting, setSubmitting] = useState(false)

  const lastAttempt = question.attempts[question.attempts.length - 1]

  async function onSubmit() {
    if (score == null || maxScore == null) { message.error('请填写得分和满分'); return }
    setSubmitting(true)
    try {
      await submitVerificationAttempt(question.id, { answer, score, max_score: maxScore })
      message.success('提交成功')
      setAnswer('')
      onChanged()
    } catch (e: any) {
      message.error(e?.response?.data?.error ?? '提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Flex vertical gap={8}>
      <Flex justify="space-between" align="center" wrap>
        <Text strong>针对错因：{question.target_error}</Text>
        <Space>
          <MasteryTag status={mastery?.status} />
          {question.review_status === 'approved'
            ? <Tag icon={<CheckCircleOutlined />} color="success">已复核</Tag>
            : <Tag icon={<ExclamationCircleOutlined />} color="warning">待复核</Tag>}
        </Space>
      </Flex>
      <Text>{question.question_text}</Text>

      {question.answer_withheld
        ? <ReviewGate question={question} onReviewed={onChanged} />
        : showAnswer
          ? <Text type="secondary">参考答案：{question.answer}</Text>
          : <Button size="small" onClick={() => setShowAnswer(true)}>查看参考答案</Button>}

      <Flex gap="small" wrap align="flex-end">
        <Input.TextArea
          placeholder="在这里写下你的作答" autoSize style={{ minWidth: 260, flex: 1 }}
          value={answer} onChange={(e) => setAnswer(e.target.value)}
        />
        <InputNumber placeholder="得分" min={0} value={score} onChange={setScore} style={{ width: 80 }} />
        <Text type="secondary">/</Text>
        <InputNumber placeholder="满分" min={1} value={maxScore} onChange={setMaxScore} style={{ width: 80 }} />
        <Button type="primary" onClick={onSubmit} loading={submitting} disabled={question.answer_withheld}>
          提交作答
        </Button>
      </Flex>

      {lastAttempt && (
        <Alert
          type={lastAttempt.passed ? 'success' : 'error'} showIcon
          title={lastAttempt.passed ? '本次通过' : '本次未通过（需要 ≥80% 才算通过）'}
          description={`最近一次：${lastAttempt.score}/${lastAttempt.max_score} 分，累计作答 ${question.attempts.length} 次`}
        />
      )}
    </Flex>
  )
}

function VerificationQuestionsCard({ questions, mastery, onChanged }: {
  questions: VerificationQuestionEntity[]
  mastery: MasteryRecord[]
  onChanged: () => void
}) {
  if (!questions.length) return null
  const masteryFor = (q: VerificationQuestionEntity) =>
    mastery.find((m) => m.knowledge_id === q.knowledge_id && m.error_cluster === q.target_error)
  return (
    <Card title="验证题">
      <ItemStack>
        {questions.map((q) => (
          <VerificationQuestionItem key={q.id} question={q} mastery={masteryFor(q)} onChanged={onChanged} />
        ))}
      </ItemStack>
    </Card>
  )
}

export default function ReportPage() {
  const { runId: routeRunId } = useParams()
  const navigate = useNavigate()
  const runId = routeRunId ?? new URLSearchParams(window.location.search).get('run') ?? ''
  const [inputValue, setInputValue] = useState(runId)
  const [report, setReport] = useState<AnalysisReport | null>(null)
  const [verificationQuestions, setVerificationQuestions] = useState<VerificationQuestionEntity[]>([])
  const [mastery, setMastery] = useState<MasteryRecord[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function reloadVerification() {
    if (!runId) return
    const { questions } = await listVerificationQuestions(runId)
    setVerificationQuestions(questions)
    const submissionId = questions[0]?.submission_id
    if (submissionId) {
      const { records } = await listMasteryRecords(submissionId)
      setMastery(records)
    }
  }

  useEffect(() => {
    setInputValue(runId)
    if (!runId) return
    setLoading(true)
    setError(null)
    fetchReport(runId)
      .then(setReport)
      .catch((e) => setError(e?.response?.data?.error ?? e.message ?? '加载失败'))
      .finally(() => setLoading(false))
    reloadVerification()
  }, [runId])

  return (
    <Flex vertical gap="large" style={{ maxWidth: 900, margin: '0 auto', padding: '24px 16px' }}>
      <Breadcrumb items={[{ title: <Link to="/">Exam Diagnosis</Link> }, { title: '报告' }]} />
      <Title level={2} style={{ marginBottom: 0 }}>Exam Diagnosis 报告</Title>

      <Space.Compact style={{ maxWidth: 480 }}>
        <Input
          placeholder="粘贴 analysis_run_id"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onPressEnter={() => navigate(`/reports/${inputValue.trim()}`)}
        />
      </Space.Compact>

      {loading && <Spin />}
      {error && <Result status="error" title="加载失败" subTitle={error} />}

      {!loading && !error && report && (
        <>
          <Row gutter={16}>
            <Col span={8}><Card><Statistic title="题目总数" value={report.question_count} /></Card></Col>
            <Col span={8}>
              <Card>
                <Statistic
                  title="错题数" value={report.wrong_question_count}
                  styles={report.wrong_question_count ? { content: { color: '#cf1322' } } : undefined}
                />
              </Card>
            </Col>
            <Col span={8}><Card><Statistic title="课程覆盖率" value={Math.round(report.scope_coverage.coverage_ratio * 100)} suffix="%" /></Card></Col>
          </Row>

          <SummaryCard summary={report.summary} />
          <QualityFlags flags={report.quality_flags} />
          <ScopeCoverageCard scope={report.scope_coverage} />
          <ClustersCard clusters={report.clusters} />
          <KnowledgeTreeCard tree={report.knowledge_tree} />
          <ReviewOrderCard items={report.review_order} />
          <WrongQuestionsCard items={report.wrong_question_collection} />
          <VerificationQuestionsCard questions={verificationQuestions} mastery={mastery} onChanged={reloadVerification} />

          <Space>
            <a href={`${API_BASE}/api/reports/${report.analysis_run_id}/export?format=md`}>导出 Markdown</a>
            <a href={`${API_BASE}/api/reports/${report.analysis_run_id}/export?format=html`}>导出 HTML</a>
          </Space>
        </>
      )}

      {!loading && !error && !report && !runId && (
        <Empty description="在上方输入 analysis_run_id 查看报告" />
      )}
    </Flex>
  )
}
