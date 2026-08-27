import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import HomePage from './pages/HomePage'
import SubmissionPage from './pages/SubmissionPage'
import ExamPage from './pages/ExamPage'
import ReportPage from './pages/ReportPage'

export default function App() {
  return (
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#1677ff' } }}>
      <BrowserRouter>
        <Routes>
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
