import { useEffect, useState } from 'react'
import { Button, ConfigProvider, Typography } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom'
import HomePage from './pages/HomePage'
import SubmissionPage from './pages/SubmissionPage'
import ExamPage from './pages/ExamPage'
import ReportPage from './pages/ReportPage'
import LoginPage from './pages/LoginPage'
import { fetchMe, getToken, logout, type MeResponse } from './api'

/** Who the backend thinks we are, in the page's top-right corner. In the default local setup this
 * says "本地用户" and offers a login link; with a session it names the account and offers logout. */
function AuthBar() {
  const location = useLocation()
  const token = getToken()
  const [me, setMe] = useState<MeResponse | null>(null)

  useEffect(() => {
    if (location.pathname === '/login') return
    fetchMe().then(setMe).catch(() => setMe(null))
  }, [location.pathname, token])

  if (location.pathname === '/login' || !me) return null
  return (
    <div style={{ position: 'absolute', top: 12, right: 20, display: 'flex', gap: 12, alignItems: 'center' }}>
      <Typography.Text type="secondary">{me.anonymous ? '本地用户（未登录）' : me.user.display_name}</Typography.Text>
      {me.anonymous
        ? <Link to="/login">登录</Link>
        : <Button size="small" onClick={() => logout().then(() => window.location.assign('/'))}>退出</Button>}
    </div>
  )
}

const theme = {
  token: {
    colorPrimary: '#c7391f',
    colorSuccess: '#1f6f5c',
    colorWarning: '#b9791f',
    colorError: '#c7391f',
    // AntD's light-theme algorithm derives each color's "Bg"/"Border" shade from the seed by
    // formula, and it doesn't handle these darker, fairly saturated seeds well — colorSuccess
    // (#1f6f5c) in particular came out as a muddy gray-green background instead of a pale mint.
    // Setting the derived shades by hand for every semantic color, rather than trusting the
    // algorithm on some and not others.
    colorSuccessBg: '#e7f2ee',
    colorSuccessBorder: '#bcdad0',
    colorWarningBg: '#faf6eb',
    colorWarningBorder: '#e8d3a3',
    colorErrorBg: '#fbeae6',
    colorErrorBorder: '#eab8ac',
    // Left at a warm paper-toned neutral rather than AntD's default blue — a diagnostic report's
    // "info" flags should read as a note on the same page, not a UI-chrome accent color.
    colorInfo: '#8a7d5c',
    colorInfoBg: '#f2f0e6',
    colorInfoBorder: '#ded9cb',
    colorText: '#1c1e21',
    colorTextSecondary: '#63666b',
    colorBgBase: '#faf9f5',
    colorBgContainer: '#ffffff',
    colorBorder: '#ded9cb',
    colorBorderSecondary: '#e8e4d8',
    fontFamily: "'Noto Sans SC', 'PingFang SC', sans-serif",
    borderRadius: 2,
    borderRadiusLG: 2,
    borderRadiusSM: 2,
  },
  components: {
    Card: { colorBgContainer: '#ffffff' },
    Typography: { fontFamilyCode: "'IBM Plex Mono', monospace" },
  },
}

export default function App() {
  return (
    <ConfigProvider locale={zhCN} theme={theme}>
      <BrowserRouter>
        <AuthBar />
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<HomePage />} />
          <Route path="/submissions/:id" element={<SubmissionPage />} />
          <Route path="/exams/:id" element={<ExamPage />} />
          <Route path="/reports/:runId" element={<ReportPage />} />
          <Route path="/reports" element={<ReportPage />} />
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  )
}
