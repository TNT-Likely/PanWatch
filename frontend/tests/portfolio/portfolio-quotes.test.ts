import { describe, expect, it } from 'vitest'
import { mergePortfolioQuotes, toQuoteMap, type PortfolioSummary, type Position } from '@/lib/portfolio-quotes'
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
