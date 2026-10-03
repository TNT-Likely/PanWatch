import { useCallback, useEffect, useRef, useState } from 'react'
import { Bell, Bot, TrendingUp, MessageSquareText } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { notificationsApi, type NotificationItem, type NotificationPage } from '@panwatch/api'
import { formatDate } from '@/i18n/format'
import { notificationTitle } from '@/lib/notifications'
import type { useNotifications } from '@/hooks/useNotifications'

interface Props {
  monitor: ReturnType<typeof useNotifications>
  onOpen: (item: NotificationItem) => Promise<void>
  onHistory: () => void
}

/** Bell preview: only unread notices and unresolved actions, no history controls. */
export default function NotificationInbox({ monitor, onOpen, onHistory }: Props) {
  const { t } = useTranslation('configuration')
  const translate = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const tr = (key: string, options?: Record<string, unknown>) => translate(`notifications.${key}`, options)
  const [page, setPage] = useState<NotificationPage>({ items: [], observed_id: 0, next_cursor: null })
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [opening, setOpening] = useState(false)
  const request = useRef<AbortController | null>(null)
  const load = useCallback(async () => {
    request.current?.abort()
    const abort = new AbortController(); request.current = abort
    setLoading(true)
    try {
      const next = await notificationsApi.list({ view: 'attention', limit: 10 }, abort.signal)
      if (!abort.signal.aborted) { setPage(next); setFailed(false) }
    } catch {
      if (!abort.signal.aborted) setFailed(true)
    } finally {
      if (!abort.signal.aborted) setLoading(false)
    }
  }, [])
  useEffect(() => { void load(); return () => request.current?.abort() }, [load, monitor.revision])
  useEffect(() => {
    const wake = () => { void load() }
    window.addEventListener('online', wake); window.addEventListener('focus', wake)
    return () => { window.removeEventListener('online', wake); window.removeEventListener('focus', wake) }
  }, [load])
  const pending = page.items.filter(item => item.action_required)
  const unread = page.items.filter(item => !item.action_required)
  const row = (item: NotificationItem) => {
    const Icon = item.source === 'assistant' ? MessageSquareText : item.source === 'agent' ? Bot : TrendingUp
    return <li key={item.id} data-notification-id={item.id}>
      <button type="button" disabled={!item.available || opening} onClick={async () => { setOpening(true); try { await onOpen(item) } finally { setOpening(false) } }} className="flex w-full gap-3 rounded-lg px-3 py-3 text-left hover:bg-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary disabled:opacity-60">
        <Icon aria-hidden className={`mt-0.5 h-4 w-4 shrink-0 ${item.action_required ? 'text-primary' : 'text-muted-foreground'}`} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-medium">{notificationTitle(item, translate)}</span>
          <span className="mt-1 block text-[12px] leading-5 text-muted-foreground">{tr(`events.${item.template_key}`, item.template_params)}</span>
          <span className="mt-1 block text-[10px] text-muted-foreground">{tr(`sources.${item.source}`)} · {formatDate(item.occurred_at)}{!item.available && ` · ${tr('sourceGone')}`}</span>
        </span>
        {!item.read_at && <span aria-label={tr('views.unread')} className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />}
      </button>
    </li>
  }
  return <section data-testid="notification-inbox" aria-label={tr('title')}>
    <header className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
      <h2 className="text-[14px] font-semibold">{tr('previewTitle')}</h2>
      <button type="button" disabled={monitor.changing || loading || !page.observed_id || monitor.summary.unread_count === 0} className="text-[12px] text-primary disabled:text-muted-foreground" onClick={() => void monitor.mutate({ through_id: page.observed_id, view: 'attention' }).then(load).catch(() => {})}>{tr('readAll')}</button>
    </header>
    <div className="max-h-[min(26rem,calc(100dvh-10rem))] overflow-y-auto overscroll-contain scrollbar px-1 py-1">
      {(failed || monitor.disconnected) && <p role="status" className="px-3 py-2 text-[12px] text-muted-foreground">{tr('disconnected')} <button onClick={() => void load()} className="text-primary">{tr('refresh')}</button></p>}
      {monitor.error && <p role="alert" className="px-3 py-2 text-[12px] text-destructive">{tr('changeFailed')}</p>}
      {loading && page.items.length === 0 && <p role="status" className="px-3 py-5 text-[12px] text-muted-foreground">{tr('loading')}</p>}
      {!loading && !failed && page.items.length === 0 && <div className="flex flex-col items-center gap-2 px-3 py-9 text-muted-foreground"><Bell aria-hidden className="h-6 w-6" /><p className="text-[13px]">{tr('noNew')}</p></div>}
      {pending.length > 0 && <><h3 className="px-3 pt-2 text-[11px] font-medium text-primary">{tr('pending')}</h3><ul>{pending.map(row)}</ul></>}
      {unread.length > 0 && <><h3 className="px-3 pt-2 text-[11px] text-muted-foreground">{tr('views.unread')}</h3><ul>{unread.map(row)}</ul></>}
    </div>
    <footer className="border-t border-border p-2"><button type="button" onClick={onHistory} className="w-full rounded-lg py-2 text-[12px] text-primary hover:bg-accent">{tr('viewAll')}</button></footer>
  </section>
}
