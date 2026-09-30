import { createContext, useCallback, useContext, useEffect, useState, lazy, Suspense, type ReactNode } from 'react'
import { Bell, AlertCircle } from 'lucide-react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { useAssistantActivity } from '@/hooks/useAssistantActivity'
import { claimAssistantNotifications } from '@/lib/assistant-activity'

const AssistantActivityPanel = lazy(() => import('./AssistantActivityPanel'))

const ActivityContext = createContext({ activeCount: 0, unreadCount: 0, disconnected: false, open: () => {} })
const foreground = () => document.visibilityState === 'visible' && document.hasFocus()

export function AssistantActivityBell({ mobile = false }: { mobile?: boolean }) {
  const { activeCount, unreadCount, disconnected, open } = useContext(ActivityContext)
  const { t } = useTranslation('configuration')
  const tr = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const label = tr('assistantPage.activity.bell', { active: activeCount, unread: unreadCount })
  const count = unreadCount || activeCount
  return (
    <button type="button" onClick={open} title={label} aria-label={label} data-testid="assistant-activity-bell"
      className={`relative flex shrink-0 items-center justify-center rounded-xl text-muted-foreground transition-colors hover:bg-background/70 hover:text-foreground ${mobile ? 'h-8 w-8' : 'h-9 w-9'}`}>
      {disconnected ? <AlertCircle className="h-4 w-4" /> : <Bell className="h-4 w-4" />}
      {count > 0 && <span aria-hidden="true" className={`absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full px-0.5 text-[9px] text-primary-foreground ${unreadCount > 0 ? 'bg-primary' : 'bg-muted-foreground'}`}>{count > 99 ? '99+' : count}</span>}
    </button>
  )
}

export function AssistantActivityProvider({ children }: { children: ReactNode }) {
  const monitor = useAssistantActivity()
  const { activity, disconnected, reading, readError, refresh, markRead } = monitor
  const [open, setOpen] = useState(false)
  const [isForeground, setForeground] = useState(foreground)
  const location = useLocation()
  const navigate = useNavigate()
  const { toast } = useToast()
  const { t } = useTranslation('configuration')
  const tr = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const currentConversationId = Number(/^\/assistant\/(\d+)$/.exec(location.pathname)?.[1]) || null

  useEffect(() => {
    const update = () => setForeground(foreground())
    window.addEventListener('focus', update)
    window.addEventListener('blur', update)
    document.addEventListener('visibilitychange', update)
    return () => {
      window.removeEventListener('focus', update)
      window.removeEventListener('blur', update)
      document.removeEventListener('visibilitychange', update)
    }
  }, [])

  const openConversation = useCallback((conversationId: number, notificationId?: number) => {
    if (notificationId) void markRead({ ids: [notificationId] })
    setOpen(false)
    navigate(`/assistant/${conversationId}`)
  }, [markRead, navigate])

  useEffect(() => {
    if (!isForeground) return
    const unread = activity.notifications.filter((item) => !item.read_at)
    const visible = unread.filter((item) => item.conversation_id === currentConversationId)
    if (visible.length > 0 && !reading && !readError) void markRead({ ids: visible.map((item) => item.id) })
    const background = unread.filter((item) => item.conversation_id !== currentConversationId)
    const ids = claimAssistantNotifications(background.map((item) => item.id))
    const newlyNotified = background.filter((item) => ids.includes(item.id))
    if (newlyNotified.length === 1) {
      const item = newlyNotified[0]
      toast(tr(`assistantPage.activity.toasts.${item.kind}`, { title: item.title || tr('assistantPage.newResearch') }), item.kind === 'failed' ? 'error' : item.kind === 'completed' ? 'success' : 'info', {
        label: tr('assistantPage.activity.viewConversation'), onClick: () => openConversation(item.conversation_id, item.id),
      })
    } else if (newlyNotified.length > 1) {
      toast(tr('assistantPage.activity.toasts.multiple', { count: newlyNotified.length }), 'info', { label: tr('assistantPage.activity.viewNotifications'), onClick: () => setOpen(true) })
    }
  }, [activity.notifications, currentConversationId, isForeground, markRead, openConversation, readError, reading, toast, tr])

  return (
    <ActivityContext.Provider value={{ activeCount: activity.active_tasks.length, unreadCount: activity.unread_count, disconnected, open: () => { setOpen(true); void refresh() } }}>
      {children}
      {open && <Suspense fallback={null}>
        <AssistantActivityPanel open={open} onOpenChange={setOpen} monitor={monitor} onOpenConversation={openConversation} />
      </Suspense>}
    </ActivityContext.Provider>
  )
}
