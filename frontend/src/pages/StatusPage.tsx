import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert, Button, Card, Flex, Space, Spin, Table, Tag, Typography } from 'antd'
import { message } from '../feedback'
import { ReloadOutlined } from '@ant-design/icons'
import {
  fetchSystemStatus, listModelCalls, listTasks,
  type ModelCall, type ModelCallsResponse, type ModelRoleStatus, type SystemStatus, type TaskRecord,
} from '../api'
import { Masthead, SectionBlock, Vital, VitalsStrip } from '../chart/Chart'
import '../chart/chart.css'

const { Text } = Typography

const TASK_KIND: Record<string, string> = { parse_exam: '解析考试', generate_report: '生成报告' }
const TASK_STATUS: Record<string, { label: string; color?: string }> = {
  queued: { label: '排队中', color: 'default' }, running: { label: '运行中', color: 'processing' },
  completed: { label: '完成', color: 'green' }, failed: { label: '失败', color: 'red' },
}
const ERROR_TEXT: Record<string, string> = {
  rate_limited: '触发每分钟调用上限', daily_token_budget_exceeded: '当天 token 预算已用完', process_restarted: '进程重启时中断',
}

const fmtTime = (iso: string | null) => (iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '—')

export default function StatusPage() {
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [calls, setCalls] = useState<ModelCallsResponse | null>(null)
  const [tasks, setTasks] = useState<TaskRecord[]>([])
  const [loading, setLoading] = useState(true)

  async function reload() {
    setLoading(true)
    try {
      const [s, c, t] = await Promise.all([fetchSystemStatus(), listModelCalls(50), listTasks(30)])
      setStatus(s)
      setCalls(c)
      setTasks(t.tasks)
    } catch (e: any) {
      message.error(e?.response?.data?.error ?? e.message ?? '加载失败（后端是不是还没重启到新版本？）')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { reload() }, [])

  const configuredRoles = status?.model_roles.filter((r) => r.configured).length ?? 0
  const budget = calls?.budget

  return (
    <div className="page-shell">
      <Masthead
        kicker="Exam Diagnosis · 系统状态"
        title="系统状态"
        meta={
          <>
            <span>{status ? `${status.application.name} v${status.application.version}` : ''}</span>
            <span>·</span>
            <Link to="/settings">设置</Link>
            <span>·</span>
            <Link to="/">返回诊断台</Link>
          </>
        }
      />

      {loading && !status ? <Spin /> : status && (
        <>
          <VitalsStrip>
            <Vital label="模型角色已生效" value={`${configuredRoles} / ${status.model_roles.length}`} tone={configuredRoles ? 'resolved' : 'caution'} />
            <Vital label="今日 token" value={budget ? `${budget.tokens_used_today}${budget.daily_token_budget ? ` / ${budget.daily_token_budget}` : ''}` : '—'} tone={status.budget.block_reason ? 'alert' : undefined} />
            <Vital label="后台任务 运行 / 失败" value={`${status.tasks.active} / ${status.tasks.failed}`} tone={status.tasks.failed ? 'caution' : undefined} />
            <Vital label="登录要求" value={status.auth.required ? '必须登录' : '可选'} />
          </VitalsStrip>

          {status.budget.block_reason && (
            <Alert type="error" showIcon title="模型调用当前被拦下" description={ERROR_TEXT[status.budget.block_reason] ?? status.budget.block_reason} style={{ marginBottom: 16 }} />
          )}
          {configuredRoles === 0 && (
            <Alert type="warning" showIcon title="没有任何模型角色配置了 API key" description={<span>所有环节都在走本地兜底逻辑（正则切题、关键词错因、模板验证题）。到<Link to="/settings">设置页</Link>填入对应角色的 API key 即可启用，不用重启；也可以写在 .env.local 里。</span>} style={{ marginBottom: 16 }} />
          )}

          <SectionBlock marker={configuredRoles ? 'resolved' : 'caution'} title="模型角色">
            <Card>
              <Table
                rowKey="role" size="small" pagination={false} dataSource={status.model_roles}
                columns={[
                  { title: '角色', dataIndex: 'role', render: (v: string, r: ModelRoleStatus) => <Space><Text code>{v}</Text><span>{r.purpose}</span></Space> },
                  { title: '状态', render: (_, r: ModelRoleStatus) => r.configured ? <Tag color="green">已配置</Tag> : <Tag>未配置（走兜底）</Tag> },
                  { title: '模型', dataIndex: 'model', render: (v: string | null) => v ?? <Text type="secondary">—</Text> },
                  { title: '接口', dataIndex: 'base_url', render: (v: string | null) => v ? <Text type="secondary">{v}</Text> : <Text type="secondary">—</Text> },
                  { title: '超时', dataIndex: 'timeout', width: 80, render: (v: number) => <span className="data-figure">{v}s</span> },
                  { title: '环境变量前缀', dataIndex: 'env_prefix', render: (v: string) => <Text code>{v}*</Text> },
                ]}
              />
            </Card>
          </SectionBlock>

          <SectionBlock marker="neutral" title="最近模型调用">
            <Card>
              <Flex vertical gap="middle">
                {calls && (
                  <Space wrap>
                    <Text>共 {calls.summary.total} 次，成功 {calls.summary.succeeded}，失败 {calls.summary.failed}</Text>
                    <Text type="secondary">平均延迟 <span className="data-figure">{calls.summary.avg_latency_ms}</span> ms · token 合计 <span className="data-figure">{calls.summary.total_tokens}</span></Text>
                    {budget && budget.max_calls_per_minute != null && <Text type="secondary">最近一分钟 {budget.calls_in_last_minute}/{budget.max_calls_per_minute} 次</Text>}
                  </Space>
                )}
                <Table
                  rowKey="id" size="small" dataSource={calls?.calls ?? []} pagination={{ pageSize: 15, hideOnSinglePage: true }}
                  locale={{ emptyText: '还没有任何模型调用——要么角色没配置，要么还没跑过分析' }}
                  columns={[
                    { title: '时间', dataIndex: 'created_at', width: 170, render: fmtTime },
                    { title: '角色', dataIndex: 'role', render: (v: string) => <Text code>{v}</Text> },
                    { title: '模型', dataIndex: 'model', render: (v: string | null) => v ?? '—' },
                    { title: '结果', render: (_, c: ModelCall) => c.blocked ? <Tag color="orange">拦下：{ERROR_TEXT[c.error] ?? c.error}</Tag> : c.success ? <Tag color="green">成功</Tag> : <Tag color="red">失败</Tag> },
                    { title: '延迟', dataIndex: 'latency_ms', width: 90, render: (v: number) => <span className="data-figure">{v} ms</span> },
                    { title: 'token', width: 140, render: (_, c: ModelCall) => c.total_tokens != null ? <span className="data-figure">{c.prompt_tokens ?? 0} + {c.completion_tokens ?? 0}</span> : '—' },
                    { title: '错误', dataIndex: 'error', render: (v: string, c: ModelCall) => (!c.success && !c.blocked && v) ? <Text type="danger" style={{ fontSize: 12 }}>{v}</Text> : null },
                  ]}
                />
              </Flex>
            </Card>
          </SectionBlock>

          <SectionBlock marker={status.tasks.failed ? 'caution' : 'neutral'} title={`后台任务（${status.tasks.workers} 个工作线程）`}>
            <Card>
              <Table
                rowKey="id" size="small" dataSource={tasks} pagination={{ pageSize: 10, hideOnSinglePage: true }}
                locale={{ emptyText: '还没有后台任务' }}
                columns={[
                  { title: '创建时间', dataIndex: 'created_at', width: 170, render: fmtTime },
                  { title: '类型', dataIndex: 'kind', render: (v: string) => TASK_KIND[v] ?? v },
                  { title: '状态', dataIndex: 'status', render: (v: string) => <Tag color={TASK_STATUS[v]?.color}>{TASK_STATUS[v]?.label ?? v}</Tag> },
                  { title: '完成时间', dataIndex: 'finished_at', width: 170, render: fmtTime },
                  { title: '关联', render: (_, t: TaskRecord) => t.run_id ? <Link to={`/reports/${t.run_id}`}>报告 #{t.run_id.slice(0, 8)}</Link> : t.job_id ? <Text type="secondary">任务 #{t.job_id.slice(0, 8)}</Text> : null },
                  { title: '错误', dataIndex: 'error', render: (v: string | null) => v ? <Text type="danger" style={{ fontSize: 12 }}>{ERROR_TEXT[v] ?? v}</Text> : null },
                ]}
              />
            </Card>
          </SectionBlock>

          <SectionBlock marker="neutral" title="存储与知识库">
            <Card>
              <Flex vertical gap={4}>
                <Text type="secondary">数据库：<Text code>{status.storage.database}</Text></Text>
                <Text type="secondary">上传目录：<Text code>{status.storage.uploads}</Text></Text>
                <Text type="secondary">知识库分区 {status.knowledge.partitions.length} 个（{status.knowledge.partitions.filter((p) => p.source === 'user').length} 个是你自己的）· <Link to="/knowledge">管理知识库</Link></Text>
              </Flex>
            </Card>
          </SectionBlock>

          <Button icon={<ReloadOutlined />} onClick={reload} loading={loading}>刷新</Button>
        </>
      )}
    </div>
  )
}
