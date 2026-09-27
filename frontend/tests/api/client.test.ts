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
})
