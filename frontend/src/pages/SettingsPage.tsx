import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert, Button, Card, Flex, Input, InputNumber, Space, Spin, Tag, Typography } from 'antd'
import { message } from '../feedback'
import { SaveOutlined } from '@ant-design/icons'
import { fetchSettings, updateSettings, type RoleSettings, type SettingsResponse, type SettingsUpdate } from '../api'
import { Masthead, SectionBlock } from '../chart/Chart'
import '../chart/chart.css'

const { Text } = Typography

type RoleDraft = { api_key?: string; clear_key?: boolean; base_url?: string; model?: string; timeout?: string }

function SourceTag({ source }: { source: 'env' | 'config' | null }) {
  if (source === 'env') return <Tag color="blue">环境变量</Tag>
  if (source === 'config') return <Tag color="green">设置页</Tag>
  return null
}

function RoleCard({ role, draft, onChange, writable }: {
  role: RoleSettings
  draft: RoleDraft
  onChange: (next: RoleDraft) => void
  writable: boolean
}) {
  const disabled = !writable || role.locked
  const keyPlaceholder = draft.clear_key ? '将清除' : role.api_key_set ? '已设置（留空不改）' : '未设置——填入 API key 即启用'
  return (
    <Card size="small" type="inner" title={
      <Space>
        <Text code>{role.role}</Text>
        <span>{role.purpose}</span>
        {role.configured ? <Tag color="green">已生效</Tag> : <Tag>未启用（走兜底）</Tag>}
        {role.locked && <Tag color="blue">由环境变量设定，此处不可改</Tag>}
      </Space>
    }>
      <Flex vertical gap="small">
        <Flex align="center" gap="small" wrap>
          <Text style={{ width: 72 }}>API key</Text>
          <Input.Password
            style={{ maxWidth: 420 }} disabled={disabled} placeholder={keyPlaceholder} autoComplete="new-password"
            value={draft.api_key ?? ''} onChange={(e) => onChange({ ...draft, api_key: e.target.value, clear_key: false })}
          />
          <SourceTag source={role.api_key_source} />
          {role.api_key_set && !disabled && (
            <Button size="small" danger={!draft.clear_key} onClick={() => onChange({ ...draft, api_key: '', clear_key: !draft.clear_key })}>
              {draft.clear_key ? '取消清除' : '清除 key'}
            </Button>
          )}
        </Flex>
        <Flex align="center" gap="small" wrap>
          <Text style={{ width: 72 }}>模型</Text>
          <Input style={{ maxWidth: 260 }} disabled={disabled} placeholder={role.model_default ?? '（必填）'} value={draft.model ?? role.model ?? ''} onChange={(e) => onChange({ ...draft, model: e.target.value })} />
          <SourceTag source={role.model_source} />
          <Text style={{ width: 72, marginLeft: 12 }}>超时(秒)</Text>
          <Input style={{ width: 90 }} disabled={disabled} placeholder={String(role.timeout_default)} value={draft.timeout ?? (role.timeout != null ? String(role.timeout) : '')} onChange={(e) => onChange({ ...draft, timeout: e.target.value })} />
        </Flex>
        <Flex align="center" gap="small" wrap>
          <Text style={{ width: 72 }}>接口地址</Text>
          <Input style={{ maxWidth: 420 }} disabled={disabled} placeholder={role.base_url_default ?? 'OpenAI-compatible 的 base URL（必填）'} value={draft.base_url ?? role.base_url ?? ''} onChange={(e) => onChange({ ...draft, base_url: e.target.value })} />
          <SourceTag source={role.base_url_source} />
        </Flex>
      </Flex>
    </Card>
  )
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsResponse | null>(null)
  const [drafts, setDrafts] = useState<Record<string, RoleDraft>>({})
  const [budgetDraft, setBudgetDraft] = useState<{ daily_token_budget?: number | null; max_calls_per_minute?: number | null }>({})
  const [saving, setSaving] = useState(false)

  async function reload() {
    try {
      setSettings(await fetchSettings())
      setDrafts({})
      setBudgetDraft({})
    } catch (e: any) {
      message.error(e?.response?.data?.error ?? e.message ?? '加载失败（后端是不是还没重启到新版本？）')
    }
  }

  useEffect(() => { reload() }, [])

  function buildUpdate(): SettingsUpdate {
    const update: SettingsUpdate = { roles: {}, budget: {} }
    for (const [role, draft] of Object.entries(drafts)) {
      const fields: NonNullable<SettingsUpdate['roles']>[string] = {}
      if (draft.clear_key) fields.api_key = ''
      else if (draft.api_key) fields.api_key = draft.api_key
      if (draft.model !== undefined) fields.model = draft.model.trim() || null
      if (draft.base_url !== undefined) fields.base_url = draft.base_url.trim() || null
      if (draft.timeout !== undefined) fields.timeout = draft.timeout.trim() || null
      if (Object.keys(fields).length) update.roles![role] = fields
    }
    update.budget = budgetDraft
    return update
  }

  async function save() {
    setSaving(true)
    try {
      const saved = await updateSettings(buildUpdate())
      setSettings(saved)
      setDrafts({})
      setBudgetDraft({})
      message.success('已保存并立即生效，不需要重启')
    } catch (e: any) {
      const data = e?.response?.data
      message.error(
        data?.error === 'locked_by_environment' ? `这些值由环境变量设定，改不了：${data.fields.join('、')}`
        : data?.error === 'invalid_settings' ? `设置不合法：${data.detail}`
        : data?.error === 'settings_locked_in_multi_user_mode' ? '多用户模式下设置只能由服务器管理员通过环境变量修改'
        : data?.error ?? e.message ?? '保存失败',
      )
    } finally {
      setSaving(false)
    }
  }

  const dirty = Object.keys(drafts).length > 0 || Object.keys(budgetDraft).length > 0

  return (
    <div className="page-shell">
      <Masthead
        kicker="Exam Diagnosis · 设置"
        title="设置"
        meta={
          <>
            <span>模型 key 和预算保存在本机 {settings?.config_path ? <Text code>{settings.config_path}</Text> : '配置文件'}</span>
            <span>·</span>
            <Link to="/status">系统状态</Link>
            <span>·</span>
            <Link to="/">返回诊断台</Link>
          </>
        }
      />

      {!settings ? <Spin /> : (
        <>
          {!settings.writable && (
            <Alert type="info" showIcon style={{ marginBottom: 16 }} title="当前是多用户模式，设置为只读" description="服务器范围的模型配置由部署它的人通过环境变量 / .env.local 管理，不由登录用户修改。" />
          )}
          <Alert
            type="warning" showIcon style={{ marginBottom: 16 }}
            title="key 只会保存在本机，不会出现在任何接口返回里"
            description="填一个角色的 API key 就启用那个角色；模型和接口地址留空用 DeepSeek 默认值。填错了会在系统状态页看到调用失败，不会影响本地兜底流程。"
          />

          <SectionBlock marker={settings.roles.some((r) => r.configured) ? 'resolved' : 'caution'} title="模型角色">
            <Flex vertical gap="middle">
              {settings.roles.map((role) => (
                <RoleCard key={role.role} role={role} writable={settings.writable} draft={drafts[role.role] ?? {}} onChange={(next) => setDrafts((d) => ({ ...d, [role.role]: next }))} />
              ))}
            </Flex>
          </SectionBlock>

          <SectionBlock marker="neutral" title="成本控制">
            <Card>
              <Flex gap="large" wrap align="center">
                <Space>
                  <Text>每日 token 预算</Text>
                  <InputNumber min={0} style={{ width: 140 }} placeholder="不限" disabled={!settings.writable || settings.budget.daily_token_budget_source === 'env'}
                    value={budgetDraft.daily_token_budget !== undefined ? budgetDraft.daily_token_budget : settings.budget.daily_token_budget}
                    onChange={(v) => setBudgetDraft((b) => ({ ...b, daily_token_budget: v ?? null }))} />
                  <SourceTag source={settings.budget.daily_token_budget_source} />
                </Space>
                <Space>
                  <Text>每分钟最多调用</Text>
                  <InputNumber min={0} style={{ width: 120 }} placeholder="不限" disabled={!settings.writable || settings.budget.max_calls_per_minute_source === 'env'}
                    value={budgetDraft.max_calls_per_minute !== undefined ? budgetDraft.max_calls_per_minute : settings.budget.max_calls_per_minute}
                    onChange={(v) => setBudgetDraft((b) => ({ ...b, max_calls_per_minute: v ?? null }))} />
                  <SourceTag source={settings.budget.max_calls_per_minute_source} />
                </Space>
                <Text type="secondary">0 或留空 = 不限制；超限的调用会被拦下并记录在系统状态页。</Text>
              </Flex>
            </Card>
          </SectionBlock>

          <Space>
            <Button type="primary" icon={<SaveOutlined />} onClick={save} loading={saving} disabled={!settings.writable || !dirty}>保存</Button>
            <Button onClick={reload} disabled={!dirty}>放弃修改</Button>
          </Space>
        </>
      )}
    </div>
  )
}
