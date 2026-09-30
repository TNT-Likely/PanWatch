import { useCallback, useEffect, useRef, useState } from 'react'
import { notificationsApi, type NotificationItem, type NotificationSelection, type NotificationSummary } from '@panwatch/api'
import { ASSISTANT_ACTIVITY_CHANGED } from '@/lib/assistant-activity'
import { NOTIFICATIONS_CHANGED } from '@/lib/notifications'

const EMPTY: NotificationSummary = { unread_count: 0, pending_action_count: 0, observed_id: 0 }
export function useNotifications(activeCount: number) {
  const [summary, setSummary] = useState(EMPTY)
  const [unread, setUnread] = useState<NotificationItem[]>([])
  const [disconnected, setDisconnected] = useState(false)
  const [error, setError] = useState(false)
  const [changing, setChanging] = useState(false)
  const [revision, setRevision] = useState(0)
  const mounted = useRef(false)
  const generation = useRef(0)
  const count = useRef(activeCount); count.current = activeCount
  const previous = useRef<NotificationSummary | null>(null)
  const request = useRef<Promise<void> | null>(null)
  const controller = useRef<AbortController | null>(null)
  const queue = useRef<Promise<void>>(Promise.resolve())

  const refresh = useCallback((): Promise<void> => {
    if (!mounted.current) return Promise.resolve()
    if (request.current) return request.current
    const epoch = generation.current
    const abort = new AbortController(); controller.current = abort
    const pending = (async () => {
      try {
        const next = await notificationsApi.summary(abort.signal)
        if (!mounted.current || generation.current !== epoch) return
        const changed = JSON.stringify(next) !== JSON.stringify(previous.current)
        if (changed) {
          const page = next.unread_count ? await notificationsApi.list({ view: 'unread', limit: 100 }, abort.signal) : { items: [] }
          if (!mounted.current || generation.current !== epoch) return
          setUnread(page.items)
          previous.current = next
          setRevision(value => value + 1)
        }
        setSummary(next); setDisconnected(false)
      } catch {
        if (mounted.current && generation.current === epoch && !abort.signal.aborted) setDisconnected(true)
      } finally {
        if (generation.current === epoch) request.current = null
      }
    })()
    request.current = pending
    return pending
  }, [])

  useEffect(() => {
    mounted.current = true; generation.current += 1
    let timer: ReturnType<typeof setTimeout> | undefined
    let stopped = false; let polling = false; let requested = false
    const poll = async () => {
      polling = true; await refresh(); polling = false
      if (stopped) return
      timer = setTimeout(poll, requested ? 0 : document.visibilityState === 'hidden' ? 30_000 : count.current ? 5_000 : 15_000)
      requested = false
    }
    const wake = () => { clearTimeout(timer); if (polling) requested = true; else void poll() }
    void poll()
    const events = [ASSISTANT_ACTIVITY_CHANGED, NOTIFICATIONS_CHANGED, 'online', 'focus']
    events.forEach(event => window.addEventListener(event, wake))
    document.addEventListener('visibilitychange', wake)
    return () => {
      stopped = true; mounted.current = false; generation.current += 1
      controller.current?.abort(); request.current = null; clearTimeout(timer)
      events.forEach(event => window.removeEventListener(event, wake))
      document.removeEventListener('visibilitychange', wake)
    }
  }, [refresh])

  const mutate = useCallback((command: NotificationSelection, archive?: boolean): Promise<void> => {
    const epoch = generation.current
    const operation = queue.current.catch(() => {}).then(async () => {
      if (!mounted.current || generation.current !== epoch) return
      setChanging(true); setError(false)
      try {
        if (archive == null) await notificationsApi.read(command)
        else await notificationsApi.archive({ ...command, archived: archive })
        if (!mounted.current || generation.current !== epoch) return
        await request.current
        previous.current = null // Re-fetch inbox even if counters did not change.
        await refresh()
      } catch (failure) {
        if (mounted.current && generation.current === epoch) setError(true)
        throw failure
      } finally {
        if (mounted.current && generation.current === epoch) setChanging(false)
      }
    })
    queue.current = operation
    return operation
  }, [refresh])
  return { summary, unread, disconnected, changing, error, revision, refresh, mutate }
}
