import { act, renderHook } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useQuoteRefresh } from '@/hooks/useQuoteRefresh'

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

it('waits for a slow refresh and cancels it when the page unmounts', async () => {
  vi.useFakeTimers()
  let finish!: () => void
  const refresh = vi.fn((_automatic: boolean, _signal?: AbortSignal) => new Promise<void>(resolve => { finish = resolve }))
  const { unmount } = renderHook(() => useQuoteRefresh(true, 1, refresh))
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  expect(refresh).toHaveBeenCalledTimes(1)
  await act(async () => { finish() })
  await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
  expect(refresh).toHaveBeenCalledTimes(2)
  const signal = refresh.mock.calls[1][1] as AbortSignal
  unmount()
  expect(signal.aborted).toBe(true)
  finish()
  await act(async () => {})
  expect(vi.getTimerCount()).toBe(0)
})

it('skips hidden tabs and refreshes when they become visible', async () => {
  vi.useFakeTimers()
  const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
  const refresh = vi.fn(async () => {})
  const { unmount } = renderHook(() => useQuoteRefresh(true, 1, refresh))
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(refresh).not.toHaveBeenCalled()
  hidden.mockReturnValue(false)
  await act(async () => { document.dispatchEvent(new Event('visibilitychange')) })
  expect(refresh).toHaveBeenCalledTimes(1)
  unmount()
})
