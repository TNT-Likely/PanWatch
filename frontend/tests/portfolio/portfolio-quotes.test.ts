import { describe, expect, it } from 'vitest'
import { applyMarketStatuses, findDisplayQuote, mergePortfolioQuotes, toQuoteMap, type PortfolioSummary, type Position } from '@/lib/portfolio-quotes'
import { marketSignTextClass } from '@/lib/market-colors'

const position = (market = 'CN', symbol = '600519'): Position => ({
  id: 1, stock_id: 1, symbol, name: symbol, market,
  cost_price: 8, quantity: 100, invested_amount: null, trading_style: '',
  current_price: 10, current_price_cny: 10, change_pct: 5,
  market_value: 1000, market_value_cny: 1000, pnl: 200, pnl_pct: 25,
  daily_pnl: 50, daily_pnl_pct: 5, exchange_rate: null,
})

const portfolio = (positions = [position()]): PortfolioSummary => ({
  accounts: [{ id: 1, name: 'Account', available_funds: 0, total_market_value: 1000,
    total_cost: 800, total_pnl: 200, total_pnl_pct: 25, total_daily_pnl: 50,
    total_assets: 1000, positions }],
  total: { total_market_value: 1000, total_cost: 800, total_pnl: 200,
    total_pnl_pct: 25, total_daily_pnl: 50, available_funds: 0, total_assets: 1000 },
  exchange_rates: { HKD_CNY: 1, USD_CNY: 1 },
})

describe('daily portfolio quote semantics', () => {
  it('keeps last prices and accumulated P&L while holiday daily figures are neutral', () => {
    const merged = mergePortfolioQuotes(portfolio(), toQuoteMap([{
      market: 'CN', symbol: '600519', current_price: 11, change_pct: null,
      daily_move_status: 'closed', quote_date: '2026-09-30',
    }]))!
    const row = merged.accounts[0].positions[0]
    expect(row.current_price).toBe(11)
    expect(row.pnl).toBe(300)
    expect(row.change_pct).toBeNull()
    expect(row.daily_pnl).toBe(0)
    expect(merged.total.total_daily_pnl).toBe(0)
    expect(marketSignTextClass(row.change_pct)).toBe('text-market-flat')
    expect(marketSignTextClass(row.daily_pnl)).toBe('text-market-flat')
  })

  it('does not restore old changes when a refreshed quote is explicitly missing or stale', () => {
    for (const status of ['stale', 'missing']) {
      const row = mergePortfolioQuotes(portfolio(), {
        'CN:600519': { current_price: 10, change_pct: null, daily_move_status: status },
      })!.accounts[0].positions[0]
      expect(row.change_pct).toBeNull()
      expect(row.daily_pnl).toBeNull()
    }
  })

  it('only aggregates active markets in a mixed portfolio', () => {
    const merged = mergePortfolioQuotes(portfolio([position(), position('US', 'AAPL')]), {
      'CN:600519': { current_price: 10, change_pct: null, daily_move_status: 'closed' },
      'US:AAPL': { current_price: 11, change_pct: 10, daily_move_status: 'current' },
    })!
    expect(merged.total.total_daily_pnl).toBe(100)
  })
})


describe('market status transitions without new quotes', () => {
  it('neutralizes yesterday’s quote on a holiday without changing its price or the cached source', () => {
    const quotes = toQuoteMap([{ market: 'CN', symbol: '600519', current_price: 10, change_pct: 5,
      daily_move_status: 'current', quote_date: '2026-09-30' }])
    const displayed = applyMarketStatuses(quotes, [{ code: 'CN', status: 'closed', local_date: '2026-10-01' }])
    expect(displayed['CN:600519']).toMatchObject({ current_price: 10, change_pct: null, daily_move_status: 'closed' })
    expect(quotes['CN:600519'].change_pct).toBe(5)
    expect(mergePortfolioQuotes(portfolio(), displayed)!.total.total_daily_pnl).toBe(0)
  })

  it('keeps final same-day movement after close and rejects a prior-day quote after reopening', () => {
    const quotes = toQuoteMap([{ market: 'US', symbol: 'AAPL', current_price: 10, change_pct: 5,
      daily_move_status: 'current', quote_date: '2026-10-02' }])
    expect(applyMarketStatuses(quotes, [{ code: 'US', status: 'after_hours', local_date: '2026-10-02' }])['US:AAPL'].change_pct).toBe(5)
    expect(applyMarketStatuses(quotes, [{ code: 'US', status: 'trading', local_date: '2026-10-05' }])['US:AAPL'])
      .toMatchObject({ change_pct: null, daily_move_status: 'stale' })
  })
})


describe('Canadian portfolio valuation', () => {
  it.each(['TSX', 'TSXV'])('converts %s fractional holdings using CAD, not USD', market => {
    const source = portfolio([{ ...position(market, 'REI-UN'), currency: 'CAD', quantity: 1.25 }])
    source.exchange_rates = { HKD_CNY: 1, USD_CNY: 7, CAD_CNY: 5 }
    const merged = mergePortfolioQuotes(source, {})!
    expect(merged.accounts[0].positions[0]).toMatchObject({
      quantity: 1.25, market_value: 12.5, market_value_cny: 62.5, pnl: 12.5,
      exchange_rate: 5, valuation_status: 'available',
    })
    expect(merged.total.total_cost).toBe(50)
    expect(merged.total.valuation_incomplete).toBe(false)
  })

  it.each([null, undefined, 0, -1, NaN])('does not invent CAD FX when the rate is %s', CAD_CNY => {
    const source = portfolio([{ ...position('TSX', 'ZSP'), currency: 'CAD' }, position()])
    source.exchange_rates = { HKD_CNY: 1, USD_CNY: 7, CAD_CNY }
    const merged = mergePortfolioQuotes(source, {})!
    expect(merged.accounts[0].positions[0]).toMatchObject({
      market_value: 1000, market_value_cny: null, current_price_cny: null,
      pnl: null, daily_pnl: null, exchange_rate: null, valuation_status: 'fx_unavailable',
    })
    expect(merged.accounts[0].valuation_incomplete).toBe(true)
    expect(merged.total.valuation_incomplete).toBe(true)
    expect(merged.total.total_market_value).toBe(1000)
    expect(merged.total.total_cost).toBe(800)
  })
})


it('uses verified position FX for a Canadian USD class without a market fallback', () => {
  const source = portfolio([{ ...position('TSX', 'TEST-U'), currency: 'USD', exchange_rate: 7 }])
  source.exchange_rates = { HKD_CNY: 1, USD_CNY: 7, CAD_CNY: 5 }
  const merged = mergePortfolioQuotes(source, toQuoteMap([{
    market: 'TSX', symbol: 'TEST-U', current_price: 10, change_pct: 5, currency: 'USD',
  }]))!
  expect(merged.accounts[0].positions[0]).toMatchObject({ currency: 'USD', exchange_rate: 7, market_value_cny: 7000 })
  const unknown = mergePortfolioQuotes(source, toQuoteMap([{
    market: 'TSX', symbol: 'TEST-U', current_price: 10, change_pct: 5, currency: null,
  }]))!
  expect(unknown.accounts[0].positions[0]).toMatchObject({ currency: null, market_value_cny: null })
  expect(unknown.total.valuation_incomplete).toBe(true)
})


it.each(['TSX', 'TSXV'])('never uses legacy USD_CNY for %s USD classes without verified position FX', market => {
  const source = portfolio([{ ...position(market, 'TEST-U'), currency: 'USD', exchange_rate: null }])
  source.exchange_rates = { HKD_CNY: 1, USD_CNY: 7.25, CAD_CNY: 5 }
  const merged = mergePortfolioQuotes(source, toQuoteMap([{
    market, symbol: 'TEST-U', current_price: 10, change_pct: 5, currency: 'USD',
  }]))!
  expect(merged.accounts[0].positions[0]).toMatchObject({
    currency: 'USD', exchange_rate: null, market_value_cny: null, valuation_status: 'fx_unavailable',
  })
  expect(merged.total.valuation_incomplete).toBe(true)
})

it('does not reuse CAD FX when a refreshed Canadian quote reports USD', () => {
  const source = portfolio([{ ...position('TSX', 'TEST-U'), currency: 'CAD', exchange_rate: 5 }])
  source.exchange_rates = { HKD_CNY: 1, USD_CNY: 7.25, CAD_CNY: 5 }
  const merged = mergePortfolioQuotes(source, toQuoteMap([{
    market: 'TSX', symbol: 'TEST-U', current_price: 10, change_pct: 5, currency: 'USD',
  }]))!
  expect(merged.accounts[0].positions[0]).toMatchObject({ exchange_rate: null, market_value_cny: null })
})


it('keeps known costs but excludes unpriced Canadian holdings from P&L and its denominator', () => {
  const source = portfolio([
    { ...position('TSX', 'ZSP'), currency: 'CAD', current_price: null },
    { ...position('TSXV', 'TEST'), currency: 'CAD' },
  ])
  source.exchange_rates = { HKD_CNY: 1, USD_CNY: 7.25, CAD_CNY: 5 }
  const merged = mergePortfolioQuotes(source, {})!
  expect(merged.accounts[0].positions[0]).toMatchObject({ pnl: null, valuation_status: 'quote_unavailable' })
  expect(merged.accounts[0]).toMatchObject({ total_cost: 8000, total_market_value: 5000, total_pnl: 1000, total_pnl_pct: 25, valuation_incomplete: true })
  expect(merged.total).toMatchObject({ total_cost: 8000, total_market_value: 5000, total_pnl: 1000, total_pnl_pct: 25, valuation_incomplete: true })
})

it('retains provider delay and timestamp through batch and holding merges', () => {
  const source = portfolio([{ ...position('TSX', 'ZSP'), currency: 'CAD' }])
  source.exchange_rates = { HKD_CNY: 1, CAD_CNY: 5 }
  const quotes = toQuoteMap([{ market: 'TSX', symbol: 'ZSP', current_price: 10, change_pct: 5,
    currency: 'CAD', is_delayed: true, source_timestamp: '2026-10-09T19:45:00+00:00' }])
  expect(mergePortfolioQuotes(source, quotes)!.accounts[0].positions[0]).toMatchObject({
    is_delayed: true, source_timestamp: '2026-10-09T19:45:00+00:00',
  })
})


it('values metadata-only Canadian USD positions once verified reference FX refreshes', () => {
  const source = portfolio([{
    ...position('TSX', 'TEST-U'), currency: null, current_price: null, exchange_rate: null,
  }])
  source.exchange_rates = { HKD_CNY: 1, USD_CNY: 7.25, CAD_CNY: null, CANADA_USD_CNY: null }
  const quotes = toQuoteMap([{
    market: 'TSX', symbol: 'TEST-U', current_price: 10, change_pct: 5, currency: 'USD',
  }])
  expect(mergePortfolioQuotes(source, quotes)!.total.valuation_incomplete).toBe(true)
  source.exchange_rates.CANADA_USD_CNY = 7.1
  const merged = mergePortfolioQuotes(source, quotes)!
  expect(merged.accounts[0].positions[0]).toMatchObject({
    currency: 'USD', exchange_rate: 7.1, current_price: 10, market_value_cny: 7100,
    valuation_status: 'available',
  })
  expect(merged.total).toMatchObject({ valuation_incomplete: false, total_pnl: 1420, total_pnl_pct: 25 })
})

it('keeps dual-listed symbols in separate market-qualified quote slots', () => {
  const source = portfolio([
    { ...position('TSX', 'RY'), currency: 'CAD' },
    position('US', 'RY'),
  ])
  source.exchange_rates = { HKD_CNY: 1, USD_CNY: 7, CAD_CNY: 5 }
  const quotes = toQuoteMap([
    { market: 'TSX', symbol: 'RY', current_price: 20, change_pct: 1, currency: 'CAD' },
    { market: 'US', symbol: 'RY', current_price: 15, change_pct: 2, currency: 'USD' },
  ])
  const rows = mergePortfolioQuotes(source, quotes)!.accounts[0].positions
  expect(rows[0]).toMatchObject({ current_price: 20, market_value_cny: 10000 })
  expect(rows[1]).toMatchObject({ current_price: 15, market_value_cny: 10500 })
})

it('marks missing quotes incomplete for legacy markets without fabricating losses', () => {
  const merged = mergePortfolioQuotes(portfolio([{ ...position('US', 'RY'), current_price: null }]), {})!
  expect(merged.accounts[0].positions[0]).toMatchObject({ pnl: null, valuation_status: 'quote_unavailable' })
  expect(merged.total).toMatchObject({ total_pnl: 0, total_pnl_pct: 0, valuation_incomplete: true })
})


it('clears an older Canadian price when a present quote explicitly reports unavailable', () => {
  const source = portfolio([{ ...position('TSX', 'ZSP'), currency: 'CAD' }])
  source.exchange_rates = { HKD_CNY: 1, CAD_CNY: 5 }
  const merged = mergePortfolioQuotes(source, toQuoteMap([{
    market: 'TSX', symbol: 'ZSP', currency: 'CAD', current_price: null, change_pct: null,
    daily_move_status: 'missing',
  }]))!
  expect(merged.accounts[0].positions[0]).toMatchObject({ current_price: null, market_value: null,
    market_value_cny: null, pnl: null, valuation_status: 'quote_unavailable' })
  expect(merged.total).toMatchObject({ valuation_incomplete: true, total_pnl: 0 })
  expect(mergePortfolioQuotes(source, {})!.accounts[0].positions[0].current_price).toBe(10)
})

it('clears expired Canadian USD FX when the latest reference rate is explicitly null', () => {
  const source = portfolio([{ ...position('TSX', 'TEST-U'), currency: 'USD', exchange_rate: 7 }])
  source.exchange_rates = { HKD_CNY: 1, USD_CNY: 7.25, CANADA_USD_CNY: null }
  const merged = mergePortfolioQuotes(source, {})!
  expect(merged.accounts[0].positions[0]).toMatchObject({ exchange_rate: null,
    market_value_cny: null, valuation_status: 'fx_unavailable' })
  expect(merged.total.valuation_incomplete).toBe(true)
})

it('normalizes backend delayed aliases and provider timestamps without requiring portfolio metadata', () => {
  const source = portfolio([{ ...position('TSX', 'ZSP'), current_price: null, currency: null }])
  source.exchange_rates = { HKD_CNY: 1, CAD_CNY: 5 }
  const quote = { market: 'TSX', symbol: 'ZSP', current_price: 10, change_pct: 1,
    currency: 'CAD', delayed: true, source_timestamp: '2026-10-09T19:45:00+00:00' }
  const rows = mergePortfolioQuotes(source, toQuoteMap([quote]))!.accounts[0].positions
  expect(rows[0]).toMatchObject({ is_delayed: true, source_timestamp: quote.source_timestamp,
    valuation_status: 'available', current_price: 10 })
})

it('prefers market-qualified watchlist quotes and uses bare aliases only for unique symbols', () => {
  const cad = { current_price: 20, change_pct: 1 }
  const usd = { current_price: 15, change_pct: 2 }
  expect(findDisplayQuote({ 'TSX:RY': cad, RY: usd }, 'TSX', 'RY', true)).toBe(cad)
  expect(findDisplayQuote({ RY: usd }, 'TSX', 'RY')).toBeNull()
  expect(findDisplayQuote({ ZSP: cad }, 'TSX', 'ZSP', true)).toBe(cad)
})
