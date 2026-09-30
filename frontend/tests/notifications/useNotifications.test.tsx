import { act, renderHook } from '@testing-library/react'
import { StrictMode, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { notificationsApi, type NotificationSummary } from '@panwatch/api'
import { useNotifications } from '@/hooks/useNotifications'
import { NOTIFICATIONS_CHANGED, claimNotifications } from '@/lib/notifications'

vi.mock('@panwatch/api', () => ({ notificationsApi: { summary: vi.fn(), list: vi.fn(), read: vi.fn(), archive: vi.fn() } }))
const empty: NotificationSummary = { unread_count: 0, pending_action_count: 0, observed_id: 0 }
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: Error) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
beforeEach(() => {
  vi.resetAllMocks(); vi.useFakeTimers()
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
  vi.mocked(notificationsApi.summary).mockResolvedValue(empty)
  vi.mocked(notificationsApi.list).mockResolvedValue({ items: [], next_cursor: null, observed_id: 0 })
  vi.mocked(notificationsApi.read).mockResolvedValue({ updated: 1 })
})
afterEach(() => vi.useRealTimers())

describe('notification summary monitoring', () => {
  it('polls fast during tasks and uses only summaries when nothing changed', async () => {
    renderHook(() => useNotifications(1)); await act(async () => {})
    await act(async () => vi.advanceTimersByTimeAsync(5000))
    expect(notificationsApi.summary).toHaveBeenCalledTimes(2)
    expect(notificationsApi.list).not.toHaveBeenCalled()
  })
  it('coalesces requests and keeps a wake requested during an older request', async () => {
    const first = deferred<NotificationSummary>()
    vi.mocked(notificationsApi.summary).mockReturnValueOnce(first.promise)
    const { result } = renderHook(() => useNotifications(0))
    act(() => { void result.current.refresh(); window.dispatchEvent(new Event(NOTIFICATIONS_CHANGED)) })
    expect(notificationsApi.summary).toHaveBeenCalledTimes(1)
    await act(async () => { first.resolve(empty); await first.promise })
    await act(async () => vi.advanceTimersByTimeAsync(0))
    expect(notificationsApi.summary).toHaveBeenCalledTimes(2)
  })
  it('retains the latest state through an outage and recovers on reconnection', async () => {
    const data = { unread_count: 2, pending_action_count: 1, observed_id: 20 }
    vi.mocked(notificationsApi.summary).mockResolvedValueOnce(data).mockRejectedValueOnce(new Error('offline')).mockResolvedValue(empty)
    const { result } = renderHook(() => useNotifications(0)); await act(async () => {})
    await act(async () => { window.dispatchEvent(new Event('online')) })
    expect(result.current.disconnected).toBe(true); expect(result.current.summary).toEqual(data)
    await act(async () => { window.dispatchEvent(new Event('online')) })
    expect(result.current.disconnected).toBe(false); expect(result.current.summary).toEqual(empty)
  })
  it('serializes mutations without dropping a second read and reports errors', async () => {
    const first = deferred<{ updated: number }>()
    vi.mocked(notificationsApi.read).mockReturnValueOnce(first.promise).mockRejectedValueOnce(new Error('offline'))
    const { result } = renderHook(() => useNotifications(0)); await act(async () => {})
    let one!: Promise<void>; let two!: Promise<void>
    await act(async () => { one = result.current.mutate({ ids: [1] }); two = result.current.mutate({ ids: [2] }); void two.catch(() => {}) })
    expect(notificationsApi.read).toHaveBeenCalledTimes(1)
    await act(async () => { first.resolve({ updated: 1 }); await one; await two.catch(() => {}) })
    expect(notificationsApi.read).toHaveBeenCalledTimes(2); expect(result.current.error).toBe(true)
  })
  it('does not let an older GET overwrite a successful read', async () => {
    const old = deferred<NotificationSummary>()
    vi.mocked(notificationsApi.summary).mockResolvedValueOnce({ unread_count: 1, pending_action_count: 0, observed_id: 1 }).mockReturnValueOnce(old.promise).mockResolvedValue(empty)
    const { result } = renderHook(() => useNotifications(0)); await act(async () => {})
    act(() => { void result.current.refresh() })
    let mutation!: Promise<void>
    await act(async () => { mutation = result.current.mutate({ ids: [1] }) })
    await act(async () => { old.resolve({ unread_count: 1, pending_action_count: 0, observed_id: 1 }); await mutation })
    expect(result.current.summary.unread_count).toBe(0)
  })
  it('aborts stale requests and leaves one poll loop under StrictMode', async () => {
    const { unmount } = renderHook(() => useNotifications(0), { wrapper: ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode> })
    await act(async () => {})
    const initial = vi.mocked(notificationsApi.summary).mock.calls.length
    await act(async () => vi.advanceTimersByTimeAsync(15000))
    expect(notificationsApi.summary).toHaveBeenCalledTimes(initial + 1)
    const request = deferred<NotificationSummary>(); vi.mocked(notificationsApi.summary).mockReturnValue(request.promise)
    await act(async () => { window.dispatchEvent(new Event('online')) })
    const signal = vi.mocked(notificationsApi.summary).mock.calls.at(-1)![0]!
    unmount(); expect(signal.aborted).toBe(true)
    request.resolve(empty); await act(async () => vi.advanceTimersByTimeAsync(30000))
    expect(notificationsApi.summary).toHaveBeenCalledTimes(initial + 2)
  })
  it('deduplicates notification identities when browser storage is unavailable', () => {
    const read = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    const write = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    expect(claimNotifications([7891234])).toEqual([7891234]); expect(claimNotifications([7891234])).toEqual([])
    read.mockRestore(); write.mockRestore()
  })
})
