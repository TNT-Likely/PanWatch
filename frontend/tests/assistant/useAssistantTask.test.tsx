import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { chatApi, type AssistantTaskSnapshot, type ChatStreamCallbacks } from '@panwatch/api'
import { useAssistantTask } from '@/hooks/useAssistantTask'

vi.mock('@panwatch/api', () => ({ chatApi: {
  sendAssistantMessageStream: vi.fn(), getAssistantTask: vi.fn(),
  subscribeAssistantTaskStream: vi.fn(), cancelAssistantTask: vi.fn(),
  retryAssistantTask: vi.fn(), decideAssistantApprovalStream: vi.fn(),
} }))

function task(overrides: Partial<AssistantTaskSnapshot> = {}): AssistantTaskSnapshot {
  return { id: 42, conversation_id: 1, status: 'running', pending_approvals: [], ...overrides }
}

function pending<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

function harness() {
  const callbacks = { onResetStream: vi.fn(), onReloadMessages: vi.fn().mockResolvedValue(undefined), onDone: vi.fn(), onToken: vi.fn(), onTrace: vi.fn() }
  const hook = renderHook(() => useAssistantTask(callbacks))
  act(() => hook.result.current.reset(1))
  return { ...hook, callbacks }
}

beforeEach(() => { vi.resetAllMocks(); sessionStorage.clear() })

describe('assistant task lifecycle', () => {
  it('stops once, aborts the stream and ignores events after cancellation', async () => {
    let events!: ChatStreamCallbacks
    let signal!: AbortSignal
    vi.mocked(chatApi.sendAssistantMessageStream).mockImplementation((_id, _text, handlers, abort) => {
      events = handlers; signal = abort!
      handlers.onRunStarted?.({ taskId: 42 })
      return new Promise((resolve) => abort!.addEventListener('abort', () => resolve()))
    })
    const response = pending<AssistantTaskSnapshot>()
    vi.mocked(chatApi.cancelAssistantTask).mockReturnValue(response.promise)
    const { result, callbacks } = harness()
    let sent!: Promise<void>
    act(() => { sent = result.current.send(1, '问题') })
    let stopped!: Promise<void>
    act(() => { stopped = result.current.cancel(); void result.current.cancel() })
    expect(chatApi.cancelAssistantTask).toHaveBeenCalledTimes(1)
    expect(result.current.control).toBe('stopping')
    await act(async () => { response.resolve(task({ status: 'cancelled', can_retry: true })); await stopped; await sent })
    act(() => { events.onToken?.('晚到的内容'); events.onDone?.({ message_id: 99, content: '晚到的回答', created_at: '' }) })
    expect(signal.aborted).toBe(true)
    expect(result.current.snapshot?.status).toBe('cancelled')
    expect(result.current.sending).toBe(false)
    expect(result.current.error).toBeNull()
    expect(callbacks.onDone).not.toHaveBeenCalled()
    expect(callbacks.onToken).not.toHaveBeenCalled()
    expect(sessionStorage.getItem('panwatch:assistant-task:1')).toBeNull()
  })

  it('retries the same task after the old terminal cursor and allows stopping the new run', async () => {
    const response = pending<AssistantTaskSnapshot>()
    vi.mocked(chatApi.retryAssistantTask).mockReturnValue(response.promise)
    vi.mocked(chatApi.cancelAssistantTask).mockResolvedValue(task({ status: 'cancelled', can_retry: true }))
    vi.mocked(chatApi.subscribeAssistantTaskStream).mockImplementation((_id, handlers, signal) => {
      handlers.onRunStarted?.({ taskId: 42 })
      return new Promise((resolve) => signal!.addEventListener('abort', () => resolve()))
    })
    const { result } = harness()
    await act(async () => result.current.restore(1, task({ status: 'failed', can_retry: true, error_code: 'run_timeout', last_event_id: '20' })))
    let retry!: Promise<void>
    act(() => { retry = result.current.retry(); void result.current.retry() })
    expect(chatApi.retryAssistantTask).toHaveBeenCalledTimes(1)
    await act(async () => { response.resolve(task({ status: 'queued', retry_count: 1, last_event_id: '21', attempt_event_id: '21' })); await Promise.resolve() })
    expect(chatApi.subscribeAssistantTaskStream).toHaveBeenCalledWith(42, expect.any(Object), expect.any(AbortSignal), 21)
    expect(chatApi.sendAssistantMessageStream).not.toHaveBeenCalled()
    expect(result.current.control).toBeNull()
    expect(result.current.snapshot?.status).toBe('running')
    await act(async () => { await result.current.cancel(); await retry })
    expect(result.current.snapshot?.status).toBe('cancelled')
  })

  it('keeps a dropped connection distinct from a failed task and reconnects without resubmission', async () => {
    vi.mocked(chatApi.sendAssistantMessageStream).mockImplementation(async (_id, _text, events) => {
      events.onRunStarted?.({ taskId: 42 }); events.onToken?.('部分内容'); throw new Error('offline')
    })
    vi.mocked(chatApi.getAssistantTask).mockResolvedValue(task({ last_event_id: '8', attempt_event_id: '3' }))
    vi.mocked(chatApi.subscribeAssistantTaskStream).mockImplementation(async (_id, events) => {
      events.onToken?.('完整内容'); events.onDone?.({ message_id: 8, content: '完整回答', created_at: '' })
    })
    const { result, callbacks } = harness()
    await act(async () => result.current.send(1, '问题'))
    expect(result.current.disconnected).toBe(true)
    expect(result.current.error).toBeNull()
    expect(result.current.snapshot?.status).toBe('running')
    await act(async () => result.current.reconnect())
    expect(chatApi.sendAssistantMessageStream).toHaveBeenCalledTimes(1)
    expect(chatApi.subscribeAssistantTaskStream).toHaveBeenCalledWith(42, expect.any(Object), expect.any(AbortSignal), 3)
    expect(result.current.snapshot?.status).toBe('completed')
    expect(result.current.disconnected).toBe(false)
    expect(callbacks.onDone).toHaveBeenCalledTimes(1)
  })

  it('restores a retried run from its attempt boundary, including tokens before the snapshot cursor', async () => {
    vi.mocked(chatApi.subscribeAssistantTaskStream).mockImplementation(async (_id, events) => {
      events.onToken?.('恢复的回答'); events.onDone?.({ message_id: 9, content: '恢复的回答', created_at: '' })
    })
    const { result, callbacks } = harness()
    await act(async () => result.current.restore(1, task({ retry_count: 2, last_event_id: '70', attempt_event_id: '60' })))
    expect(chatApi.subscribeAssistantTaskStream).toHaveBeenCalledWith(42, expect.any(Object), expect.any(AbortSignal), 60)
    expect(callbacks.onToken).toHaveBeenCalledWith('恢复的回答')
  })

  it('restores terminal failure and tool evidence without a session marker', async () => {
    const trace = [{ event: 'tool_result', id: 7, data: { name: 'create_price_alert', ok: true, preview: '提醒已创建' } }]
    const { result, callbacks } = harness()
    await act(async () => result.current.restore(1, task({ status: 'failed', error_code: 'run_timeout', can_retry: false, retry_blocked_reason: 'tools_already_started', trace })))
    await act(async () => result.current.retry())
    expect(callbacks.onTrace).toHaveBeenCalledWith(trace[0])
    expect(result.current.error?.code).toBe('run_timeout')
    expect(chatApi.retryAssistantTask).not.toHaveBeenCalled()
    expect(chatApi.subscribeAssistantTaskStream).not.toHaveBeenCalled()
  })

  it('uses the completed snapshot when completion wins the stop race', async () => {
    const { result, callbacks } = harness()
    await act(async () => result.current.restore(1, task({ status: 'awaiting_approval' })))
    vi.mocked(chatApi.cancelAssistantTask).mockResolvedValue(task({ status: 'completed' }))
    await act(async () => result.current.cancel())
    expect(result.current.snapshot?.status).toBe('completed')
    expect(callbacks.onReloadMessages).toHaveBeenCalledWith(1)
    expect(result.current.error).toBeNull()
  })

  it('clears pending approvals when stopped', async () => {
    const { result } = harness()
    await act(async () => result.current.restore(1, task({ status: 'awaiting_approval', pending_approvals: [{ id: 'a', call_id: 'c', tool_name: 'create_price_alert', risk: 'write', arguments: {}, presentation: {}, expires_at: '' }] })))
    expect(result.current.pendingApprovals).toHaveLength(1)
    vi.mocked(chatApi.cancelAssistantTask).mockResolvedValue(task({ status: 'cancelled' }))
    await act(async () => result.current.cancel())
    expect(result.current.pendingApprovals).toEqual([])
  })

  it('does not overwrite the next conversation with a late stop response', async () => {
    const response = pending<AssistantTaskSnapshot>()
    vi.mocked(chatApi.cancelAssistantTask).mockReturnValue(response.promise)
    const { result } = harness()
    await act(async () => result.current.restore(1, task({ status: 'awaiting_approval' })))
    let stopped!: Promise<void>
    act(() => { stopped = result.current.cancel(); result.current.reset(2) })
    await act(async () => { response.resolve(task({ status: 'cancelled' })); await stopped })
    expect(result.current.snapshot).toBeNull()
    expect(result.current.control).toBeNull()
    expect(result.current.error).toBeNull()
  })

  it('keeps the unresolved task and offers status checking after a stop request fails', async () => {
    const { result } = harness()
    await act(async () => result.current.restore(1, task({ status: 'awaiting_approval' })))
    vi.mocked(chatApi.cancelAssistantTask).mockRejectedValue(new Error('offline'))
    await act(async () => result.current.cancel())
    expect(result.current.snapshot?.status).toBe('awaiting_approval')
    expect(result.current.controlError).toBe(true)
    vi.mocked(chatApi.getAssistantTask).mockResolvedValue(task({ status: 'cancelled', can_retry: true }))
    await act(async () => result.current.reconnect())
    expect(result.current.snapshot?.status).toBe('cancelled')
    expect(result.current.controlError).toBe(false)
  })

  it('does not queue a retry while the old worker is still stopping', async () => {
    const { result } = harness()
    await act(async () => result.current.restore(1, task({ status: 'cancelled', can_retry: true })))
    vi.mocked(chatApi.retryAssistantTask).mockResolvedValue(task({ status: 'cancelled', can_retry: false, retry_blocked_reason: 'worker_stopping' }))
    await act(async () => result.current.retry())
    expect(result.current.snapshot?.retry_blocked_reason).toBe('worker_stopping')
    expect(chatApi.subscribeAssistantTaskStream).not.toHaveBeenCalled()
    expect(result.current.control).toBeNull()
  })

  it('resolves a restored approval while retaining the next pending card', async () => {
    const approval = { id: 'a', call_id: 'c', tool_name: 'create_price_alert', risk: 'write' as const, arguments: {}, presentation: {}, expires_at: '' }
    vi.mocked(chatApi.decideAssistantApprovalStream).mockImplementation(async (_id, _decision, events) => {
      events.onPaused?.({ taskId: 42, reason: 'approval_required', resolvedApprovalId: 'a', resolvedStatus: 'approved' })
    })
    const { result } = harness()
    await act(async () => result.current.restore(1, task({ status: 'awaiting_approval', last_event_id: '20', pending_approvals: [approval, { ...approval, id: 'b', call_id: 'd' }] })))
    await act(async () => result.current.decide(result.current.pendingApprovals[0], 'approved'))
    expect(chatApi.decideAssistantApprovalStream).toHaveBeenCalledWith('a', 'approved', expect.any(Object), 42, expect.any(AbortSignal), 20)
    expect(result.current.pendingApprovals.map(item => [item.id, item.status])).toEqual([['a', 'approved'], ['b', 'pending']])
    expect(result.current.snapshot?.pending_approvals.map(item => item.id)).toEqual(['b'])
    expect(result.current.decidingApprovalId).toBeNull()
  })

  it('serializes approval decisions from multiple cards', async () => {
    const approval = { id: 'a', call_id: 'c', tool_name: 'create_price_alert', risk: 'write' as const, arguments: {}, presentation: {}, expires_at: '' }
    const response = pending<void>()
    vi.mocked(chatApi.decideAssistantApprovalStream).mockImplementation(async (_id, _decision, events) => {
      await response.promise
      events.onPaused?.({ taskId: 42, reason: 'approval_required', resolvedApprovalId: 'a', resolvedStatus: 'approved' })
    })
    const { result } = harness()
    await act(async () => result.current.restore(1, task({ status: 'awaiting_approval', pending_approvals: [approval, { ...approval, id: 'b' }] })))
    let first!: Promise<void>
    act(() => { first = result.current.decide(result.current.pendingApprovals[0], 'approved') })
    await expect(result.current.decide(result.current.pendingApprovals[1], 'approved')).rejects.toThrow('approval_pending')
    await act(async () => { response.resolve(); await first })
    expect(chatApi.decideAssistantApprovalStream).toHaveBeenCalledTimes(1)
  })
})
