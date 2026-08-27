import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import ReportPage from './pages/ReportPage'

export default function App() {
  return (
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#1677ff' } }}>
      <ReportPage />
    </ConfigProvider>
  )
}
