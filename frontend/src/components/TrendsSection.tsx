import { useEffect, useState } from 'react'
import { Alert, Card, Empty, Flex, Space, Spin, Table, Tag, Tooltip, Typography } from 'antd'
import { ArrowDownOutlined, ArrowUpOutlined, MinusOutlined } from '@ant-design/icons'
import { fetchTrends, type KnowledgeTrend, type SubmissionTrends, type TrendDirection, type TrendSeriesPoint } from '../api'
import { SectionBlock } from '../chart/Chart'

const { Text } = Typography

const DIRECTION: Record<TrendDirection, { label: string; color?: string; icon: React.ReactNode }> = {
  improving: { label: '好转', color: 'green', icon: <ArrowDownOutlined /> },
  worsening: { label: '恶化', color: 'red', icon: <ArrowUpOutlined /> },
  stable: { label: '基本持平', icon: <MinusOutlined /> },
  insufficient: { label: '数据不足', icon: <MinusOutlined /> },
}

const pct = (rate: number) => `${Math.round(rate * 100)}%`

/** Error rate over time as a bar per exam. Inline SVG rather than a charting library: it's a
 * handful of bars, and pulling in a chart dependency for this would cost more than it renders. */
function Sparkline({ points, height = 34, barWidth = 16, gap = 5 }: { points: TrendSeriesPoint[]; height?: number; barWidth?: number; gap?: number }) {
  const width = points.length * barWidth + Math.max(0, points.length - 1) * gap
  return (
    <svg width={width} height={height} role="img" aria-label={`各场考试错误率：${points.map((p) => pct(p.error_rate)).join('、')}`}>
      {points.map((point, i) => {
        // Always leave 2px so a zero-error exam still reads as "there was an exam here".
        const barHeight = Math.max(2, Math.round(point.error_rate * (height - 2)))
        const tone = point.error_rate === 0 ? 'var(--resolved)' : point.error_rate >= 0.5 ? 'var(--alert)' : 'var(--caution)'
        return (
          <Tooltip key={point.exam_id} title={`${point.name}：错 ${point.wrong_count}/${point.question_count}（${pct(point.error_rate)}）`}>
            <rect x={i * (barWidth + gap)} y={height - barHeight} width={barWidth} height={barHeight} fill={tone} />
          </Tooltip>
        )
      })}
    </svg>
  )
}

function DirectionTag({ direction, delta }: { direction: TrendDirection; delta: number | null }) {
  const meta = DIRECTION[direction]
  const shift = delta == null || direction === 'insufficient' ? '' : ` ${delta > 0 ? '+' : ''}${Math.round(delta * 100)}pt`
  return <Tag color={meta.color} icon={meta.icon}>{meta.label}{shift}</Tag>
}

export default function TrendsSection({ submissionId }: { submissionId: string }) {
  const [trends, setTrends] = useState<SubmissionTrends | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    fetchTrends(submissionId)
      .then(setTrends)
      .catch((e) => setError(e?.response?.data?.error ?? e.message ?? '加载失败'))
      .finally(() => setLoading(false))
  }, [submissionId])

  const enough = trends && trends.summary.exams_with_questions >= 2
  const marker = trends?.summary.overall_direction === 'worsening' ? 'alert' : trends?.summary.overall_direction === 'improving' ? 'resolved' : 'neutral'

  return (
    <SectionBlock marker={marker} title="纵向趋势">
      <Card>
        {loading ? <Spin /> : error ? <Alert type="error" showIcon title={error} /> : !trends || !enough ? (
          <Empty description={`至少确认两场考试才能比较；当前有 ${trends?.summary.exams_with_questions ?? 0} 场有题目的考试`} />
        ) : (
          <Flex vertical gap="middle">
            <Space wrap size="large">
              <Space>
                <Text type="secondary">整体错误率</Text>
                <span className="data-figure">{pct(trends.summary.first_error_rate ?? 0)}</span>
                <Text type="secondary">→</Text>
                <span className="data-figure">{pct(trends.summary.last_error_rate ?? 0)}</span>
                <DirectionTag direction={trends.summary.overall_direction} delta={trends.summary.overall_delta} />
              </Space>
              <Space>
                <Text type="secondary">共 {trends.summary.exam_count} 场考试 · {trends.summary.total_question_count} 道题</Text>
              </Space>
            </Space>

            <Sparkline points={trends.exams.filter((e) => e.question_count > 0).map((e) => ({ exam_id: e.exam_id, name: e.name, question_count: e.question_count, wrong_count: e.wrong_count, error_rate: e.error_rate }))} height={44} barWidth={24} gap={8} />

            {trends.summary.persistent.length > 0 && (
              <Alert
                type="warning" showIcon
                title={`${trends.summary.persistent.length} 个知识点反复出错，到最近一场仍然错`}
                description={trends.knowledge_trends.filter((t) => t.persistent).map((t) => t.name).join('、')}
              />
            )}

            <Table
              rowKey="knowledge_id" size="small" dataSource={trends.knowledge_trends}
              pagination={{ pageSize: 10, hideOnSinglePage: true }}
              columns={[
                { title: '知识点', render: (_, t: KnowledgeTrend) => (
                  <Space size={6}>
                    <span>{t.name}</span>
                    {t.persistent && <Tag color="volcano">反复出错</Tag>}
                  </Space>
                ) },
                { title: '章节', dataIndex: 'chapter', width: 110 },
                { title: '各场表现', width: 130, render: (_, t: KnowledgeTrend) => <Sparkline points={t.points} /> },
                { title: '错误率', width: 140, render: (_, t: KnowledgeTrend) => t.exam_count < 2
                  ? <Text type="secondary">{pct(t.last_error_rate)}（仅 1 场）</Text>
                  : <span className="data-figure">{pct(t.first_error_rate)} → {pct(t.last_error_rate)}</span> },
                { title: '累计错题', width: 90, render: (_, t: KnowledgeTrend) => <span className="data-figure">{t.total_wrong_count}/{t.total_question_count}</span> },
                { title: '趋势', width: 130, render: (_, t: KnowledgeTrend) => <DirectionTag direction={t.direction} delta={t.delta} /> },
              ]}
            />

            <Text type="secondary" style={{ fontSize: 12 }}>{trends.note}</Text>
          </Flex>
        )}
      </Card>
    </SectionBlock>
  )
}
