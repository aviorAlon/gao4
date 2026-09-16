import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  Button, Card, Empty, Flex, Form, Popconfirm, Select, Spin, Table, Tag, Typography, Upload,
} from 'antd'
import { message } from '../feedback'
import { PlusOutlined, DeleteOutlined, UploadOutlined } from '@ant-design/icons'
import {
  createSubmission, deleteSubmission, importSubmission, listKnowledgePartitions, listSubmissions,
  type KnowledgePartition, type Submission,
} from '../api'
import { Masthead, SectionBlock } from '../chart/Chart'
import { useIsNarrow } from '../useIsNarrow'
import '../chart/chart.css'

const { Text } = Typography

const STAGE_LABEL: Record<string, string> = { primary: '小学', middle: '初中', high: '高中' }

export default function HomePage() {
  const navigate = useNavigate()
  const narrow = useIsNarrow()
  const [partitions, setPartitions] = useState<KnowledgePartition[]>([])
  const [submissions, setSubmissions] = useState<Submission[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [importing, setImporting] = useState(false)
  const [form] = Form.useForm()

  async function reload() {
    setLoading(true)
    try {
      const [p, s] = await Promise.all([listKnowledgePartitions(), listSubmissions()])
      setPartitions(p)
      setSubmissions(s)
    } catch {
      message.error('加载失败，请检查后端服务是否在运行')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { reload() }, [])

  const partitionKey = (p: KnowledgePartition) => `${p.stage}|${p.subject}|${p.province}|${p.curriculum_year}`

  async function onCreate(values: { partitionKey: string; school_grade: string; academic_year_label: string }) {
    const [stage, subject, province, curriculum_year] = values.partitionKey.split('|')
    setCreating(true)
    try {
      const submission = await createSubmission({
        stage, subject, province, curriculum_year: Number(curriculum_year),
        school_grade: values.school_grade, academic_year_label: values.academic_year_label,
      })
      message.success('任务创建成功')
      navigate(`/submissions/${submission.id}`)
    } catch (e: any) {
      message.error(e?.response?.data?.error ?? '创建失败')
    } finally {
      setCreating(false)
    }
  }

  async function onImport(file: File) {
    setImporting(true)
    try {
      const result = await importSubmission(file)
      const missing = result.missing_files.length
      message.success(
        `已导入：${result.imported.exams} 场考试、${result.imported.question_records} 道题` +
        (missing ? `，${missing} 个上传文件不在备份里（JSON 备份不含图片）` : `，${result.restored_files} 个文件已还原`),
      )
      await reload()
      navigate(`/submissions/${result.submission_id}`)
    } catch (e: any) {
      const data = e?.response?.data
      message.error(data?.error === 'invalid_bundle' ? `这不像本应用导出的备份：${data.detail}` : data?.error ?? e.message ?? '导入失败')
    } finally {
      setImporting(false)
    }
    return false  // never let antd upload it itself
  }

  async function onDelete(id: string) {
    await deleteSubmission(id)
    message.success('已删除')
    reload()
  }

  const watchedPartitionKey = Form.useWatch('partitionKey', form)
  const selectedPartition = partitions.find((p) => partitionKey(p) === watchedPartitionKey)
  const gradeOptions = selectedPartition?.academic_year_range.map((g) => ({ value: g, label: g })) ?? []

  return (
    <div className="page-shell">
      <Masthead
        kicker="Exam Diagnosis · 诊断台"
        title="Exam Diagnosis"
        meta={
          <>
            <span>{submissions.length ? `${submissions.length} 个任务在案` : '还没有任务'}</span>
            <span>·</span>
            <Link to="/knowledge">知识库</Link>
            <span>·</span>
            <Link to="/status">系统状态</Link>
            <span>·</span>
            <Link to="/settings">设置</Link>
          </>
        }
      />

      <SectionBlock marker="neutral" title="创建诊断任务">
        <Card>
          <Form form={form} layout={narrow ? 'vertical' : 'inline'} onFinish={onCreate} disabled={creating}>
            <Form.Item name="partitionKey" label="知识库分区" rules={[{ required: true, message: '请选择知识库分区' }]}>
              <Select
                style={{ minWidth: narrow ? undefined : 260, width: narrow ? "100%" : undefined }}
                placeholder="阶段 / 科目 / 省份"
                options={partitions.map((p) => ({
                  value: partitionKey(p),
                  label: `${STAGE_LABEL[p.stage] ?? p.stage} / ${p.subject} / ${p.province} · 课标${p.curriculum_year}`,
                }))}
              />
            </Form.Item>
            <Form.Item name="school_grade" label="年级" rules={[{ required: true, message: '请选择年级' }]}>
              <Select style={{ minWidth: narrow ? undefined : 120, width: narrow ? "100%" : undefined }} placeholder="年级" options={gradeOptions} disabled={!selectedPartition} />
            </Form.Item>
            <Form.Item name="academic_year_label" label="学年" rules={[{ required: true, message: '如 25-26' }]} initialValue="25-26">
              <Select style={{ minWidth: narrow ? undefined : 100, width: narrow ? "100%" : undefined }} options={[{ value: '25-26', label: '25-26' }, { value: '26-27', label: '26-27' }]} />
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit" icon={<PlusOutlined />} loading={creating}>创建任务</Button>
            </Form.Item>
          </Form>
        </Card>
      </SectionBlock>

      <SectionBlock marker="neutral" title="导入备份">
        <Card>
          <Flex vertical gap="small" align="flex-start">
            <Upload accept=".zip,.json" showUploadList={false} beforeUpload={onImport} disabled={importing}>
              <Button icon={<UploadOutlined />} loading={importing}>选择备份文件（.zip / .json）</Button>
            </Upload>
            <Text type="secondary">
              导入总是新建一个任务，不会覆盖已有数据——同一份备份导两次就是两份独立的副本。
              zip 备份含上传的图片；JSON 只有数据，图片会标为缺失。
            </Text>
          </Flex>
        </Card>
      </SectionBlock>

      <SectionBlock marker="neutral" title="已有任务">
        <Card>
          {loading ? <Spin /> : submissions.length === 0 ? (
            <Empty description="还没有任务" />
          ) : (
            <Table
              rowKey="id"
              dataSource={submissions}
              pagination={false}
              onRow={(r) => ({ onClick: () => navigate(`/submissions/${r.id}`), style: { cursor: 'pointer' } })}
              columns={[
                { title: '阶段/科目/省份', render: (_, s: Submission) => `${STAGE_LABEL[s.stage] ?? s.stage} / ${s.subject} / ${s.province}` },
                { title: '年级', dataIndex: 'school_grade' },
                { title: '学年', dataIndex: 'academic_year_label' },
                { title: '考试数', render: (_, s: Submission) => <span className="data-figure">{s.exam_count}/{s.max_exam_count}</span> },
                { title: '状态', render: (_, s: Submission) => <Tag className="case-row-tag">{s.status}</Tag> },
                {
                  title: '', width: 48,
                  render: (_, s: Submission) => (
                    <Popconfirm title="确认删除这个任务？" onConfirm={(e) => { e?.stopPropagation(); onDelete(s.id) }} onCancel={(e) => e?.stopPropagation()}>
                      <Button danger type="text" icon={<DeleteOutlined />} onClick={(e) => e.stopPropagation()} />
                    </Popconfirm>
                  ),
                },
              ]}
            />
          )}
        </Card>
      </SectionBlock>
    </div>
  )
}
