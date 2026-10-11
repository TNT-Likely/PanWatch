import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchAPI } from '@panwatch/api'
import { MARKET_CATEGORIES, matchesMarketCategory, searchStocksByMarketCategory } from '@/lib/market-categories'

vi.mock('@panwatch/api', () => ({ fetchAPI: vi.fn() }))

beforeEach(() => vi.resetAllMocks())

describe('presentation market categories', () => {
  it('has one Canada category and keeps exchange filters backwards compatible', () => {
    expect(MARKET_CATEGORIES).toEqual(['CN', 'HK', 'US', 'CA'])
    expect(['TSX', 'TSXV', 'US', 'HK', 'CN'].filter(market => matchesMarketCategory(market, 'CA'))).toEqual(['TSX', 'TSXV'])
    expect(matchesMarketCategory('TSXV', 'TSXV')).toBe(true)
    expect(matchesMarketCategory('TSX', 'TSXV')).toBe(false)
    expect(matchesMarketCategory('TSXV', '')).toBe(true)
  })

  it('queries both real exchanges with one cancellation signal and preserves identities and asset types', async () => {
    const tsx = { market: 'TSX', symbol: 'V', name: 'TSX listing', asset_type: 'etf', currency: 'CAD' }
    const venture = { market: 'TSXV', symbol: 'V', name: 'Venture listing', asset_type: 'stock', currency: 'CAD' }
    vi.mocked(fetchAPI).mockResolvedValueOnce([tsx, tsx]).mockResolvedValueOnce([venture, { market: 'US', symbol: 'V' }])
    const signal = new AbortController().signal
    const response = await searchStocksByMarketCategory('V & name', 'CA', { signal })
    expect(fetchAPI).toHaveBeenNthCalledWith(1, '/stocks/search?q=V%20%26%20name&market=TSX', { signal })
    expect(fetchAPI).toHaveBeenNthCalledWith(2, '/stocks/search?q=V%20%26%20name&market=TSXV', { signal })
    expect(response).toEqual({ results: [tsx, venture], failedMarkets: [] })
    expect(response.results[0]).toBe(tsx)
    expect(response.results[1]).toBe(venture)
  })

  it.each(['', 'CN', 'HK', 'US', 'TSX', 'TSXV'])('keeps the existing %s API request unchanged', async category => {
    vi.mocked(fetchAPI).mockResolvedValue([])
    await searchStocksByMarketCategory('SHOP.TO', category)
    expect(fetchAPI).toHaveBeenCalledOnce()
    expect(fetchAPI).toHaveBeenCalledWith(`/stocks/search?q=SHOP.TO${category ? `&market=${category}` : ''}`, undefined)
  })

  it.each(['TSX', 'TSXV'])('retains the healthy exchange and reports a partial %s failure', async failedMarket => {
    vi.mocked(fetchAPI).mockImplementation(async path => {
      const market = new URL(path, 'https://example.test').searchParams.get('market')
      if (market === failedMarket) throw new Error('unavailable')
      return [{ market, symbol: 'V' }]
    })
    expect(await searchStocksByMarketCategory('V', 'CA')).toEqual({
      results: [{ market: failedMarket === 'TSX' ? 'TSXV' : 'TSX', symbol: 'V' }],
      failedMarkets: [failedMarket],
    })
  })

  it('rejects complete failure so the UI cannot confuse it with no matches', async () => {
    vi.mocked(fetchAPI).mockRejectedValue(new Error('unavailable'))
    await expect(searchStocksByMarketCategory('V', 'CA')).rejects.toThrow('unavailable')
  })
})
