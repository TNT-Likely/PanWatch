import { describe, expect, it, vi } from 'vitest'

import { loadPortfolioPageData } from '@/lib/portfolio-page-data'

describe('loadPortfolioPageData', () => {
  it('loads core data once and runs each follow-up lane once', async () => {
    const signal = new AbortController().signal
    const api = {
      loadStocks: vi.fn().mockResolvedValue([{ symbol: '600519', market: 'CN' }]),
      loadPortfolio: vi.fn().mockResolvedValue({ accounts: [{ positions: [] }] }),
      loadMarketStatus: vi.fn().mockResolvedValue([{ code: 'CN' }]),
      buildQuoteItems: vi.fn().mockReturnValue([{ symbol: '600519', market: 'CN' }]),
      loadQuotes: vi.fn().mockResolvedValue([{ symbol: '600519', market: 'CN' }]),
      loadSuggestions: vi.fn().mockResolvedValue({}),
      loadPriceAlerts: vi.fn().mockResolvedValue({}),
      loadKlines: vi.fn().mockResolvedValue({ 'CN:600519': { trend: '多头排列' } }),
    }

    const result = await loadPortfolioPageData(api, signal)

    expect(api.loadStocks).toHaveBeenCalledTimes(1)
    expect(api.loadPortfolio).toHaveBeenCalledTimes(1)
    expect(api.loadMarketStatus).toHaveBeenCalledTimes(1)
    expect(api.loadQuotes).toHaveBeenCalledTimes(1)
    expect(api.loadSuggestions).toHaveBeenCalledTimes(1)
    expect(api.loadPriceAlerts).toHaveBeenCalledTimes(1)
    expect(api.loadKlines).toHaveBeenCalledTimes(1)
    expect(api.loadPriceAlerts).toHaveBeenCalledWith([{ symbol: '600519', market: 'CN' }], signal)
    expect(result.stocks).toEqual([{ symbol: '600519', market: 'CN' }])
  })

  it('passes the same abort signal through every request lane', async () => {
    const signal = new AbortController().signal
    const api = {
      loadStocks: vi.fn().mockResolvedValue([]),
      loadPortfolio: vi.fn().mockResolvedValue({ accounts: [] }),
      loadMarketStatus: vi.fn().mockResolvedValue([]),
      buildQuoteItems: vi.fn().mockReturnValue([]),
      loadQuotes: vi.fn().mockResolvedValue([]),
      loadSuggestions: vi.fn().mockResolvedValue({}),
      loadPriceAlerts: vi.fn().mockResolvedValue({}),
      loadKlines: vi.fn().mockResolvedValue({}),
    }

    await loadPortfolioPageData(api, signal)

    expect(api.loadStocks).toHaveBeenCalledWith(signal)
    expect(api.loadPortfolio).toHaveBeenCalledWith(signal)
    expect(api.loadMarketStatus).toHaveBeenCalledWith(signal)
    expect(api.loadQuotes).toHaveBeenCalledWith([], signal)
    expect(api.loadSuggestions).toHaveBeenCalledWith([], signal)
    expect(api.loadPriceAlerts).toHaveBeenCalledWith([], signal)
    expect(api.loadKlines).toHaveBeenCalledWith([], signal)
  })
})
