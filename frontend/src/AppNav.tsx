import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { Button, Typography } from 'antd'
import { ArrowLeftOutlined } from '@ant-design/icons'
import { Badge } from 'antd'
import { AUTH_LOST_EVENT, fetchMe, fetchReviewPlan, getToken, logout, type MeResponse } from './api'

const { Text } = Typography

const NAV = [
  { to: '/', label: '诊断台' },
  { to: '/review', label: '复习' },
  { to: '/wrong-book', label: '错题本' },
  { to: '/knowledge', label: '知识库' },
  { to: '/status', label: '系统状态' },
  { to: '/settings', label: '设置' },
]

/** One navigation strip on every page. The desktop shell has no browser chrome — no back
 * button, no address bar — so every page must carry its own way out; this is it. Also hosts
 * the identity line (local user / account + 退出) and reacts to a lost session by routing to
 * /login in-app instead of reloading the window. */
export default function AppNav() {
  const location = useLocation()
  const navigate = useNavigate()
  const token = getToken()
  const [me, setMe] = useState<MeResponse | null>(null)
  // How many reviews are due, so the nav itself tells the user there's something to come back
  // for. Re-read on every navigation: practising on /review changes it immediately.
  const [dueCount, setDueCount] = useState(0)

  useEffect(() => {
    if (location.pathname === '/login') return
    fetchMe().then(setMe).catch(() => setMe(null))
    fetchReviewPlan().then((plan) => setDueCount(plan.summary.due_count)).catch(() => setDueCount(0))
  }, [location.pathname, token])

  useEffect(() => {
    const onAuthLost = (event: Event) => {
      const next = (event as CustomEvent<{ next: string }>).detail?.next ?? '/'
      if (location.pathname !== '/login') navigate(`/login?next=${encodeURIComponent(next)}`)
    }
    window.addEventListener(AUTH_LOST_EVENT, onAuthLost)
    return () => window.removeEventListener(AUTH_LOST_EVENT, onAuthLost)
  }, [location.pathname, navigate])

  if (location.pathname === '/login') return null

  const atHome = location.pathname === '/'
  const goBack = () => {
    // window.history.state.idx is react-router's own position counter: 0 means this is the
    // first page of the session, so "back" would leave the app — go home instead.
    const idx = (window.history.state as { idx?: number } | null)?.idx ?? 0
    if (idx > 0) navigate(-1)
    else navigate('/')
  }

  return (
    <nav className="app-nav">
      <div className="app-nav-left">
        {!atHome && <Button size="small" type="text" icon={<ArrowLeftOutlined />} onClick={goBack}>返回</Button>}
        <Link to="/" className="app-nav-brand">Exam Diagnosis</Link>
        {NAV.map((item) => (
          <Link key={item.to} to={item.to} className={`app-nav-link${location.pathname === item.to ? ' is-active' : ''}`}>
            {item.to === '/review' && dueCount > 0
              ? <Badge count={dueCount} size="small" offset={[8, -2]}>{item.label}</Badge>
              : item.label}
          </Link>
        ))}
      </div>
      <div className="app-nav-right">
        {me && <Text type="secondary">{me.anonymous ? '本地用户（未登录）' : me.user.display_name}</Text>}
        {me && (me.anonymous
          ? <Link to={`/login?next=${encodeURIComponent(location.pathname)}`}>登录</Link>
          : <Button size="small" onClick={() => logout().then(() => { setMe(null); navigate('/') })}>退出</Button>)}
      </div>
    </nav>
  )
}
