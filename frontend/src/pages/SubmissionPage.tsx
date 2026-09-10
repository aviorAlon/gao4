import { useEffect, useState } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import { Breadcrumb, Button, Card, Empty, Flex, Input, Spin, Table, Tag, message } from 'antd'
import { PlusOutlined, DownloadOutlined } from '@ant-design/icons'
import {
  createExam, downloadSubmissionExport, getSubmission, listAnalysisRuns, listExams,
  listMasteryRecords, type AnalysisRunRef, type Exam, type MasteryRecord, type Submission,
} from '../api'
import { Masthead, SectionBlock, Vital, VitalsStrip } from '../chart/Chart'
import TrendsSection from '../components/TrendsSection'
import '../chart/chart.css'

const EXAM_STATUS_LABEL: Record<string, string> = {
  uploading: '待上传', parsing: '解析中', waiting_confirmation: '待确认', confirmed: '已确认',
}
const EXAM_STATUS_COLOR: Record<string, string> = {
  uploading: 'default', parsing: 'gold', waiting_confirmation: 'gold', confirmed: 'green',
}
const RUN_STATUS_LABEL: Record<string, string> = {
  awaiting_confirmation: '待开始', queued: '生成中', report_ready: '报告已生成',
}
const RUN_STATUS_COLOR: Record<string, string> = {
  awaiting_confirmation: 'default', queued: 'gold', report_ready: 'green',
}
const MASTERY_LABEL: Record<string, string> = {
  not_started: '未开始', in_progress: '练习中', provisionally_resolved: '初步掌握', resolved: '已掌握',
}
const MASTERY_COLOR: Record<string, string> = {
  not_started: 'default', in_progress: 'processing', provisionally_resolved: 'gold', resolved: 'success',
}

export default function SubmissionPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [submission, setSubmission] = useState<Submission | null>(null)
  const [exams, setExams] = useState<Exam[]>([])
  const [runs, setRuns] = useState<AnalysisRunRef[]>([])
  const [mastery, setMastery] = useState<MasteryRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [examName, setExamName] = useState('')
  const [creating, setCreating] = useState(false)
  const [exporting, setExporting] = useState(false)

  async function reload() {
    setLoading(true)
    try {
      const [s, e, r, m] = await Promise.all([
        getSubmission(id), listExams(id), listAnalysisRuns(id), listMasteryRecords(id),
      ])
      setSubmission(s)
      setExams(e)
      setRuns(r.runs)
      setMastery(m.records)
    } catch {
      message.error('加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { reload() }, [id])

  async function onCreateExam() {
    setCreating(true)
    try {
      const exam = await createExam(id, examName || `月考${exams.length + 1}`)
      navigate(`/exams/${exam.id}`)
    } catch (e: any) {
      message.error(e?.response?.data?.error ?? '创建失败')
    } finally {
      setCreating(false)
    }
  }

  async function onExport() {
    if (!submission) return
    setExporting(true)
    try {
      await downloadSubmissionExport(id, submission.subject)
      message.success('已开始下载')
    } catch {
      message.error('导出失败')
    } finally {
      setExporting(false)
    }
  }

  if (loading) return <Spin style={{ margin: 40 }} />
  if (!submission) return <Empty description="任务不存在" style={{ margin: 40 }} />

  const confirmedCount = exams.filter((e) => e.status === 'confirmed').length

  return (
    <div className="page-shell">
      <Breadcrumb items={[{ title: <Link to="/">Exam Diagnosis</Link> }, { title: '任务详情' }]} style={{ marginBottom: 20 }} />

      <Masthead
        kicker="Exam Diagnosis · 病历"
        caseId={`#${submission.id.slice(0, 8)}`}
        title={`${submission.subject} · ${submission.province} · ${submission.school_grade}`}
        meta={
          <>
            <span>{submission.academic_year_label} 学年</span>
            <span>·</span>
            <span>知识库 v{submission.knowledge_partition?.version}</span>
            <span>·</span>
            <span>{submission.knowledge_partition?.curriculum}</span>
          </>
        }
      />

      <VitalsStrip>
        <Vital label="考试数" value={`${submission.exam_count}/${submission.max_exam_count}`} />
        <Vital label="已确认" value={confirmedCount} tone={confirmedCount ? 'resolved' : undefined} />
        <Vital label="课程标准年份" value={submission.curriculum_year} />
      </VitalsStrip>

      <SectionBlock marker="neutral" title="考试列表">
        <Card>
          <Flex vertical gap="middle">
            <Flex gap="small">
              <Input
                placeholder="考试名称，如「月考」"
                value={examName}
                onChange={(e) => setExamName(e.target.value)}
                style={{ maxWidth: 240 }}
                disabled={exams.length >= submission.max_exam_count}
              />
              <Button
                type="primary" icon={<PlusOutlined />} onClick={onCreateExam} loading={creating}
                disabled={exams.length >= submission.max_exam_count}
              >
                新建考试
              </Button>
            </Flex>
            {exams.length === 0 ? <Empty description="还没有考试" /> : (
              <Table
                rowKey="id"
                dataSource={exams}
                pagination={false}
                onRow={(r) => ({ onClick: () => navigate(`/exams/${r.id}`), style: { cursor: 'pointer' } })}
                columns={[
                  { title: '#', dataIndex: 'sequence_no', width: 48, render: (v) => <span className="data-figure">{v}</span> },
                  { title: '名称', dataIndex: 'name' },
                  { title: '状态', render: (_, e: Exam) => <Tag color={EXAM_STATUS_COLOR[e.status]}>{EXAM_STATUS_LABEL[e.status] ?? e.status}</Tag> },
                  { title: '题目数', render: (_, e: Exam) => <span className="data-figure">{e.draft_questions?.length ?? e.questions?.length ?? 0}</span> },
                ]}
              />
            )}
          </Flex>
        </Card>
      </SectionBlock>

      <Flex gap="small" style={{ marginBottom: 16 }}>
        <Link to={`/wrong-book/${submission.id}`}><Button>打开错题本</Button></Link>
        <Link to="/review"><Button>复习计划</Button></Link>
      </Flex>

      <TrendsSection submissionId={submission.id} />

      <SectionBlock marker="neutral" title="历史报告">
        <Card>
          {runs.length === 0 ? <Empty description="还没有生成过报告" /> : (
            <Table
              rowKey="id"
              dataSource={runs}
              pagination={false}
              onRow={(r) => (r.status === 'report_ready' ? { onClick: () => navigate(`/reports/${r.id}`), style: { cursor: 'pointer' } } : {})}
              columns={[
                {
                  title: '考试', render: (_, r: AnalysisRunRef) =>
                    r.exam_versions?.map((e) => `${e.name} v${e.version}`).join('、') || r.exam_ids.join('、'),
                },
                { title: '状态', render: (_, r: AnalysisRunRef) => <Tag color={RUN_STATUS_COLOR[r.status]}>{RUN_STATUS_LABEL[r.status] ?? r.status}</Tag> },
                {
                  title: '', width: 90,
                  render: (_, r: AnalysisRunRef) => r.status === 'report_ready' && <a onClick={(e) => { e.stopPropagation(); navigate(`/reports/${r.id}`) }}>查看报告</a>,
                },
              ]}
            />
          )}
        </Card>
      </SectionBlock>

      <SectionBlock marker="resolved" title="掌握状态总览">
        <Card>
          {mastery.length === 0 ? <Empty description="还没有掌握记录，生成报告并完成验证题后会出现在这里" /> : (
            <Table
              rowKey="id"
              dataSource={mastery}
              pagination={false}
              columns={[
                { title: '知识点', dataIndex: 'knowledge_id', render: (v) => <span className="data-figure">{v}</span> },
                { title: '错因', dataIndex: 'error_cluster' },
                { title: '掌握状态', render: (_, m: MasteryRecord) => <Tag color={MASTERY_COLOR[m.status]}>{MASTERY_LABEL[m.status] ?? m.status}</Tag> },
                { title: '作答次数', render: (_, m: MasteryRecord) => <span className="data-figure">{m.attempt_history.length}</span> },
              ]}
            />
          )}
        </Card>
      </SectionBlock>

      <Button icon={<DownloadOutlined />} onClick={onExport} loading={exporting}>
        导出个人数据（JSON）
      </Button>
    </div>
  )
}
