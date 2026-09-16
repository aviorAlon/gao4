import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Flex, Select, Space, Tag, Typography } from 'antd'
import { message } from '../feedback'
import { MobileOutlined, StopOutlined } from '@ant-design/icons'
import { getMobileUploadStatus, startMobileUpload, stopMobileUpload, type MobileUploadSession } from '../api'

const { Text, Paragraph } = Typography

const POLL_MS = 2500

function countdown(expiresAt: string): string {
  const seconds = Math.max(0, Math.round((new Date(expiresAt).getTime() - Date.now()) / 1000))
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`
}

/** Desktop side of phone upload: show a QR, watch files arrive, close the channel when done.
 *
 * The address the phone should use isn't knowable for certain — a machine usually has several
 * interfaces (Wi-Fi, Ethernet, VPN, virtual adapters) and only one of them is on the phone's
 * network. So all candidates are offered and the user can switch rather than being stuck with a
 * silently wrong guess. */
export default function MobileUploadPanel({ examId, onUploaded }: { examId: string; onUploaded: () => void }) {
  const [session, setSession] = useState<MobileUploadSession | null>(null)
  const [urlIndex, setUrlIndex] = useState(0)
  const [busy, setBusy] = useState(false)
  const [uploadCount, setUploadCount] = useState(0)
  const [remaining, setRemaining] = useState('')
  // A channel open on the server that this page can't show a QR for, because the token is only
  // ever handed out at creation (see the backend: only its hash is stored). Happens when the page
  // was reloaded or navigated away from mid-session.
  const [orphaned, setOrphaned] = useState(false)
  const lastCount = useRef(0)

  useEffect(() => {
    getMobileUploadStatus(examId)
      .then((status) => setOrphaned(status.open))
      .catch(() => setOrphaned(false))
  }, [examId])

  // Poll while the channel is open: files arriving on the phone should show up here without the
  // user having to do anything.
  useEffect(() => {
    if (!session) return
    const timer = setInterval(async () => {
      try {
        const status = await getMobileUploadStatus(examId)
        if (!status.open) { setSession(null); return }
        setUploadCount(status.upload_count)
        if (status.upload_count !== lastCount.current) {
          lastCount.current = status.upload_count
          onUploaded()
        }
      } catch { /* transient; the next tick will retry */ }
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [session, examId])

  useEffect(() => {
    if (!session) return
    const tick = () => setRemaining(countdown(session.expires_at))
    tick()
    const timer = setInterval(tick, 1000)
    return () => clearInterval(timer)
  }, [session])

  async function start() {
    setBusy(true)
    try {
      // Close any orphaned channel first so there's exactly one live token per exam.
      if (orphaned) await stopMobileUpload(examId).catch(() => {})
      const started = await startMobileUpload(examId)
      setOrphaned(false)
      setSession(started)
      setUrlIndex(0)
      setUploadCount(0)
      lastCount.current = 0
    } catch (e: any) {
      const data = e?.response?.data
      message.error(data?.error === 'lan_listener_unavailable'
        ? `无法开启局域网通道：${data.detail ?? '未知原因'}`
        : data?.error === 'exam_confirmed' ? '这场考试已确认，先「重新打开」再添加资料'
        : data?.error ?? e.message ?? '开启失败')
    } finally {
      setBusy(false)
    }
  }

  async function stop() {
    setBusy(true)
    try {
      await stopMobileUpload(examId)
      setSession(null)
      setOrphaned(false)
      onUploaded()
    } catch (e: any) {
      message.error(e?.response?.data?.error ?? e.message ?? '关闭失败')
    } finally {
      setBusy(false)
    }
  }

  if (!session) {
    return (
      <Flex vertical gap="small" align="flex-start">
        <Button icon={<MobileOutlined />} onClick={start} loading={busy}>
          {orphaned ? '重新生成二维码' : '用手机拍照上传'}
        </Button>
        <Text type="secondary">
          {orphaned
            ? '这场考试还有一个上传通道开着，但二维码只在生成时给出一次，这个页面已经拿不到了——点上面的按钮会关掉旧的、换一个新的。'
            : '电脑上会出现一个二维码，手机连同一个 Wi-Fi 扫码就能拍照上传，不经过公网。'}
        </Text>
      </Flex>
    )
  }

  const urls = session.urls ?? []
  const url = urls[urlIndex] ?? ''

  return (
    <Card size="small" type="inner" title={
      <Space wrap>
        <Text strong>手机上传通道已开启</Text>
        <Tag color={remaining.startsWith('0:') ? 'red' : 'green'}>剩余 {remaining}</Tag>
        <Tag>已收到 {uploadCount} 份</Tag>
      </Space>
    }>
      <Flex gap="large" wrap align="flex-start">
        {session.qr_svg ? (
          <div
            style={{ width: 190, height: 190, background: '#fff', padding: 8, border: '1px solid #ded9cb' }}
            // The SVG comes from our own backend's QR generator, not from user input.
            dangerouslySetInnerHTML={{ __html: session.qr_svg.replace(/width="[^"]*"\s+height="[^"]*"/, 'width="100%" height="100%"') }}
          />
        ) : (
          <Alert type="warning" showIcon title="二维码生成失败" description="用下面的地址在手机浏览器里手动打开也一样。" />
        )}
        <Flex vertical gap="small" style={{ flex: 1, minWidth: 260 }}>
          <Text>用手机扫码，或在手机浏览器里打开这个地址：</Text>
          <Text code copyable={{ text: url }} style={{ wordBreak: 'break-all' }}>{url || '（没有可用的局域网地址）'}</Text>
          {urls.length > 1 && (
            <Flex gap="small" align="center" wrap>
              <Text type="secondary">扫不通？换一个网卡地址：</Text>
              <Select
                size="small" style={{ minWidth: 200 }} value={urlIndex} onChange={setUrlIndex}
                options={urls.map((u, i) => ({ value: i, label: session.addresses?.[i] ?? u }))}
              />
            </Flex>
          )}
          <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0 }}>{session.security_note}</Paragraph>
          <Text type="secondary" style={{ fontSize: 12 }}>
            第一次开启时 Windows 可能会弹防火墙授权，需要允许「专用网络」才能让手机连上。
          </Text>
          <Space>
            <Button danger icon={<StopOutlined />} onClick={stop} loading={busy}>关闭通道</Button>
          </Space>
        </Flex>
      </Flex>
    </Card>
  )
}
