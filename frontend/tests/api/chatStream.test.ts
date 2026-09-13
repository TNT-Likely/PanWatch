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
    })
  })
})
