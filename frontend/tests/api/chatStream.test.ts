import { beforeEach, describe, expect, it, vi } from 'vitest'

const { readSSE } = vi.hoisted(() => ({ readSSE: vi.fn() }))

vi.mock('../../packages/api/src/sse', () => ({
  readSSE,
}))

import { chatApi } from '../../packages/api/src/chat'

describe('assistant task stream', () => {
  beforeEach(() => {
    readSSE.mockReset()
  })

  it('reconnects a durable task stream after the POST connection drops', async () => {
    const calls: Array<{ path: string; lastEventId?: number }> = []
    let firstConnection = true
    readSSE.mockImplementation(async (path: string, options: { lastEventId?: number; onEvent: (event: unknown) => void }) => {
      calls.push({ path, lastEventId: options.lastEventId })
      if (firstConnection) {
        firstConnection = false
        options.onEvent({ id: 1, event: 'task_created', data: { task_id: 42 } })
        options.onEvent({ id: 2, event: 'task_queued', data: { task_id: 42 } })
        throw new Error('connection dropped')
      }
      options.onEvent({ id: 3, event: 'run_started', data: { task_id: 42 } })
      options.onEvent({
        id: 4,
        event: 'done',
        data: { message_id: 7, content: '完成', created_at: '' },
      })
      return { lastEventId: 4 }
    })

    const onDone = vi.fn()
    await chatApi.sendAssistantMessageStream(1, '分析市场', { onDone })

    expect(calls).toEqual([
      {
        path: '/assistant/conversations/1/messages/stream',
        lastEventId: undefined,
      },
      {
        path: '/assistant/tasks/42/events',
        lastEventId: 2,
      },
    ])
    expect(onDone).toHaveBeenCalledWith({
      message_id: 7,
      content: '完成',
      created_at: '',
      result: null,
    })
  })

  it('reconnects an approval decision without submitting it twice', async () => {
    const calls: Array<{ path: string; method?: string; lastEventId?: number }> = []
    let firstConnection = true
    readSSE.mockImplementation(async (path: string, options: { method?: string; lastEventId?: number; onEvent: (event: any) => void }) => {
      calls.push({ path, method: options.method, lastEventId: options.lastEventId })
      if (firstConnection) {
        firstConnection = false
        options.onEvent({ id: 8, event: 'tool_result', data: { name: 'create_price_alert', ok: true } })
        throw new Error('response disconnected after decision was accepted')
      }
      options.onEvent({
        id: 9,
        event: 'done',
        data: { message_id: 10, content: '全部完成', created_at: '' },
      })
      return { lastEventId: 9 }
    })

    const onDone = vi.fn()
    await chatApi.decideAssistantApprovalStream('approval-1', 'approved', { onDone }, 42)

    expect(calls).toEqual([
      {
        path: '/assistant/approvals/approval-1/decision/stream',
        method: 'POST',
        lastEventId: undefined,
      },
      {
        path: '/assistant/tasks/42/events',
        method: undefined,
        lastEventId: 8,
      },
    ])
    expect(onDone).toHaveBeenCalledWith({
      message_id: 10,
      content: '全部完成',
      created_at: '',
      result: null,
    })
  })

  it.each(['empty', 'disconnected'])('resumes past the prior approval pause after an %s decision response', async (mode) => {
    const onDone = vi.fn()
    const onPaused = vi.fn()
    const calls: Array<{ path: string; lastEventId?: number }> = []
    readSSE.mockImplementation(async (path: string, options: { lastEventId?: number; onEvent: (event: any) => void }) => {
      calls.push({ path, lastEventId: options.lastEventId })
      if (path.includes('/decision/stream')) {
        if (mode === 'disconnected') throw new Error('decision accepted; response lost before first event')
        return { lastEventId: 0 }
      }
      if ((options.lastEventId || 0) < 20) {
        options.onEvent({ id: 20, event: 'paused', data: { task_id: 42, reason: 'approval_required' } })
      } else {
        options.onEvent({ id: 21, event: 'done', data: { message_id: 10, content: 'created once', created_at: '' } })
      }
      return { lastEventId: 21 }
    })
    await chatApi.decideAssistantApprovalStream('approval-1', 'approved', { onDone, onPaused }, 42, undefined, 20)
    expect(calls).toEqual([
      { path: '/assistant/approvals/approval-1/decision/stream', lastEventId: undefined },
      { path: '/assistant/tasks/42/events', lastEventId: 20 },
    ])
    expect(onPaused).not.toHaveBeenCalled()
    expect(onDone).toHaveBeenCalledTimes(1)
  })

  it('preserves a structured result on the terminal event', async () => {
    const result = {
      schema_version: 1,
      summary: '结论',
      facts: [],
      inferences: [],
      risks: [],
      missing_data: [],
      evidence: [],
      next_actions: [],
    }
    readSSE.mockImplementation(async (_path: string, options: { onEvent: (event: unknown) => void }) => {
      options.onEvent({
        id: 4,
        event: 'done',
        data: { message_id: 7, content: '完成', created_at: '', result },
      })
      return { lastEventId: 4 }
    })

    const onDone = vi.fn()
    await chatApi.sendAssistantMessageStream(1, '分析市场', { onDone })

    expect(onDone).toHaveBeenCalledWith({
      message_id: 7,
      content: '完成',
      created_at: '',
      result,
    })
  })

  it('preserves structured AI failure details from the task stream', async () => {
    readSSE.mockImplementation(async (_path: string, options: { onEvent: (event: unknown) => void }) => {
      options.onEvent({
        id: 3,
        event: 'error',
        data: {
          code: 'ai_quota_exhausted',
          message: 'AI 服务额度已用尽，请充值或切换可用模型后重试。',
          retryable: false,
        },
      })
      return { lastEventId: 3 }
    })

    const onError = vi.fn()

    await expect(chatApi.sendAssistantMessageStream(1, '分析市场', { onError }))
      .rejects.toThrow('AI 服务额度已用尽')
    expect(onError).toHaveBeenCalledWith({
      code: 'ai_quota_exhausted',
      message: 'AI 服务额度已用尽，请充值或切换可用模型后重试。',
      retryable: false,
    })
  })

  it('ends cancellation normally without reconnecting or reporting failure', async () => {
    readSSE.mockImplementation(async (_path: string, options: { onEvent: (event: unknown) => void }) => {
      options.onEvent({ id: 1, event: 'task_created', data: { task_id: 42 } })
      options.onEvent({ id: 2, event: 'cancelled', data: {} })
      return { lastEventId: 2 }
    })
    const onCancelled = vi.fn()
    const onError = vi.fn()
    await chatApi.sendAssistantMessageStream(1, '问题', { onCancelled, onError })
    expect(readSSE).toHaveBeenCalledTimes(1)
    expect(onCancelled).toHaveBeenCalledTimes(1)
    expect(onError).not.toHaveBeenCalled()
  })

  it('does not reconnect a known terminal error even if the stream contained a task ID', async () => {
    readSSE.mockImplementation(async (_path: string, options: { onEvent: (event: unknown) => void }) => {
      options.onEvent({ id: 1, event: 'run_started', data: { task_id: 42 } })
      options.onEvent({ id: 2, event: 'error', data: { code: 'run_timeout', message: 'timeout' } })
      return { lastEventId: 2 }
    })
    await expect(chatApi.sendAssistantMessageStream(1, '问题', {})).rejects.toThrow('timeout')
    expect(readSSE).toHaveBeenCalledTimes(1)
  })

  it('resumes a retry after the previous terminal events', async () => {
    readSSE.mockImplementation(async (_path: string, options: { lastEventId?: number; onEvent: (event: unknown) => void }) => {
      expect(options.lastEventId).toBe(21)
      options.onEvent({ id: 22, event: 'done', data: { message_id: 7, content: '完成' } })
      return { lastEventId: 22 }
    })
    await chatApi.subscribeAssistantTaskStream(42, {}, undefined, 21)
    expect(readSSE).toHaveBeenCalledTimes(1)
  })
})
