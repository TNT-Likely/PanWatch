import { act, renderHook } from '@testing-library/react'
import { StrictMode, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { chatApi, type AssistantActivity } from '@panwatch/api'
import { useAssistantActivity } from '@/hooks/useAssistantActivity'
import { ASSISTANT_ACTIVITY_CHANGED, claimAssistantNotifications } from '@/lib/assistant-activity'

vi.mock('@panwatch/api', () => ({ chatApi: { getAssistantActivity: vi.fn(), readAssistantNotifications: vi.fn() } }))
const empty = (): AssistantActivity => ({ active_tasks: [], notifications: [], unread_count: 0, notification_cursor: 0 })
const running = (): AssistantActivity => ({ ...empty(), active_tasks: [{ id: 42, conversation_id: 1, title: '后台研究', status: 'running', current_step: 2 }] })
function pending<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

beforeEach(() => {
  vi.resetAllMocks(); vi.useFakeTimers(); localStorage.clear()
  vi.mocked(chatApi.getAssistantActivity).mockResolvedValue(empty())
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
})
afterEach(() => { vi.useRealTimers() })

describe('background activity monitor', () => {
  it('discovers tasks without a browser marker and polls independently of the conversation stream', async () => {
    vi.mocked(chatApi.getAssistantActivity).mockResolvedValue(running())
    const { result } = renderHook(useAssistantActivity)
    await act(async () => {})
    expect(result.current.activity.active_tasks[0].id).toBe(42)
    await act(async () => vi.advanceTimersByTimeAsync(5_000))
    expect(chatApi.getAssistantActivity).toHaveBeenCalledTimes(2)
  })

  it('preserves the last known task when offline and refreshes on reconnection', async () => {
    vi.mocked(chatApi.getAssistantActivity).mockResolvedValueOnce(running()).mockRejectedValueOnce(new Error('offline')).mockResolvedValue(empty())
    const { result } = renderHook(useAssistantActivity)
    await act(async () => {})
    await act(async () => vi.advanceTimersByTimeAsync(5_000))
    expect(result.current.disconnected).toBe(true)
    expect(result.current.activity.active_tasks[0].status).toBe('running')
    await act(async () => { window.dispatchEvent(new Event('online')) })
    expect(result.current.disconnected).toBe(false)
    expect(result.current.activity.active_tasks).toEqual([])
  })

  it('coalesces simultaneous refreshes without losing a task change during the first response', async () => {
    const request = pending<AssistantActivity>()
    vi.mocked(chatApi.getAssistantActivity).mockReturnValueOnce(request.promise).mockResolvedValue(running())
    const { result } = renderHook(useAssistantActivity)
    act(() => { void result.current.refresh(); window.dispatchEvent(new Event(ASSISTANT_ACTIVITY_CHANGED)) })
    expect(chatApi.getAssistantActivity).toHaveBeenCalledTimes(1)
    await act(async () => { request.resolve(empty()); await request.promise })
    await act(async () => vi.advanceTimersByTimeAsync(0))
    expect(chatApi.getAssistantActivity).toHaveBeenCalledTimes(2)
    expect(result.current.activity.active_tasks[0].id).toBe(42)
  })

  it('uses a slower background poll and refreshes immediately when the tab becomes visible', async () => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
    vi.mocked(chatApi.getAssistantActivity).mockResolvedValue(running())
    renderHook(useAssistantActivity)
    await act(async () => {})
    await act(async () => vi.advanceTimersByTimeAsync(29_000))
    expect(chatApi.getAssistantActivity).toHaveBeenCalledTimes(1)
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')) })
    expect(chatApi.getAssistantActivity).toHaveBeenCalledTimes(2)
  })

  it('aborts its GET and removes poll listeners on unmount', async () => {
    const request = pending<AssistantActivity>()
    vi.mocked(chatApi.getAssistantActivity).mockReturnValue(request.promise)
    const { unmount } = renderHook(useAssistantActivity)
    const signal = vi.mocked(chatApi.getAssistantActivity).mock.calls[0][0]!
    unmount()
    expect(signal.aborted).toBe(true)
    request.resolve(running())
    await act(async () => vi.advanceTimersByTimeAsync(30_000))
    window.dispatchEvent(new Event('online'))
    expect(chatApi.getAssistantActivity).toHaveBeenCalledTimes(1)
  })

  it('keeps one polling loop after StrictMode remounts its effects', async () => {
    vi.mocked(chatApi.getAssistantActivity).mockResolvedValue(running())
    const { result, unmount } = renderHook(useAssistantActivity, { wrapper: ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode> })
    await act(async () => {})
    expect(result.current.activity.active_tasks[0].id).toBe(42)
    const initialRequests = vi.mocked(chatApi.getAssistantActivity).mock.calls.length
    await act(async () => vi.advanceTimersByTimeAsync(5_000))
    expect(chatApi.getAssistantActivity).toHaveBeenCalledTimes(initialRequests + 1)
    unmount()
    await act(async () => vi.advanceTimersByTimeAsync(30_000))
    expect(chatApi.getAssistantActivity).toHaveBeenCalledTimes(initialRequests + 1)
  })

  it('refreshes authoritative read state after an older in-flight GET finishes', async () => {
    const notice = { id: 8, task_id: 42, conversation_id: 1, title: '研究', kind: 'completed' as const }
    const unread = { ...empty(), notifications: [notice], unread_count: 1, notification_cursor: 8 }
    const olderGet = pending<AssistantActivity>()
    vi.mocked(chatApi.getAssistantActivity).mockResolvedValueOnce(unread).mockReturnValueOnce(olderGet.promise)
      .mockResolvedValue({ ...unread, unread_count: 0, notifications: [{ ...notice, read_at: '2026-09-30T00:00:00Z' }] })
    vi.mocked(chatApi.readAssistantNotifications).mockResolvedValue({ updated: 1 })
    const { result } = renderHook(useAssistantActivity)
    await act(async () => {})
    act(() => { void result.current.refresh() })
    let reading!: Promise<void>
    await act(async () => { reading = result.current.markRead({ ids: [8] }) })
    await act(async () => { olderGet.resolve(unread); await reading })
    expect(chatApi.getAssistantActivity).toHaveBeenCalledTimes(3)
    expect(result.current.activity.unread_count).toBe(0)
    expect(result.current.activity.notifications[0].read_at).toBeTruthy()
  })

  it('keeps unread state when marking read fails and permits a later retry', async () => {
    const data: AssistantActivity = { ...empty(), unread_count: 1, notification_cursor: 8, notifications: [{ id: 8, task_id: 42, conversation_id: 1, title: '研究', kind: 'completed' }] }
    vi.mocked(chatApi.getAssistantActivity).mockResolvedValue(data)
    vi.mocked(chatApi.readAssistantNotifications).mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ updated: 1 })
    const { result } = renderHook(useAssistantActivity)
    await act(async () => {})
    await act(async () => result.current.markRead({ through_id: 8 }))
    expect(result.current.readError).toBe(true)
    expect(result.current.activity.unread_count).toBe(1)
    vi.mocked(chatApi.getAssistantActivity).mockResolvedValue({ ...data, unread_count: 0, notifications: [{ ...data.notifications[0], read_at: '2026-09-30T00:00:00Z' }] })
    await act(async () => result.current.markRead({ through_id: 8 }))
    expect(result.current.readError).toBe(false)
    expect(result.current.activity.unread_count).toBe(0)
    expect(chatApi.readAssistantNotifications).toHaveBeenCalledTimes(2)
  })
})

describe('notification presentation deduplication', () => {
  it('deduplicates across polls and a fresh browser instance without changing inbox read status', () => {
    expect(claimAssistantNotifications([1, 2])).toEqual([1, 2])
    expect(claimAssistantNotifications([2, 3])).toEqual([3])
    expect(claimAssistantNotifications([1, 2, 3])).toEqual([])
  })

  it('recovers from a malformed browser preference', () => {
    localStorage.setItem('panwatch:assistant-notified', '{bad')
    expect(claimAssistantNotifications([8])).toEqual([8])
  })

  it('deduplicates in memory when browser storage is unavailable', () => {
    const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('unavailable') })
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('unavailable') })
    try {
      expect(claimAssistantNotifications([987654])).toEqual([987654])
      expect(claimAssistantNotifications([987654])).toEqual([])
    } finally { get.mockRestore(); set.mockRestore() }
  })
})
