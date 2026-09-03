import { useEffect, useState } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import { Breadcrumb, Button, Card, Empty, Flex, Input, Spin, Table, Tag, message } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { createExam, getSubmission, listExams, type Exam, type Submission } from '../api'
import { Masthead, SectionBlock, Vital, VitalsStrip } from '../chart/Chart'
import '../chart/chart.css'

const EXAM_STATUS_LABEL: Record<string, string> = {
  uploading: '待上传', parsing: '解析中', waiting_confirmation: '待确认', confirmed: '已确认',
}
const EXAM_STATUS_COLOR: Record<string, string> = {
  uploading: 'default', parsing: 'gold', waiting_confirmation: 'gold', confirmed: 'green',
}

export default function SubmissionPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [submission, setSubmission] = useState<Submission | null>(null)
  const [exams, setExams] = useState<Exam[]>([])
  const [loading, setLoading] = useState(true)
  const [examName, setExamName] = useState('')
  const [creating, setCreating] = useState(false)

  async function reload() {
    setLoading(true)
    try {
      const [s, e] = await Promise.all([getSubmission(id), listExams(id)])
      setSubmission(s)
      setExams(e)
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
    </div>
  )
}
