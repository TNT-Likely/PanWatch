import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchAPI } from '../../packages/api/src/client'

describe('fetchAPI error localization', () => {
  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('maps a stable API error code for the English interface', async () => {
    localStorage.setItem('panwatch-locale', 'en-US')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      status: 400,
      json: async () => ({
        code: 400,
        success: false,
        error_code: 'template_module_invalid',
        message: '不支持的配置模块',
      }),
    }))

    await expect(fetchAPI('/templates/import')).rejects.toThrow(
      'The configuration package contains an unsupported module.',
    )
  })

  it('keeps the server message for the Chinese interface', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      status: 404,
      json: async () => ({
        code: 404,
        success: false,
        error_code: 'stock_not_found',
        message: '股票不存在',
      }),
    }))

    await expect(fetchAPI('/stocks/1')).rejects.toThrow('股票不存在')
  })

  it('explains the A-share settlement error for the English interface', async () => {
    localStorage.setItem('panwatch-locale', 'en-US')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      status: 400,
      json: async () => ({ code: 400, error_code: 'paper_trading_t1_locked', message: 'A 股实行 T+1' }),
    }))
    await expect(fetchAPI('/paper-trading/positions/1/close')).rejects.toThrow(
      'A shares use T+1: positions bought today can be sold from the next trading day.',
    )
  })

  it('maps an unauthenticated error instead of treating sign-in failure as an expired session', async () => {
    localStorage.setItem('panwatch-locale', 'en-US')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      status: 401,
      json: async () => ({
        code: 401,
        success: false,
        error_code: 'invalid_credentials',
        message: '用户名或密码错误',
      }),
    }))

    await expect(fetchAPI('/auth/login', { method: 'POST' })).rejects.toThrow(
      'The username or password is incorrect.',
    )
  })
})
