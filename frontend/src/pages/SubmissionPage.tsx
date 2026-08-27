import { useEffect, useState } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import { Breadcrumb, Button, Card, Descriptions, Empty, Flex, Input, Spin, Table, Tag, Typography, message } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { createExam, getSubmission, listExams, type Exam, type Submission } from '../api'

const { Title } = Typography

const EXAM_STATUS_LABEL: Record<string, string> = {
  uploading: '待上传', parsing: '解析中', waiting_confirmation: '待确认', confirmed: '已确认',
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

  return (
    <Flex vertical gap="large" style={{ maxWidth: 900, margin: '0 auto', padding: '24px 16px' }}>
      <Breadcrumb items={[{ title: <Link to="/">Exam Diagnosis</Link> }, { title: '任务详情' }]} />
      <Title level={2} style={{ marginBottom: 0 }}>{submission.subject} · {submission.province} · {submission.school_grade}</Title>

      <Card title="任务信息">
        <Descriptions column={2} size="small">
          <Descriptions.Item label="学年">{submission.academic_year_label}</Descriptions.Item>
          <Descriptions.Item label="考试数">{submission.exam_count}/{submission.max_exam_count}</Descriptions.Item>
          <Descriptions.Item label="知识库版本">{submission.knowledge_partition?.version}</Descriptions.Item>
          <Descriptions.Item label="课程标准">{submission.knowledge_partition?.curriculum}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="考试列表">
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
                { title: '#', dataIndex: 'sequence_no', width: 48 },
                { title: '名称', dataIndex: 'name' },
                { title: '状态', render: (_, e: Exam) => <Tag>{EXAM_STATUS_LABEL[e.status] ?? e.status}</Tag> },
                { title: '题目数', render: (_, e: Exam) => e.draft_questions?.length ?? e.questions?.length ?? 0 },
              ]}
            />
          )}
        </Flex>
      </Card>
    </Flex>
  )
}
