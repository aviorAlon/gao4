import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { isDesktopShell, resolveApiBase, waitForBackend } from './api'

const root = createRoot(document.getElementById('root')!)

function showBooting(text: string) {
  root.render(
    <div style={{ fontFamily: "'Noto Sans SC', sans-serif", color: '#63666b', padding: '48px 40px', maxWidth: 560 }}>
      <div style={{ fontSize: 12, letterSpacing: '0.18em', textTransform: 'uppercase', marginBottom: 12 }}>Exam Diagnosis</div>
      <div style={{ fontSize: 16, color: '#1c1e21' }}>{text}</div>
    </div>,
  )
}

async function bootstrap() {
  // In the desktop shell the backend is a child process the shell just spawned: ask it where
  // the API is, then confirm the API answers before showing anything that would call it.
  if (isDesktopShell()) {
    showBooting('正在启动本地服务…')
    try {
      await resolveApiBase()
    } catch (e) {
      showBooting(`无法启动本地服务：${e instanceof Error ? e.message : String(e)}`)
      return
    }
    const ready = await waitForBackend(30000, (attempt) => showBooting(`正在启动本地服务…（${attempt}）`))
    if (!ready) {
      showBooting('本地服务没有响应。请关闭应用后重新打开；如果反复出现，把系统状态页的信息反馈给我们。')
      return
    }
  }
  root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}

bootstrap()
