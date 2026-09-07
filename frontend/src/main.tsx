import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { API_BASE, isDesktopShell, resolveApiBase, waitForBackend } from './api'

const root = createRoot(document.getElementById('root')!)

const shellStyle = { fontFamily: "'Noto Sans SC', sans-serif", color: '#63666b', padding: '48px 40px', maxWidth: 620 } as const

function showBooting(text: string, detail?: string, retry?: () => void) {
  root.render(
    <div style={shellStyle}>
      <div style={{ fontSize: 12, letterSpacing: '0.18em', textTransform: 'uppercase', marginBottom: 12 }}>Exam Diagnosis</div>
      <div style={{ fontSize: 16, color: '#1c1e21' }}>{text}</div>
      {detail && <pre style={{ marginTop: 16, fontSize: 12, whiteSpace: 'pre-wrap', background: '#f2f0e6', padding: 12, border: '1px solid #ded9cb' }}>{detail}</pre>}
      {retry && (
        <button onClick={retry} style={{ marginTop: 16, padding: '6px 18px', border: '1px solid #c7391f', background: '#c7391f', color: '#fff', cursor: 'pointer' }}>重试</button>
      )}
    </div>,
  )
}

async function bootstrap() {
  // In the desktop shell the backend is a child process the shell just spawned: ask it where
  // the API is, then confirm the API answers before showing anything that would call it.
  if (isDesktopShell()) {
    showBooting('正在启动本地服务…')
    let base = ''
    try {
      base = await resolveApiBase()
    } catch (e) {
      showBooting('无法从桌面壳获取本地服务地址', String(e instanceof Error ? e.message : e), bootstrap)
      return
    }
    let lastError = ''
    const ready = await waitForBackend(60000, (attempt, error) => {
      lastError = error
      showBooting(`正在启动本地服务…（第 ${attempt} 次探测）`, `地址：${base || API_BASE}\n首次启动时杀毒软件扫描可能需要更久。`)
    })
    if (!ready) {
      showBooting(
        '本地服务没有响应',
        `地址：${base || API_BASE}\n最后一次错误：${lastError || '未知'}\n\n` +
          (base ? '后端进程已启动但页面连不上——请把这段信息反馈给我们。' : '桌面壳没有找到内置后端（api_base 为空），当前尝试的是默认地址；如果你是开发者，请先运行后端或 scripts/build_backend.py。'),
        bootstrap,
      )
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
