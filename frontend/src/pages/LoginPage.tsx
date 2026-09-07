import { useEffect, useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { Alert, Button, Card, Flex, Form, Input, Segmented, Space, Typography } from 'antd'
import { ArrowLeftOutlined } from '@ant-design/icons'
import { fetchHealth, login, register } from '../api'
import { Masthead } from '../chart/Chart'
import '../chart/chart.css'

const { Text } = Typography

const ERROR_TEXT: Record<string, string> = {
  invalid_credentials: '用户名或密码不对',
  too_many_attempts: '失败次数太多，请过一刻钟再试',
  username_taken: '这个用户名已经有人用了',
  invalid_username: '用户名需要 3-32 位，只能用小写字母、数字和 _ . -',
  password_too_short: '密码至少 8 位',
}

export default function LoginPage() {
  const navigate = useNavigate()
  const next = new URLSearchParams(window.location.search).get('next') || '/'
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // null until the backend has answered — don't offer "continue without logging in" on a guess.
  const [authRequired, setAuthRequired] = useState<boolean | null>(null)

  useEffect(() => {
    fetchHealth().then((h) => setAuthRequired(h.auth_required)).catch(() => setAuthRequired(null))
  }, [])

  const target = next.startsWith('/') ? next : '/'

  async function submit(values: { username: string; password: string; display_name?: string }) {
    setBusy(true)
    setError(null)
    try {
      if (mode === 'login') await login(values.username, values.password)
      else await register(values.username, values.password, values.display_name)
      navigate(target, { replace: true })
    } catch (e: any) {
      const code = e?.response?.data?.error ?? e?.response?.data?.detail?.error
      setError(ERROR_TEXT[code] ?? code ?? e.message ?? '请求失败')
    } finally {
      setBusy(false)
    }
  }

  const optional = authRequired === false

  return (
    <div className="page-shell" style={{ maxWidth: 480 }}>
      {optional && (
        <Link to={target} style={{ display: 'inline-block', marginBottom: 12 }}>
          <ArrowLeftOutlined /> 返回，不登录
        </Link>
      )}
      <Masthead
        kicker="Exam Diagnosis" caseId="" animate={false}
        title={mode === 'login' ? '登录' : '注册账号'}
        meta={<span>{authRequired ? '这个服务要求登录后使用' : '每个账号只看得到自己的考试和报告'}</span>}
      />
      <Card>
        <Flex vertical gap="middle">
          <Segmented
            block value={mode}
            options={[{ label: '登录', value: 'login' }, { label: '注册', value: 'register' }]}
            onChange={(v) => { setMode(v as 'login' | 'register'); setError(null) }}
          />
          {error && <Alert type="error" showIcon title={error} />}
          <Form layout="vertical" onFinish={submit} disabled={busy}>
            <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
              <Input autoComplete="username" autoFocus />
            </Form.Item>
            {mode === 'register' && (
              <Form.Item name="display_name" label="显示名（可选）">
                <Input placeholder="比如学生姓名" />
              </Form.Item>
            )}
            <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }, { min: mode === 'register' ? 8 : 1, message: '密码至少 8 位' }]}>
              <Input.Password autoComplete={mode === 'login' ? 'current-password' : 'new-password'} />
            </Form.Item>
            <Space orientation="vertical" style={{ width: "100%" }} size="small">
              <Button type="primary" htmlType="submit" loading={busy} block>
                {mode === 'login' ? '登录' : '注册并登录'}
              </Button>
              {optional && (
                <Button block onClick={() => navigate(target)}>
                  不登录，直接使用
                </Button>
              )}
            </Space>
          </Form>
          {optional && (
            <Text type="secondary">
              登录是可选的：不登录时数据归"本地用户"，同一台机器上谁都能看；注册账号后各自只看到自己的考试和报告。
            </Text>
          )}
        </Flex>
      </Card>
    </div>
  )
}
