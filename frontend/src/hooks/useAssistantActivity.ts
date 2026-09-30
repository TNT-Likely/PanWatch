import { useCallback, useEffect, useRef, useState } from 'react'
import { chatApi, type AssistantActivity } from '@panwatch/api'
import { ASSISTANT_ACTIVITY_CHANGED } from '@/lib/assistant-activity'

const EMPTY_ACTIVITY: AssistantActivity = { active_tasks: [], notifications: [], unread_count: 0, notification_cursor: 0 }

/** One compact monitor stays mounted in the authenticated application shell. */
export function useAssistantActivity(progressOnly = false) {
  const [activity, setActivity] = useState<AssistantActivity>(EMPTY_ACTIVITY)
  const activityRef = useRef(activity)
  const [disconnected, setDisconnected] = useState(false)
  const [loading, setLoading] = useState(true)
  const [reading, setReading] = useState(false)
  const [readError, setReadError] = useState(false)
  const readingRef = useRef(false)
  const mountedRef = useRef(false)
  const generationRef = useRef(0)
  const requestRef = useRef<Promise<void> | null>(null)
  const controllerRef = useRef<AbortController | null>(null)

  const update = useCallback((next: AssistantActivity) => {
    activityRef.current = next
    setActivity(next)
  }, [])

  const refresh = useCallback((): Promise<void> => {
    if (!mountedRef.current) return Promise.resolve()
    if (requestRef.current) return requestRef.current
    const generation = generationRef.current
    const controller = new AbortController()
    controllerRef.current = controller
    const request = (async () => {
      try {
        const next = progressOnly
          ? { ...EMPTY_ACTIVITY, active_tasks: await chatApi.getActiveAssistantTasks(controller.signal) }
          : await chatApi.getAssistantActivity(controller.signal)
        if (!mountedRef.current || generationRef.current !== generation) return
        update(next)
        setDisconnected(false)
      } catch {
        if (mountedRef.current && generationRef.current === generation && !controller.signal.aborted) setDisconnected(true)
      } finally {
        if (generationRef.current === generation) { setLoading(false); requestRef.current = null }
      }
    })()
    requestRef.current = request
    return request
  }, [progressOnly, update])

  useEffect(() => {
    mountedRef.current = true
    generationRef.current += 1
    let timer: ReturnType<typeof setTimeout> | undefined
    let stopped = false
    let refreshRequested = false
    let polling = false
    const poll = async () => {
      polling = true
      await refresh()
      polling = false
      if (stopped) return
      const delay = document.visibilityState === 'hidden' ? 30_000 : activityRef.current.active_tasks.length > 0 ? 5_000 : 15_000
      timer = setTimeout(poll, refreshRequested ? 0 : delay)
      refreshRequested = false
    }
    const wake = () => {
      clearTimeout(timer)
      if (polling) { refreshRequested = true; return }
      void poll()
    }
    void poll()
    window.addEventListener(ASSISTANT_ACTIVITY_CHANGED, wake)
    window.addEventListener('online', wake)
    window.addEventListener('focus', wake)
    document.addEventListener('visibilitychange', wake)
    return () => {
      stopped = true
      mountedRef.current = false
      generationRef.current += 1
      controllerRef.current?.abort()
      requestRef.current = null
      readingRef.current = false
      clearTimeout(timer)
      window.removeEventListener(ASSISTANT_ACTIVITY_CHANGED, wake)
      window.removeEventListener('online', wake)
      window.removeEventListener('focus', wake)
      document.removeEventListener('visibilitychange', wake)
    }
  }, [refresh])

  const markRead = useCallback(async (command: { ids?: number[]; through_id?: number }) => {
    if (readingRef.current || !mountedRef.current) return
    readingRef.current = true
    setReading(true)
    setReadError(false)
    const generation = generationRef.current
    try {
      const result = await chatApi.readAssistantNotifications(command)
      if (!mountedRef.current || generationRef.current !== generation) return
      const previous = activityRef.current
      update({ ...previous,
        unread_count: Math.max(0, previous.unread_count - result.updated),
        notifications: previous.notifications.map((item) => (command.through_id != null ? item.id <= command.through_id : command.ids?.includes(item.id))
          ? { ...item, read_at: item.read_at || new Date().toISOString() } : item),
      })
      // Refresh after any older GET so the final snapshot includes this read command.
      await requestRef.current
      if (!mountedRef.current || generationRef.current !== generation) return
      await refresh()
    } catch {
      if (mountedRef.current && generationRef.current === generation) setReadError(true)
    } finally {
      if (generationRef.current === generation) { readingRef.current = false; setReading(false) }
    }
  }, [refresh, update])

  return { activity, disconnected, loading, reading, readError, refresh, markRead }
}
