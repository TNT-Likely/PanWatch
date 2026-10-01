import { beforeEach, describe, expect, it, vi } from 'vitest'

const { fetchAPI } = vi.hoisted(() => ({ fetchAPI: vi.fn() }))

vi.mock('../../packages/api/src/client', () => ({ fetchAPI }))

import { chatApi } from '../../packages/api/src/chat'

describe('assistant conversation API paths', () => {
  beforeEach(() => {
    fetchAPI.mockReset()
    fetchAPI.mockResolvedValue({})
  })

  it('uses only the canonical assistant route for conversation operations', async () => {
    await chatApi.createConversation()
    await chatApi.listConversations()
    await chatApi.getConversation(7)
    await chatApi.deleteConversation(7)
    await chatApi.getSuggestedQuestions('600519', 'CN')

    expect(fetchAPI.mock.calls.map(([path]) => path)).toEqual([
      '/assistant/conversations',
      '/assistant/conversations?limit=30',
      '/assistant/conversations/7',
      '/assistant/conversations/7',
      '/assistant/suggested-questions?symbol=600519&market=CN',
    ])
  })

  it('posts stop and retry commands to the existing task without creating another conversation', async () => {
    await chatApi.cancelAssistantTask(42)
    await chatApi.retryAssistantTask(42)
    expect(fetchAPI.mock.calls).toEqual([
      ['/assistant/tasks/42/cancel', { method: 'POST' }],
      ['/assistant/tasks/42/retry', { method: 'POST' }],
    ])
  })

  it('uses compact activity and read-state endpoints for background tracking', async () => {
    const controller = new AbortController()
    await chatApi.getAssistantActivity(controller.signal)
    await chatApi.readAssistantNotifications({ ids: [8] })
    expect(fetchAPI.mock.calls).toEqual([
      ['/assistant/activity', { signal: controller.signal }],
      ['/assistant/notifications/read', { method: 'POST', body: JSON.stringify({ ids: [8] }) }],
    ])
  })

  it('submits, reads, retries and filters durable Markdown exports', async () => {
    const signal = new AbortController().signal
    await chatApi.exportConversationContext(8, 'zh-CN', signal)
    await chatApi.getContextExport(7, signal)
    await chatApi.retryContextExport(7, signal)
    await chatApi.listContextExports({ conversationId: 8, beforeId: 7, ids: [6, 5], limit: 50 }, signal)
    expect(fetchAPI.mock.calls.slice(0, 3)).toEqual([
      ['/assistant/conversations/8/export', { method: 'POST', body: JSON.stringify({ language: 'zh-CN' }), signal }],
      ['/assistant/exports/7', { signal }],
      ['/assistant/exports/7/retry', { method: 'POST', signal }],
    ])
    const url = new URL(fetchAPI.mock.calls[3][0], 'https://example.com')
    expect(url.pathname).toBe('/assistant/exports')
    expect(url.searchParams.get('conversation_id')).toBe('8')
    expect(url.searchParams.get('before_id')).toBe('7')
    expect(url.searchParams.getAll('ids')).toEqual(['6', '5'])
    expect(url.searchParams.get('limit')).toBe('50')
    expect(fetchAPI.mock.calls[3][1]).toEqual({ signal })
  })
})
