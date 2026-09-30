import { createContext, lazy, Suspense, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { Bell, ListTodo, AlertCircle } from 'lucide-react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { notificationsApi, type NotificationItem, type NotificationTarget } from '@panwatch/api'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { useNotifications } from '@/hooks/useNotifications'
import { useAssistantActivity } from '@/hooks/useAssistantActivity'
import { claimNotifications } from '@/lib/notifications'
import { localizeAgentName } from '@/i18n/agent-labels'

const NotificationPanel = lazy(() => import('./NotificationPanel'))
const SourceDialog = lazy(() => import('./NotificationSourceDialog'))
const TaskPanel = lazy(() => import('@/components/assistant/AssistantActivityPanel'))
const Context = createContext({ unread: 0, active: 0, disconnected: false, open: () => {}, openTasks: () => {} })
const foreground = () => document.visibilityState === 'visible' && document.hasFocus()
export function NotificationBell({ mobile = false }: { mobile?: boolean }) {
  const { unread, active, disconnected, open, openTasks } = useContext(Context)
  const { t } = useTranslation('configuration')
  const css = `relative flex shrink-0 items-center justify-center rounded-xl text-muted-foreground hover:bg-accent ${mobile ? 'h-8 w-8' : 'h-9 w-9'}`
  return <>
    {active > 0 && <button className={css} onClick={openTasks} aria-label={t('notifications.taskEntry', { count: active }) as string} data-testid="assistant-task-entry"><ListTodo className="h-4 w-4" /></button>}
    <button className={css} onClick={open} aria-label={t('notifications.bell', { count: unread }) as string} data-testid="notification-bell">
      {disconnected ? <AlertCircle className="h-4 w-4" /> : <Bell className="h-4 w-4" />}
      {unread > 0 && <span data-testid="notification-badge" aria-hidden className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-0.5 text-[9px] text-primary-foreground">{unread > 99 ? '99+' : unread}</span>}
    </button>
  </>
}
export function NotificationProvider({ children }: { children: ReactNode }) {
  const tasks = useAssistantActivity(true)
  const monitor = useNotifications(tasks.activity.active_tasks.length)
  const { unread, changing, error, mutate } = monitor
  const [open, setOpen] = useState(false)
  const [tasksOpen, setTasksOpen] = useState(false)
  const [target, setTarget] = useState<Exclude<NotificationTarget, { kind: 'assistant_conversation' }> | null>(null)
  const [focused, setFocused] = useState(foreground)
  const location = useLocation(); const navigate = useNavigate()
  const { t } = useTranslation('configuration'); const { toast } = useToast()
  const mounted = useRef(true)
  const currentConversation = Number(/^\/assistant\/(\d+)$/.exec(location.pathname)?.[1]) || null
  const tr = useCallback((key: string, options?: Record<string, unknown>) => (t as unknown as (key: string, options?: Record<string, unknown>) => string)(`notifications.${key}`, options), [t])
  useEffect(() => {
    mounted.current = true
    const update = () => setFocused(foreground())
    window.addEventListener('focus', update); window.addEventListener('blur', update); document.addEventListener('visibilitychange', update)
    return () => { mounted.current = false; window.removeEventListener('focus', update); window.removeEventListener('blur', update); document.removeEventListener('visibilitychange', update) }
  }, [])
  const openItem = useCallback(async (item: NotificationItem) => {
    try {
      const resource = await notificationsApi.target(item.id)
      if (!mounted.current) return
      void mutate({ ids: [item.id] }).catch(() => {})
      setOpen(false)
      if (resource.kind === 'assistant_conversation') navigate(`/assistant/${resource.conversation_id}`)
      else setTarget(resource)
    } catch {
      if (mounted.current) { toast(tr('sourceGone'), 'error'); void monitor.refresh() }
    }
  }, [mutate, navigate, toast, tr, monitor.refresh])
  useEffect(() => {
    if (!focused) return
    const visible = unread.filter(item => item.source === 'assistant' && item.actions.some(action => action.conversation_id === currentConversation))
    if (visible.length && !changing && !error) void mutate({ ids: visible.map(item => item.id) }).catch(() => {})
    const candidates = unread.filter(item => item.toast_eligible && item.available && !item.resolved_at && !visible.includes(item))
    const claimed = claimNotifications(candidates.map(item => item.id))
    const fresh = candidates.filter(item => claimed.includes(item.id))
    if (fresh.length === 1) {
      const item = fresh[0]
      toast(tr(`toasts.${item.event_type}`, { title: item.source === 'agent' ? localizeAgentName(item.title, item.title, t as unknown as (key: string) => string) : item.title || tr(`sources.${item.source}`) }), item.event_type.endsWith('_failed') ? 'error' : item.event_type === 'assistant_completed' ? 'success' : 'info', { label: tr('view'), onClick: () => void openItem(item) })
    } else if (fresh.length > 1) toast(tr('multiple', { count: fresh.length }), 'info', { label: tr('view'), onClick: () => setOpen(true) })
  }, [unread, focused, currentConversation, changing, error, mutate, openItem, toast, tr])
  return <Context.Provider value={{ unread: monitor.summary.unread_count, active: tasks.activity.active_tasks.length, disconnected: monitor.disconnected, open: () => { setOpen(true); void monitor.refresh() }, openTasks: () => { setTasksOpen(true); void tasks.refresh() } }}>
    {children}
    <Suspense fallback={null}>
      {open && <NotificationPanel monitor={monitor} onClose={() => setOpen(false)} onOpen={openItem} />}
      {target && <SourceDialog target={target} onClose={() => setTarget(null)} />}
      {tasksOpen && <TaskPanel open onOpenChange={setTasksOpen} monitor={tasks} progressOnly onOpenConversation={id => { setTasksOpen(false); navigate(`/assistant/${id}`) }} />}
    </Suspense>
  </Context.Provider>
}
