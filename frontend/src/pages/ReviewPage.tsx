import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert, Button, Card, Empty, Flex, Input, InputNumber, Space, Spin, Table, Tag, Typography } from 'antd'
import { message } from '../feedback'
import { CheckOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  fetchReviewPlan, getVerificationQuestion, submitVerificationAttempt,
  type ReviewItem, type ReviewPlan, type VerificationQuestionEntity,
} from '../api'
import { Masthead, SectionBlock, Vital, VitalsStrip } from '../chart/Chart'
import '../chart/chart.css'

const { Text, Paragraph } = Typography

const STATUS_LABEL: Record<string, string> = {
  not_started: '未开始', in_progress: '进行中', provisionally_resolved: '初步解决', resolved: '已解决',
}

const dueLabel = (item: ReviewItem) => {
  if (item.overdue_days > 0) return `逾期 ${item.overdue_days} 天`
  if (item.days_until_due === 0) return '今天'
  if (item.days_until_due == null) return '—'
  return `${item.days_until_due} 天后`
}

const fmtDate = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString('zh-CN') : '—')

/** One due item, expanded for practice: the question, an answer box, and a self-reported score.
 * The backend re-grades with the `grade` model role when it can, so the score here is a record
 * of what the student thought, not the verdict. */
function PracticeCard({ item, onDone }: { item: ReviewItem; onDone: () => void }) {
  const [question, setQuestion] = useState<VerificationQuestionEntity | null>(null)
  const [answer, setAnswer] = useState('')
  const [score, setScore] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ passed: boolean; feedback?: string; gradedBy: string } | null>(null)

  useEffect(() => {
    if (!item.verification_question_id) return
    getVerificationQuestion(item.verification_question_id).then(setQuestion).catch(() => setQuestion(null))
  }, [item.verification_question_id])

  async function submit() {
    if (!item.verification_question_id) return
    if (!answer.trim()) { message.error('请先写下你的作答'); return }
    setBusy(true)
    try {
      const attempt = await submitVerificationAttempt(item.verification_question_id, { answer, score: score ?? 0, max_score: 10 })
      setResult({ passed: attempt.passed, feedback: (attempt as any).auto_graded_feedback, gradedBy: (attempt as any).graded_by ?? 'self' })
      onDone()
    } catch (e: any) {
      message.error(e?.response?.data?.error ?? e.message ?? '提交失败')
    } finally {
      setBusy(false)
    }
  }

  if (!item.verification_question_id) {
    return (
      <Card size="small" type="inner" title={item.knowledge_id}>
        <Text type="secondary">这个知识点还没有对应的验证题——生成一次报告后会出现。</Text>
      </Card>
    )
  }

  return (
    <Card
      size="small" type="inner"
      title={
        <Space wrap>
          <Text strong>{question?.knowledge_id ?? item.knowledge_id}</Text>
          <Tag color="volcano">{item.error_cluster}</Tag>
          <Tag color={item.overdue_days > 0 ? 'red' : undefined}>{dueLabel(item)}</Tag>
          <Text type="secondary">第 {item.review_stage + 1} 轮</Text>
        </Space>
      }
    >
      <Flex vertical gap="small">
        <Paragraph style={{ marginBottom: 0 }}>{question?.question_text ?? item.question_text ?? '加载中…'}</Paragraph>
        {result ? (
          <Alert
            type={result.passed ? 'success' : 'warning'} showIcon
            title={result.passed ? '通过，下次复习已排到更远' : '没通过，会很快再安排一次'}
            description={
              <>
                {result.feedback && <div>{result.feedback}</div>}
                <Text type="secondary">判定方：{result.gradedBy === 'model' ? '模型批改' : '学生自评'}</Text>
              </>
            }
          />
        ) : (
          <>
            <Input.TextArea
              value={answer} onChange={(e) => setAnswer(e.target.value)} autoSize={{ minRows: 2 }}
              placeholder="写下你的解答过程"
            />
            <Space wrap>
              <Text type="secondary">自评得分</Text>
              <InputNumber min={0} max={10} value={score} onChange={setScore} placeholder="0-10" />
              <Text type="secondary">/ 10</Text>
              <Button type="primary" icon={<CheckOutlined />} onClick={submit} loading={busy}>提交</Button>
              {item.analysis_run_id && <Link to={`/reports/${item.analysis_run_id}`}>查看来源报告</Link>}
            </Space>
          </>
        )}
      </Flex>
    </Card>
  )
}

function ScheduleTable({ items, emptyText }: { items: ReviewItem[]; emptyText: string }) {
  if (!items.length) return <Empty description={emptyText} />
  return (
    <Table
      rowKey="mastery_id" size="small" dataSource={items} pagination={{ pageSize: 10, hideOnSinglePage: true }}
      columns={[
        { title: '知识点', dataIndex: 'knowledge_id', render: (v: string) => <Text code>{v}</Text> },
        { title: '错因', dataIndex: 'error_cluster' },
        { title: '掌握状态', render: (_, r: ReviewItem) => <Tag>{STATUS_LABEL[r.status] ?? r.status}</Tag> },
        { title: '轮次', width: 70, render: (_, r: ReviewItem) => <span className="data-figure">{r.review_stage}</span> },
        { title: '下次复习', width: 130, render: (_, r: ReviewItem) => <Space size={4}><span>{fmtDate(r.due_at)}</span><Text type="secondary">{dueLabel(r)}</Text></Space> },
        { title: '作答次数', width: 90, render: (_, r: ReviewItem) => <span className="data-figure">{r.attempt_count}</span> },
      ]}
    />
  )
}

export default function ReviewPage() {
  const [plan, setPlan] = useState<ReviewPlan | null>(null)
  const [loading, setLoading] = useState(true)

  async function reload() {
    setLoading(true)
    try {
      setPlan(await fetchReviewPlan())
    } catch (e: any) {
      message.error(e?.response?.data?.error ?? e.message ?? '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { reload() }, [])

  return (
    <div className="page-shell">
      <Masthead
        kicker="Exam Diagnosis · 复习"
        title="复习计划"
        meta={<span>每道题按 1/3/7/16/35 天的间隔安排复习；通过一次推远一档，没通过就退回第一档</span>}
      />

      {loading && !plan ? <Spin /> : plan && (
        <>
          <VitalsStrip>
            <Vital label="今天要练" value={plan.summary.due_count} tone={plan.summary.due_count ? 'alert' : 'resolved'} />
            <Vital label="其中逾期" value={plan.summary.overdue_count} tone={plan.summary.overdue_count ? 'caution' : undefined} />
            <Vital label="已排期" value={plan.summary.upcoming_count} />
            <Vital label="已巩固" value={plan.summary.completed_count} tone={plan.summary.completed_count ? 'resolved' : undefined} />
          </VitalsStrip>

          <SectionBlock marker={plan.summary.due_count ? 'alert' : 'resolved'} title={`今天要练（${plan.summary.due_count}）`}>
            {plan.due.length === 0 ? (
              <Card>
                <Empty description={plan.summary.next_due_at ? `今天没有要复习的，下一次是 ${fmtDate(plan.summary.next_due_at)}` : '还没有复习任务——生成一次报告后，错因会进入复习计划'} />
              </Card>
            ) : (
              <Flex vertical gap="middle">
                {plan.due.map((item) => <PracticeCard key={item.mastery_id} item={item} onDone={reload} />)}
              </Flex>
            )}
          </SectionBlock>

          <SectionBlock marker="neutral" title={`已排期（${plan.summary.upcoming_count}）`}>
            <Card><ScheduleTable items={plan.upcoming} emptyText="还没有排期中的复习" /></Card>
          </SectionBlock>

          <SectionBlock marker="resolved" title={`已巩固（${plan.summary.completed_count}）`}>
            <Card><ScheduleTable items={plan.completed} emptyText="还没有走完全部复习轮次的知识点" /></Card>
          </SectionBlock>

          <Space>
            <Button icon={<ReloadOutlined />} onClick={reload} loading={loading}>刷新</Button>
            <Text type="secondary" style={{ fontSize: 12 }}>{plan.note}</Text>
          </Space>
        </>
      )}
    </div>
  )
}
