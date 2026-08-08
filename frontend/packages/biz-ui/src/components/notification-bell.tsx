import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bell, CheckCheck, Trash2, CheckCircle2, AlertCircle, Info, AlertTriangle } from 'lucide-react'
import { fetchAPI } from '@panwatch/api'

export interface NotificationItem {
  id: number
  category: string
  level: 'info' | 'success' | 'warning' | 'error'
  title: string
  body: string
  link: string
  source: string
  trace_id: string
  push_status: string
  push_error: string
  read: boolean
  created_at: string
}

const LEVEL_ICON: Record<string, React.ReactNode> = {
  success: <CheckCircle2 className="w-4 h-4 text-emerald-500" />,
  error: <AlertCircle className="w-4 h-4 text-rose-500" />,
  warning: <AlertTriangle className="w-4 h-4 text-amber-500" />,
  info: <Info className="w-4 h-4 text-primary" />,
}

const PUSH_LABEL: Record<string, string> = {
  sent: '已推送',
  failed: '推送失败',
  skipped: '仅站内',
  pending: '推送中',
}

function timeAgo(iso: string): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const diff = Date.now() - t
  if (diff < 60_000) return '刚刚'
  if (diff < 3600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86400_000) return `${Math.floor(diff / 3600_000)} 小时前`
  return `${Math.floor(diff / 86400_000)} 天前`
}

/** 顶栏消息中心：未读红点 + 下拉列表。后台任务完成/失败在这里落地。 */
export function NotificationBell({ size = 'md' }: { size?: 'sm' | 'md' }) {
  const nav = useNavigate()
  const [open, setOpen] = useState(false)
  const [unread, setUnread] = useState(0)
  const [items, setItems] = useState<NotificationItem[]>([])
  const [loading, setLoading] = useState(false)
  const ref = useRef<HTMLDivElement | null>(null)

  const loadCount = useCallback(async () => {
    try {
      const r = await fetchAPI<{ unread: number }>('/notifications/unread-count')
      setUnread(r?.unread ?? 0)
    } catch {
      /* 静默：轮询失败不打扰用户 */
    }
  }, [])

  const loadList = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetchAPI<{ items: NotificationItem[]; unread: number }>('/notifications?limit=30')
      setItems(r?.items || [])
      setUnread(r?.unread ?? 0)
    } catch {
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [])

  // 未读数轮询（20s）。后台任务通常 1-5 分钟，20s 足够及时又不吵。
  useEffect(() => {
    void loadCount()
    const t = window.setInterval(() => void loadCount(), 20000)
    return () => window.clearInterval(t)
  }, [loadCount])

  useEffect(() => {
    if (open) void loadList()
  }, [open, loadList])

  // 点击外部关闭
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const markRead = async (n: NotificationItem) => {
    if (!n.read) {
      try {
        await fetchAPI(`/notifications/${n.id}/read`, { method: 'POST' })
        setItems(prev => prev.map(x => (x.id === n.id ? { ...x, read: true } : x)))
        setUnread(u => Math.max(0, u - 1))
      } catch { /* ignore */ }
    }
    if (n.link) {
      setOpen(false)
      nav(n.link)
    }
  }

  const markAll = async () => {
    try {
      await fetchAPI('/notifications/read-all', { method: 'POST' })
      setItems(prev => prev.map(x => ({ ...x, read: true })))
      setUnread(0)
    } catch { /* ignore */ }
  }

  const clearRead = async () => {
    try {
      await fetchAPI('/notifications/clear', { method: 'DELETE' })
      await loadList()
    } catch { /* ignore */ }
  }

  const box = size === 'sm' ? 'w-8 h-8' : 'w-9 h-9'

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(v => !v)}
        className={`${box} rounded-xl flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-background/70 transition-all relative`}
        title={unread > 0 ? `${unread} 条未读消息` : '消息中心'}
      >
        <Bell className="w-4 h-4" />
        {unread > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[16px] h-[16px] px-1 rounded-full bg-rose-500 text-white text-[10px] font-medium flex items-center justify-center ring-2 ring-card">
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-[360px] max-w-[calc(100vw-2rem)] rounded-2xl border border-border/60 bg-card shadow-[0_16px_48px_rgba(0,0,0,0.16)] z-[80] overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/50">
            <span className="text-[13px] font-semibold text-foreground">
              消息中心 {unread > 0 && <span className="text-rose-500">({unread} 未读)</span>}
            </span>
            <div className="flex items-center gap-1">
              <button onClick={markAll} title="全部已读"
                className="w-7 h-7 rounded-lg flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-accent transition-colors">
                <CheckCheck className="w-3.5 h-3.5" />
              </button>
              <button onClick={clearRead} title="清空已读"
                className="w-7 h-7 rounded-lg flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-accent transition-colors">
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>

          <div className="max-h-[420px] overflow-y-auto">
            {loading ? (
              <div className="py-8 text-center text-[12px] text-muted-foreground">加载中…</div>
            ) : items.length === 0 ? (
              <div className="py-10 text-center text-[12px] text-muted-foreground">
                暂无消息<br />
                <span className="text-[11px] opacity-70">后台任务完成后会出现在这里</span>
              </div>
            ) : (
              items.map(n => (
                <button
                  key={n.id}
                  onClick={() => void markRead(n)}
                  className={`w-full text-left px-4 py-3 border-b border-border/30 last:border-0 hover:bg-accent/40 transition-colors flex gap-2.5 ${
                    n.read ? 'opacity-60' : ''
                  }`}
                >
                  <span className="mt-0.5 flex-shrink-0">{LEVEL_ICON[n.level] || LEVEL_ICON.info}</span>
                  <span className="flex-1 min-w-0">
                    <span className="flex items-center gap-1.5">
                      {!n.read && <span className="w-1.5 h-1.5 rounded-full bg-rose-500 flex-shrink-0" />}
                      <span className="text-[12.5px] font-medium text-foreground truncate">{n.title}</span>
                    </span>
                    {n.body && (
                      <span className="block text-[11.5px] text-muted-foreground mt-0.5 line-clamp-2">{n.body}</span>
                    )}
                    <span className="flex items-center gap-2 mt-1 text-[10.5px] text-muted-foreground/70">
                      <span>{timeAgo(n.created_at)}</span>
                      {n.source && <span>· {n.source}</span>}
                      {n.push_status && (
                        <span
                          className={
                            n.push_status === 'sent'
                              ? 'text-emerald-500'
                              : n.push_status === 'failed'
                                ? 'text-rose-500'
                                : ''
                          }
                          title={n.push_error || ''}
                        >
                          · {PUSH_LABEL[n.push_status] || n.push_status}
                        </span>
                      )}
                    </span>
                  </span>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default NotificationBell
