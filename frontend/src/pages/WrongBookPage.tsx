import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Alert, Button, Card, Collapse, Empty, Flex, Segmented, Select, Space, Spin, Tag, Typography, message } from 'antd'
import { DownloadOutlined, PrinterOutlined } from '@ant-design/icons'
import {
  downloadWrongBook, fetchWrongBook, listSubmissions,
  type Submission, type WrongBook, type WrongBookFilters, type WrongBookItem,
} from '../api'
import { Masthead, RedPenMark, SectionBlock, Vital, VitalsStrip } from '../chart/Chart'
import '../chart/chart.css'

const { Text, Paragraph } = Typography

const STAGE_LABEL: Record<string, string> = { primary: '小学', middle: '初中', high: '高中' }

function QuestionCard({ item }: { item: WrongBookItem }) {
  return (
    <Card
      size="small" type="inner"
      title={
        <Flex align="center" gap={8} wrap>
          <RedPenMark />
          <Text strong>{item.exam_name} · 第 {item.question_number} 题</Text>
          {item.knowledge_points.map((p) => <Tag key={p.knowledge_id}>{p.name}</Tag>)}
          {item.root_cause && <Tag color="volcano">{item.root_cause}</Tag>}
          {!item.analysed && <Tag>未分析</Tag>}
        </Flex>
      }
    >
      <Flex vertical gap={6}>
        <Paragraph style={{ marginBottom: 0 }}>{item.question_text || '（题干未记录）'}</Paragraph>
        {item.student_answer && <Text type="secondary">上次作答：{item.student_answer}</Text>}
        {item.explanation && <Text type="secondary">分析：{item.explanation}</Text>}
        {item.avoidance_advice && <Text>规避建议：{item.avoidance_advice}</Text>}
        {item.teacher_comment && <Text type="secondary">老师批注：{item.teacher_comment}</Text>}
        {!item.analysed && <Text type="secondary">这道题还没有经过分析，只有原始记录——生成一次报告后会补上错因和建议。</Text>}
        {item.analysis_run_id && <Link to={`/reports/${item.analysis_run_id}`}>查看来源报告</Link>}
      </Flex>
    </Card>
  )
}

export default function WrongBookPage() {
  const { id: routeSubmissionId } = useParams()
  const [submissions, setSubmissions] = useState<Submission[]>([])
  const [submissionId, setSubmissionId] = useState<string | undefined>(routeSubmissionId)
  const [book, setBook] = useState<WrongBook | null>(null)
  const [filters, setFilters] = useState<Partial<WrongBookFilters>>({})
  const [loading, setLoading] = useState(true)
  const [exporting, setExporting] = useState(false)
  const [edition, setEdition] = useState<'review' | 'practice'>('review')

  useEffect(() => {
    listSubmissions()
      .then((all) => {
        setSubmissions(all)
        if (!submissionId && all.length) setSubmissionId(all[0].id)
        if (!all.length) setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!submissionId) return
    setLoading(true)
    fetchWrongBook(submissionId, filters)
      .then(setBook)
      .catch((e) => message.error(e?.response?.data?.error ?? e.message ?? '加载失败'))
      .finally(() => setLoading(false))
  }, [submissionId, filters.knowledge_id, filters.exam_id, filters.root_cause])

  // Group by exam so the book reads like a book, not a flat list.
  const grouped = useMemo(() => {
    const groups: Array<{ examId: string; label: string; items: WrongBookItem[] }> = []
    for (const item of book?.items ?? []) {
      const last = groups[groups.length - 1]
      if (last && last.examId === item.exam_id) last.items.push(item)
      else groups.push({ examId: item.exam_id, label: item.exam_date ? `${item.exam_name}（${item.exam_date}）` : item.exam_name, items: [item] })
    }
    return groups
  }, [book])

  async function onExport(format: 'pdf' | 'docx') {
    if (!submissionId) return
    setExporting(true)
    try {
      await downloadWrongBook(submissionId, { format, edition, ...filters })
    } catch (e: any) {
      message.error(e?.response?.data?.error ?? e.message ?? '导出失败')
    } finally {
      setExporting(false)
    }
  }

  const current = submissions.find((s) => s.id === submissionId)

  return (
    <div className="page-shell">
      <Masthead
        kicker="Exam Diagnosis · 错题本"
        title="错题本"
        meta={<span>{current ? `${STAGE_LABEL[current.stage] ?? current.stage} / ${current.subject} / ${current.school_grade}` : '把每一次错题攒在一起，可以按知识点筛，也可以打印出来重做'}</span>}
      />

      {submissions.length === 0 && !loading ? (
        <Card><Empty description={<span>还没有任务——先到<Link to="/">诊断台</Link>建一个</span>} /></Card>
      ) : (
        <>
          <Flex gap="small" wrap style={{ marginBottom: 16 }}>
            {submissions.length > 1 && (
              <Select
                style={{ minWidth: 240 }} value={submissionId} onChange={(v) => { setSubmissionId(v); setFilters({}) }}
                options={submissions.map((s) => ({ value: s.id, label: `${STAGE_LABEL[s.stage] ?? s.stage} / ${s.subject} / ${s.school_grade}` }))}
              />
            )}
            <Select
              style={{ minWidth: 180 }} allowClear placeholder="全部考试"
              value={filters.exam_id} onChange={(v) => setFilters((f) => ({ ...f, exam_id: v }))}
              options={(book?.facets.exams ?? []).map((e) => ({ value: e.exam_id, label: `${e.name}（${e.count}）` }))}
            />
            <Select
              style={{ minWidth: 200 }} allowClear placeholder="全部知识点"
              value={filters.knowledge_id} onChange={(v) => setFilters((f) => ({ ...f, knowledge_id: v }))}
              options={(book?.facets.knowledge_points ?? []).map((k) => ({ value: k.knowledge_id, label: `${k.name}（${k.count}）` }))}
            />
            <Select
              style={{ minWidth: 180 }} allowClear placeholder="全部错因"
              value={filters.root_cause} onChange={(v) => setFilters((f) => ({ ...f, root_cause: v }))}
              options={(book?.facets.root_causes ?? []).map((c) => ({ value: c.root_cause, label: `${c.root_cause}（${c.count}）` }))}
            />
          </Flex>

          {loading && !book ? <Spin /> : book && (
            <>
              <VitalsStrip>
                <Vital label="错题数" value={book.summary.total} tone={book.summary.total ? 'alert' : 'resolved'} />
                <Vital label="已分析" value={book.summary.analysed} />
                <Vital label="涉及考试" value={book.summary.exam_count} />
              </VitalsStrip>

              <SectionBlock marker="neutral" title="打印 / 导出">
                <Card>
                  <Flex vertical gap="small">
                    <Space wrap>
                      <Segmented
                        value={edition} onChange={(v) => setEdition(v as 'review' | 'practice')}
                        options={[{ label: '复习版', value: 'review' }, { label: '练习版', value: 'practice' }]}
                      />
                      <Button icon={<PrinterOutlined />} onClick={() => onExport('pdf')} loading={exporting}>导出 PDF</Button>
                      <Button icon={<DownloadOutlined />} onClick={() => onExport('docx')} loading={exporting}>导出 Word</Button>
                    </Space>
                    <Text type="secondary">
                      {edition === 'practice'
                        ? '练习版只印题干和空白作答区——重做时看不到上次的错误答案和结论，才练得出东西。'
                        : '复习版包含上次的作答、错因和规避建议。'}
                      {(filters.exam_id || filters.knowledge_id || filters.root_cause) && ' 当前筛选条件会一起带进导出。'}
                    </Text>
                  </Flex>
                </Card>
              </SectionBlock>

              {book.summary.total === 0 ? (
                <Card><Empty description="这个筛选条件下没有错题" /></Card>
              ) : (
                <>
                  {book.summary.analysed < book.summary.total && (
                    <Alert
                      type="info" showIcon style={{ marginBottom: 16 }}
                      title={`${book.summary.total - book.summary.analysed} 道题还没有经过分析`}
                      description="它们仍然收录在错题本里，只是没有错因和规避建议——对相关考试生成一次报告即可补上。"
                    />
                  )}
                  <Collapse
                    defaultActiveKey={grouped.map((g) => g.examId)}
                    items={grouped.map((group) => ({
                      key: group.examId,
                      label: <Space><Text strong>{group.label}</Text><Text type="secondary">{group.items.length} 道</Text></Space>,
                      children: <Flex vertical gap="middle">{group.items.map((item) => <QuestionCard key={item.question_id} item={item} />)}</Flex>,
                    }))}
                  />
                </>
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}
