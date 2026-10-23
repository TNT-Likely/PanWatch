import { useEffect, useRef } from 'react'

/** Refresh visible quotes without overlapping slow requests or polling hidden tabs. */
export function useQuoteRefresh(enabled: boolean, intervalSeconds: number,
  refresh: (automatic: boolean, signal?: AbortSignal) => Promise<void>) {
  const latest = useRef(refresh)
  latest.current = refresh
  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    let running = false
    const poll = async () => {
      clearTimeout(timer)
      if (running || controller.signal.aborted) return
      running = true
      try {
        if (!document.hidden) await latest.current(true, controller.signal)
      } catch (error) {
        if (!controller.signal.aborted) console.warn('Quote refresh failed', error)
      } finally {
        running = false
        if (!controller.signal.aborted) timer = setTimeout(poll, Math.max(1, intervalSeconds) * 1000)
      }
    }
    void poll()
    document.addEventListener('visibilitychange', poll)
    window.addEventListener('online', poll)
    return () => {
      controller.abort()
      clearTimeout(timer)
      document.removeEventListener('visibilitychange', poll)
      window.removeEventListener('online', poll)
    }
  }, [enabled, intervalSeconds])
}
