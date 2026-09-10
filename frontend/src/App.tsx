import { Suspense, lazy } from 'react'
import { ConfigProvider, Spin } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import AppNav from './AppNav'

// Each page is its own chunk, loaded on first visit — the report page alone pulls in a fair
// amount of antd that the home page never needs.
const HomePage = lazy(() => import('./pages/HomePage'))
const SubmissionPage = lazy(() => import('./pages/SubmissionPage'))
const ExamPage = lazy(() => import('./pages/ExamPage'))
const ReportPage = lazy(() => import('./pages/ReportPage'))
const LoginPage = lazy(() => import('./pages/LoginPage'))
const KnowledgePage = lazy(() => import('./pages/KnowledgePage'))
const StatusPage = lazy(() => import('./pages/StatusPage'))
const ReviewPage = lazy(() => import('./pages/ReviewPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))

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
        <AppNav />
        <Suspense fallback={<div className="page-shell"><Spin /></div>}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/knowledge" element={<KnowledgePage />} />
            <Route path="/status" element={<StatusPage />} />
            <Route path="/review" element={<ReviewPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/" element={<HomePage />} />
            <Route path="/submissions/:id" element={<SubmissionPage />} />
            <Route path="/exams/:id" element={<ExamPage />} />
            <Route path="/reports/:runId" element={<ReportPage />} />
            <Route path="/reports" element={<ReportPage />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </ConfigProvider>
  )
}
