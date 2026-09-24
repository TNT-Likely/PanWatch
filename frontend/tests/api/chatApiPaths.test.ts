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
})
