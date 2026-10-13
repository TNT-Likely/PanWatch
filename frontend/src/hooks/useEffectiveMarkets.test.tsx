import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { resetEffectiveMarketsCacheForTests, useEffectiveMarkets } from './useEffectiveMarkets'

vi.mock('@panwatch/api', () => ({
  marketsApi: {
    getEffectiveMarkets: vi.fn().mockResolvedValue({
      markets: [
        { code: 'CN', name: 'A股', optional: false, capabilities: ['quotes', 'klines', 'watchlist', 'holdings', 'search', 'news', 'alerts', 'analysis'], currency: 'CNY' },
        { code: 'AU', name: '澳股', optional: true, capabilities: ['quotes', 'klines', 'watchlist', 'holdings'], currency: 'AUD' },
      ],
    }),
  },
}))

describe('useEffectiveMarkets', () => {
  beforeEach(() => {
    resetEffectiveMarketsCacheForTests()
  })

  it('exposes effective market codes for selectors', async () => {
    const { result } = renderHook(() => useEffectiveMarkets())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.codes).toEqual(['CN', 'AU'])
    expect(result.current.capabilityCodes('watchlist')).toEqual(['CN', 'AU'])
    expect(result.current.capabilityCodes('search')).toEqual(['CN'])
  })

  it('falls back to core markets when the endpoint fails', async () => {
    const api = await import('@panwatch/api')
    vi.mocked(api.marketsApi.getEffectiveMarkets).mockRejectedValueOnce(new Error('backend down'))
    const { result } = renderHook(() => useEffectiveMarkets())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.codes).toEqual(['CN', 'HK', 'US'])
  })
})
