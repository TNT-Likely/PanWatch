import { afterEach, describe, expect, it, vi } from 'vitest'

import { readSSE } from '../../packages/api/src/sse'

describe('SSE HTTP errors', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.removeItem('panwatch-locale')
  })

  it('uses the structured API error instead of an opaque HTTP status', async () => {
    localStorage.setItem('panwatch-locale', 'en-US')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: 429,
      error_code: 'ai_quota_exhausted',
      success: false,
      data: null,
      message: 'AI 服务额度已用尽',
    }), {
      status: 429,
      headers: { 'content-type': 'application/json' },
    })))

    await expect(readSSE('/assistant/test', { onEvent: vi.fn() }))
      .rejects.toThrow('The AI service quota is exhausted')
  })
})
