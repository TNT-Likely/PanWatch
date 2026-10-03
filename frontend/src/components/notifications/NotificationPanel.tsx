import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { notificationsApi, type NotificationFilter, type NotificationItem, type NotificationPage, type NotificationSource, type NotificationView } from '@panwatch/api'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { formatDate } from '@/i18n/format'
import { notificationTitle } from '@/lib/notifications'
import type { useNotifications } from '@/hooks/useNotifications'

const button = 'rounded-lg border border-border bg-background px-2.5 py-1.5 text-[12px] disabled:opacity-50 hover:bg-accent'
interface Props { monitor: ReturnType<typeof useNotifications>; onClose: () => void; onOpen: (item: NotificationItem) => Promise<void> }
export default function NotificationPanel({ monitor, onClose, onOpen }: Props) {
  const { t } = useTranslation('configuration')
  const translate = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const tr = (key: string, options?: Record<string, unknown>) => translate(`notifications.${key}`, options)
  const [source, setSource] = useState<NotificationSource | ''>('')
  const [view, setView] = useState<NotificationView>('all')
  const [page, setPage] = useState<NotificationPage>({ items: [], next_cursor: null, observed_id: 0 })
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [opening, setOpening] = useState<number | null>(null)
  const request = useRef<AbortController | null>(null)
  const epoch = useRef(0)
  const filter: NotificationFilter = { source: source || undefined, view }
  const load = useCallback(async (cursor?: string) => {
    const id = ++epoch.current
    request.current?.abort()
    const abort = new AbortController(); request.current = abort
    setLoading(true); setFailed(false)
    try {
      const next = await notificationsApi.list({ source: source || undefined, view, cursor }, abort.signal)
      if (epoch.current !== id || abort.signal.aborted) return
      setPage(previous => cursor ? { ...next, items: [...previous.items, ...next.items.filter(item => !previous.items.some(old => old.id === item.id))] } : next)
    } catch {
      if (epoch.current === id && !abort.signal.aborted) setFailed(true)
    } finally {
      if (epoch.current === id && !abort.signal.aborted) setLoading(false)
    }
  }, [source, view])
  useEffect(() => { void load(); return () => { epoch.current++; request.current?.abort() } }, [monitor.revision, load])

  useEffect(() => {
    const wake = () => { void load() }
    window.addEventListener('online', wake)
    window.addEventListener('focus', wake)
    return () => { window.removeEventListener('online', wake); window.removeEventListener('focus', wake) }
  }, [load])

  const group = (item: NotificationItem) => item.event_type === 'agent_failed' && item.group_key
    ? page.items.filter(other => other.group_key === item.group_key && other.event_type === 'agent_failed') : [item]
  const visibleItems = page.items.filter((item, index) => item.event_type !== 'agent_failed' || !item.group_key || expanded.has(item.group_key)
    || !page.items.slice(0, index).some(other => other.event_type === 'agent_failed' && other.group_key === item.group_key))
  const mutate = async (item?: NotificationItem, archive?: boolean) => {
    try { await monitor.mutate(item ? { ids: (expanded.has(item.group_key) ? [item] : group(item)).map(row => row.id), ...filter } : { through_id: page.observed_id, ...filter }, archive); await load() } catch { /* Monitor displays the failure. */ }
  }
  return <Dialog open onOpenChange={open => { if (!open) onClose() }}>
    <DialogContent data-testid="notification-panel" className="p-4 sm:p-6">
      <DialogHeader className="pr-8"><DialogTitle>{tr('title')}</DialogTitle><DialogDescription>{tr('description')}</DialogDescription></DialogHeader>
      <p className="text-[12px] text-muted-foreground">{tr('summary', { unread: monitor.summary.unread_count, pending: monitor.summary.pending_action_count })}</p>
      <div className="my-3 flex flex-wrap items-center gap-2">
        <Select value={source || 'all'} onValueChange={value => setSource(value === 'all' ? '' : value as NotificationSource)}>
          <SelectTrigger aria-label={tr('sourceFilter')} className="h-8 w-auto min-w-[120px] text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{tr('sources.all')}</SelectItem>
            {(['assistant', 'agent', 'market'] as const).map(value => <SelectItem key={value} value={value}>{tr(`sources.${value}`)}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={view} onValueChange={value => setView(value as NotificationView)}>
          <SelectTrigger aria-label={tr('viewFilter')} className="h-8 w-auto min-w-[120px] text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            {(['all', 'unread', 'pending', 'archived'] as const).map(value => <SelectItem key={value} value={value}>{tr(`views.${value}`)}</SelectItem>)}
          </SelectContent>
        </Select>
        <button className={button} disabled={loading || monitor.changing} onClick={() => { void load(); void monitor.refresh() }}>{tr('refresh')}</button>
        <button className={button} disabled={!page.observed_id || monitor.changing || loading} onClick={() => void mutate()}>{tr('readFiltered')}</button>
      </div>
      {(monitor.disconnected || failed) && <p role="status" className="mb-3 rounded-lg bg-muted p-3 text-[12px]">{tr('disconnected')}</p>}
      {monitor.error && <p role="alert" className="mb-3 text-[12px] text-destructive">{tr('changeFailed')}</p>}
      {loading && <p role="status" className="text-[12px] text-muted-foreground">{tr('loading')}</p>}
      {!loading && !failed && page.items.length === 0 && <p className="text-[12px] text-muted-foreground">{tr('empty')}</p>}
      <ul className="space-y-2">
        {visibleItems.map(item => <li key={item.id} data-notification-id={item.id} className={`rounded-xl border p-3 ${!item.read_at && !item.resolved_at ? 'border-primary/20 bg-primary/5' : 'border-border bg-background'}`}>
          <div className="mb-1 flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
            <span>{tr(`sources.${item.source}`)}</span><span>{formatDate(item.occurred_at)}</span>
            {item.action_required && <span className="text-primary">{tr('pending')}</span>}
            {item.resolved_at && <span>{tr('resolved')}</span>}
          </div>
          <p className="break-words text-[13px] font-medium">{notificationTitle(item, translate)}</p>
          <p className="mt-1 text-[12px] text-muted-foreground">{tr(`events.${item.template_key}`, item.template_params)}</p>
          {!item.available && <p className="mt-1 text-[12px] text-muted-foreground">{tr('sourceGone')}</p>}
          {group(item).length > 1 && <button className="mt-2 text-[11px] text-primary" onClick={() => setExpanded(previous => { const next = new Set(previous); if (next.has(item.group_key)) next.delete(item.group_key); else next.add(item.group_key); return next })}>{tr(expanded.has(item.group_key) ? 'collapseFailures' : 'repeatedFailures', { count: group(item).length })}</button>}
          <div className="mt-3 flex flex-wrap gap-2">
            <button className={button} disabled={!item.available || opening !== null} onClick={async () => { setOpening(item.id); try { await onOpen(item) } finally { setOpening(null) } }}>{tr(item.action_required ? 'review' : item.actions.some(action => action.kind === 'assistant_export') ? 'viewExport' : item.source === 'agent' ? 'viewReport' : item.source === 'market' ? 'viewHit' : 'viewConversation')}</button>
            {!item.read_at && <button className={button} disabled={monitor.changing} onClick={() => void mutate(item)}>{tr('read')}</button>}
            <button className={button} disabled={monitor.changing || item.action_required} onClick={() => void mutate(item, !item.archived_at)}>{tr(item.archived_at ? 'restore' : 'archive')}</button>
          </div>
        </li>)}
      </ul>
      {page.next_cursor && <button className={`${button} mt-4 w-full`} disabled={loading} onClick={() => void load(page.next_cursor!)}>{tr('more')}</button>}
    </DialogContent>
  </Dialog>
}
