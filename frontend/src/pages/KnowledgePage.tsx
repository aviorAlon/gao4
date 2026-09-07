import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Alert, Button, Card, Divider, Empty, Flex, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Spin, Table, Tag, Typography, Upload, message,
} from 'antd'
import { DeleteOutlined, DownloadOutlined, PlusOutlined, UploadOutlined } from '@ant-design/icons'
import {
  createKnowledgeBase, deleteKnowledgeBase, deleteKnowledgePoint, downloadKnowledgeBase, importKnowledgeBase,
  listKnowledgeBases, listKnowledgePartitions, listPartitionPoints, upsertKnowledgePoint,
  type KnowledgeBase, type KnowledgePartition, type KnowledgePoint, type KnowledgeScope,
} from '../api'
import { Masthead, SectionBlock } from '../chart/Chart'
import '../chart/chart.css'

const { Text } = Typography
const STAGE_LABEL: Record<string, string> = { primary: '小学', middle: '初中', high: '高中' }

const scopeLabel = (p: KnowledgeScope) => `${STAGE_LABEL[p.stage] ?? p.stage} / ${p.subject} / ${p.province} · 课标${p.curriculum_year}`
const sameScope = (a: KnowledgeScope, b: KnowledgeScope) =>
  a.stage === b.stage && a.subject === b.subject && a.province === b.province && a.curriculum_year === b.curriculum_year

const ERROR_TEXT: Record<string, string> = {
  knowledge_base_exists_for_scope: '这个阶段/科目/省份已经有一个你自己的知识库了',
  public_partition_not_found: '没有对应的公共知识库可以扩展',
  invalid_knowledge_base: '知识库内容不合法',
  invalid_yaml: 'YAML 文件无法解析',
}

function errorText(e: any) {
  const data = e?.response?.data
  const code = data?.error ?? data?.detail?.error
  const base = ERROR_TEXT[code] ?? code ?? e?.message ?? '请求失败'
  return data?.detail && typeof data.detail === 'string' ? `${base}：${data.detail}` : base
}

/** Points table shared by the public view and the user's own base; `editable` adds delete. */
function PointsTable({ points, onDelete }: { points: KnowledgePoint[]; onDelete?: (id: string) => void }) {
  return (
    <Table
      rowKey="knowledge_id" size="small" dataSource={points} pagination={{ pageSize: 20, hideOnSinglePage: true }}
      columns={[
        { title: '知识点', dataIndex: 'name', render: (name: string, p: KnowledgePoint) => (
          <Space size={6}>
            <span>{name}</span>
            {p.origin === 'user' && <Tag color="volcano">自定义</Tag>}
          </Space>
        ) },
        { title: 'ID', dataIndex: 'knowledge_id', render: (v: string) => <Text code>{v}</Text> },
        { title: '章节', dataIndex: 'chapter' },
        { title: '年级', dataIndex: 'grade_scope', width: 70 },
        { title: '关键词', dataIndex: 'keywords', render: (ks: string[]) => ks.map((k) => <Tag key={k}>{k}</Tag>) },
        { title: '置信度', dataIndex: 'confidence', width: 80, render: (v: number) => <span className="data-figure">{v}</span> },
        ...(onDelete ? [{
          title: '', width: 48,
          render: (_: unknown, p: KnowledgePoint) => p.origin === 'user' ? (
            <Popconfirm title="删除这个知识点？" onConfirm={() => onDelete(p.knowledge_id)}>
              <Button danger type="text" size="small" icon={<DeleteOutlined />} />
            </Popconfirm>
          ) : null,
        }] : []),
      ]}
    />
  )
}

function PointForm({ open, base, effectivePoints, onClose, onSaved }: {
  open: boolean
  base: KnowledgeBase
  effectivePoints: KnowledgePoint[]
  onClose: () => void
  onSaved: (kb: KnowledgeBase) => void
}) {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const grades = base.resolved?.academic_year_range ?? base.academic_year_range

  async function submit(values: any) {
    setSaving(true)
    try {
      const saved = await upsertKnowledgePoint(base.id, {
        knowledge_id: values.knowledge_id.trim(),
        name: values.name.trim(),
        chapter: values.chapter.trim(),
        keywords: String(values.keywords ?? '').split(/[,，\s]+/).map((k: string) => k.trim()).filter(Boolean),
        confidence: values.confidence ?? 0.8,
        parent: null,
        dependencies: values.dependencies ?? [],
        grade_scope: values.grade_scope,
        textbook_chapter: values.textbook_chapter?.trim() ?? '',
      })
      message.success('知识点已保存')
      form.resetFields()
      onSaved(saved)
    } catch (e) {
      message.error(errorText(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open={open} title="添加 / 覆盖知识点" onCancel={onClose} onOk={() => form.submit()} confirmLoading={saving} okText="保存" cancelText="取消" destroyOnHidden>
      <Alert type="info" showIcon style={{ marginBottom: 12 }} title="ID 与公共库里的某个知识点相同时，会用你的定义覆盖它（比如补充关键词）。" />
      <Form form={form} layout="vertical" onFinish={submit} initialValues={{ confidence: 0.8, grade_scope: grades[0] }}>
        <Form.Item name="knowledge_id" label="知识点 ID" rules={[{ required: true, message: '例如 user.math.polar' }]}>
          <Input placeholder="user.math.polar" />
        </Form.Item>
        <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="chapter" label="章节" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="keywords" label="关键词（逗号分隔，题干里出现即命中）"><Input placeholder="极坐标, 参数方程" /></Form.Item>
        <Space size="large">
          <Form.Item name="grade_scope" label="年级" rules={[{ required: true }]}>
            <Select style={{ width: 120 }} options={grades.map((g) => ({ value: g, label: g }))} />
          </Form.Item>
          <Form.Item name="confidence" label="置信度"><InputNumber min={0} max={1} step={0.05} /></Form.Item>
        </Space>
        <Form.Item name="dependencies" label="前置知识点">
          <Select mode="multiple" allowClear options={effectivePoints.map((p) => ({ value: p.knowledge_id, label: `${p.name}（${p.knowledge_id}）` }))} />
        </Form.Item>
        <Form.Item name="textbook_chapter" label="教材章节"><Input placeholder="选择性必修 第一册 第二章" /></Form.Item>
      </Form>
    </Modal>
  )
}

export default function KnowledgePage() {
  const [partitions, setPartitions] = useState<KnowledgePartition[]>([])
  const [bases, setBases] = useState<KnowledgeBase[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedScope, setSelectedScope] = useState<KnowledgeScope | null>(null)
  const [scopePoints, setScopePoints] = useState<KnowledgePoint[]>([])
  const [scopePartition, setScopePartition] = useState<KnowledgePartition | null>(null)
  const [pointsLoading, setPointsLoading] = useState(false)
  const [creatingFor, setCreatingFor] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)

  async function reload() {
    setLoading(true)
    try {
      const [p, b] = await Promise.all([listKnowledgePartitions(), listKnowledgeBases()])
      setPartitions(p)
      setBases(b)
      if (!selectedScope && p.length) setSelectedScope(p[0])
    } catch (e) {
      message.error(errorText(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { reload() }, [])

  // The partition list is "as this user sees it" — creating or deleting a user base flips a
  // row between 公共 and 我的, so it is re-fetched whenever the set of bases changes.
  useEffect(() => {
    if (loading) return
    listKnowledgePartitions().then(setPartitions).catch(() => undefined)
  }, [bases])

  useEffect(() => {
    if (!selectedScope) return
    setPointsLoading(true)
    listPartitionPoints(selectedScope)
      .then(({ partition, points }) => { setScopePartition(partition); setScopePoints(points) })
      .catch((e) => message.error(errorText(e)))
      .finally(() => setPointsLoading(false))
  }, [selectedScope, bases])

  const selectedBase = useMemo(
    () => (selectedScope ? bases.find((b) => sameScope(b, selectedScope)) ?? null : null),
    [bases, selectedScope],
  )

  async function createFor(p: KnowledgePartition) {
    setCreatingFor(scopeLabel(p))
    try {
      const created = await createKnowledgeBase({ stage: p.stage, subject: p.subject, province: p.province, curriculum_year: p.curriculum_year, extends_public: true, name: `我的${p.subject}知识库` })
      message.success('已创建，可以开始添加知识点')
      setBases((prev) => [...prev, created])
      setSelectedScope(p)
    } catch (e) {
      message.error(errorText(e))
    } finally {
      setCreatingFor(null)
    }
  }

  async function removeBase(kb: KnowledgeBase) {
    await deleteKnowledgeBase(kb.id)
    message.success('已删除，恢复为公共知识库')
    setBases((prev) => prev.filter((b) => b.id !== kb.id))
  }

  async function removePoint(kb: KnowledgeBase, knowledgeId: string) {
    try {
      const updated = await deleteKnowledgePoint(kb.id, knowledgeId)
      setBases((prev) => prev.map((b) => (b.id === updated.id ? updated : b)))
    } catch (e) {
      message.error(errorText(e))
    }
  }

  async function onImport(file: File) {
    try {
      const created = await importKnowledgeBase(file)
      message.success(`已导入「${created.name || scopeLabel(created)}」`)
      setBases((prev) => [...prev, created])
      setSelectedScope(created)
    } catch (e) {
      message.error(errorText(e))
    }
    return false
  }

  return (
    <div className="page-shell">
      <Masthead
        kicker="Exam Diagnosis · 知识库"
        title="知识库"
        meta={<span>产品自带公共知识库；你可以在它之上补充或覆盖自己的知识点，也可以为产品没覆盖的科目建一个自己的库。<Link to="/">返回诊断台</Link></span>}
      />

      <SectionBlock marker="neutral" title="知识库分区">
        <Card>
          {loading ? <Spin /> : (
            <Table
              rowKey={(p) => `${p.stage}|${p.subject}|${p.province}|${p.curriculum_year}`}
              dataSource={partitions} pagination={false} size="small"
              rowClassName={(p) => (selectedScope && sameScope(p, selectedScope) ? 'ant-table-row-selected' : '')}
              onRow={(p) => ({ onClick: () => setSelectedScope(p), style: { cursor: 'pointer' } })}
              columns={[
                { title: '阶段 / 科目 / 省份', render: (_, p: KnowledgePartition) => scopeLabel(p) },
                { title: '来源', render: (_, p: KnowledgePartition) => p.source === 'user' ? <Tag color="volcano">我的（{p.extends_public ? '扩展公共库' : '独立'}）</Tag> : <Tag>公共</Tag> },
                { title: '版本', dataIndex: 'version' },
                { title: '知识点数', render: (_, p: KnowledgePartition) => <span className="data-figure">{p.knowledge_point_count}{p.source === 'user' ? `（自定义 ${p.user_point_count}）` : ''}</span> },
                { title: '年级范围', render: (_, p: KnowledgePartition) => p.academic_year_range.join('、') },
                {
                  title: '', width: 160,
                  render: (_, p: KnowledgePartition) => p.source === 'public'
                    ? <Button size="small" icon={<PlusOutlined />} loading={creatingFor === scopeLabel(p)} onClick={(e) => { e.stopPropagation(); createFor(p) }}>在此之上建我的库</Button>
                    : null,
                },
              ]}
            />
          )}
          <Divider />
          <Space>
            <Upload accept=".yaml,.yml" showUploadList={false} beforeUpload={onImport}>
              <Button icon={<UploadOutlined />}>导入 YAML 知识库</Button>
            </Upload>
            <Text type="secondary">与 data/knowledge/*.yaml 同一格式；加 <Text code>extends_public: true</Text> 表示在公共库之上扩展。</Text>
          </Space>
        </Card>
      </SectionBlock>

      {selectedScope && (
        <SectionBlock marker={selectedBase ? 'caution' : 'neutral'} title={`${scopeLabel(selectedScope)} 的知识点`}>
          <Card>
            {selectedBase ? (
              <Flex vertical gap="middle">
                <Flex justify="space-between" align="center" wrap>
                  <Space>
                    <Text strong>{selectedBase.name || '我的知识库'}</Text>
                    <Tag color="volcano">{selectedBase.extends_public ? `扩展公共库 v${selectedBase.resolved?.base_version ?? ''}` : '独立知识库'}</Tag>
                    <Text type="secondary">自定义 {selectedBase.knowledge_points.length} 个知识点</Text>
                  </Space>
                  <Space>
                    <Button type="primary" icon={<PlusOutlined />} onClick={() => setAdding(true)}>添加知识点</Button>
                    <Button icon={<DownloadOutlined />} onClick={() => downloadKnowledgeBase(selectedBase.id).catch((e) => message.error(errorText(e)))}>导出 YAML</Button>
                    <Popconfirm title="删除整个自定义知识库？将恢复为公共知识库。" onConfirm={() => removeBase(selectedBase)}>
                      <Button danger icon={<DeleteOutlined />}>删除我的库</Button>
                    </Popconfirm>
                  </Space>
                </Flex>
                {selectedBase.problem && <Alert type="error" showIcon title="这个知识库当前无法生效" description={selectedBase.problem} />}
                {pointsLoading ? <Spin /> : <PointsTable points={scopePoints} onDelete={(id) => removePoint(selectedBase, id)} />}
                <PointForm open={adding} base={selectedBase} effectivePoints={scopePoints} onClose={() => setAdding(false)}
                  onSaved={(kb) => { setAdding(false); setBases((prev) => prev.map((b) => (b.id === kb.id ? kb : b))) }} />
              </Flex>
            ) : (
              <Flex vertical gap="middle">
                <Text type="secondary">
                  这是公共知识库{scopePartition ? ` v${scopePartition.version}` : ''}，不能直接修改；点上方"在此之上建我的库"后即可补充、覆盖知识点。
                </Text>
                {pointsLoading ? <Spin /> : scopePoints.length ? <PointsTable points={scopePoints} /> : <Empty description="暂无知识点" />}
              </Flex>
            )}
          </Card>
        </SectionBlock>
      )}
    </div>
  )
}
