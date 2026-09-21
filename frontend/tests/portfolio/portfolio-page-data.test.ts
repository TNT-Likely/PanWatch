import { describe, expect, it, vi } from 'vitest'

import {
  loadPortfolioPageBackgroundData,
  loadPortfolioPageCoreData,
  loadPortfolioPageQuoteData,
  buildPortfolioStockKeys,
} from '@/lib/portfolio-page-data'

describe('portfolio page loading', () => {
  it('resolves core data without waiting for background lanes', async () => {
    const signal = new AbortController().signal
    const api = {
      loadStocks: vi.fn().mockResolvedValue([{ symbol: '600519', market: 'CN' }]),
      loadPortfolio: vi.fn().mockResolvedValue({ accounts: [{ positions: [] }] }),
      loadMarketStatus: vi.fn(),
      buildQuoteItems: vi.fn(),
      loadQuotes: vi.fn(),
      loadSuggestions: vi.fn(),
      loadPriceAlerts: vi.fn(),
      loadKlines: vi.fn(),
    }

    const result = await loadPortfolioPageCoreData(api, signal)

    expect(result).toEqual({
      stocks: [{ symbol: '600519', market: 'CN' }],
      portfolio: { accounts: [{ positions: [] }] },
    })
    expect(api.loadMarketStatus).not.toHaveBeenCalled()
    expect(api.loadQuotes).not.toHaveBeenCalled()
    expect(api.loadKlines).not.toHaveBeenCalled()
  })

  it('runs background lanes after core data and passes the same signal', async () => {
    const signal = new AbortController().signal
    const items = [{ symbol: '600519', market: 'CN' }]
    const api = {
      loadMarketStatus: vi.fn().mockResolvedValue([{ code: 'CN' }]),
      buildQuoteItems: vi.fn().mockReturnValue(items),
      loadSuggestions: vi.fn().mockResolvedValue({}),
      loadPriceAlerts: vi.fn().mockResolvedValue({}),
      loadKlines: vi.fn().mockResolvedValue({ 'CN:600519': { trend: '多头排列' } }),
    }
    const stocks = [{ symbol: '600519', market: 'CN' }]
    const portfolio = { accounts: [{ positions: [] }] }

    const result = await loadPortfolioPageBackgroundData(api, stocks, portfolio, signal)

    expect(api.buildQuoteItems).toHaveBeenCalledWith(stocks, portfolio)
    expect(api.loadMarketStatus).toHaveBeenCalledWith(signal)
    expect(api.loadSuggestions).toHaveBeenCalledWith(items, signal)
    expect(api.loadPriceAlerts).toHaveBeenCalledWith(items, signal)
    expect(api.loadKlines).toHaveBeenCalledWith(items, signal)
    expect(result).toEqual({
      marketStatus: [{ code: 'CN' }],
      suggestions: {},
      priceAlerts: {},
      klines: { 'CN:600519': { trend: '多头排列' } },
    })
  })

  it('loads quotes as the priority lane before the page is revealed', async () => {
    const signal = new AbortController().signal
    const items = [{ symbol: '600519', market: 'CN' }]
    const api = {
      buildQuoteItems: vi.fn().mockReturnValue(items),
      loadQuotes: vi.fn().mockResolvedValue([{ symbol: '600519', market: 'CN' }]),
    }

    const result = await loadPortfolioPageQuoteData(
      api,
      [{ symbol: '600519', market: 'CN' }],
      { accounts: [] },
      signal,
    )

    expect(api.buildQuoteItems).toHaveBeenCalledWith(
      [{ symbol: '600519', market: 'CN' }],
      { accounts: [] },
    )
    expect(api.loadQuotes).toHaveBeenCalledWith(items, signal)
    expect(result).toEqual({ quotes: [{ symbol: '600519', market: 'CN' }] })
  })

  it('formats suggestion stock keys as market then symbol', () => {
    expect(buildPortfolioStockKeys([
      { symbol: '600519', market: 'CN' },
      { symbol: '00700', market: 'HK' },
    ])).toBe('CN:600519,HK:00700')
  })
})
