import { useCallback, useLayoutEffect, useRef } from 'react'

/** Latest request wins within a mounted view. Cleanup also invalidates callbacks
 * from the first mount of React StrictMode's setup/cleanup/setup cycle. */
export function useRequestScope() {
  const scope = useRef({ active: false, epoch: 0, requests: new Map<string, number>() })
  useLayoutEffect(() => {
    const current = scope.current
    current.active = true
    return () => {
      current.active = false
      current.epoch += 1
    }
  }, [])

  return useCallback((lane: string) => {
    const current = scope.current
    const epoch = current.epoch
    const request = (current.requests.get(lane) || 0) + 1
    current.requests.set(lane, request)
    return () => current.active && current.epoch === epoch && current.requests.get(lane) === request
  }, [])
}
