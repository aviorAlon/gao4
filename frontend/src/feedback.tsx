import { App } from 'antd'
import type { MessageInstance } from 'antd/es/message/interface'

/** Toast messages that respect the app's theme.
 *
 * antd's static `message.success(...)` renders outside the React tree, so it can't see the
 * ConfigProvider theme and logs "Static function can not consume context" on every call. The
 * sanctioned fix is `App.useApp()`, which only works inside a component — but the call sites are
 * mostly in async handlers. This bridges the two: <FeedbackBridge/> sits inside <App> and hands
 * its context-bound instance to a module-level holder, and `message` here forwards to it.
 * Call sites keep the familiar `message.error(...)` shape and just import it from here.
 */
let instance: MessageInstance | null = null

export function FeedbackBridge() {
  instance = App.useApp().message
  return null
}

type Level = 'success' | 'error' | 'warning' | 'info' | 'loading'

function forward(level: Level) {
  return (content: Parameters<MessageInstance[Level]>[0], duration?: number) => {
    if (instance) return instance[level](content, duration)
    // Before the bridge has mounted (practically never): don't lose the message silently.
    console[level === 'error' ? 'error' : 'warn'](`[${level}]`, content)
    return undefined
  }
}

export const message = {
  success: forward('success'),
  error: forward('error'),
  warning: forward('warning'),
  info: forward('info'),
  loading: forward('loading'),
}
