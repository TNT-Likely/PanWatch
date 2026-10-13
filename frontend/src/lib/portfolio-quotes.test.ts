import { describe, expect, it } from 'vitest'
import { mergePortfolioQuotes } from './portfolio-quotes'

const auPosition = {
  id: 1, stock_id: 1, symbol: 'VAS', name: 'VAS', market: 'AU',
  cost_price: 80, quantity: 10, invested_amount: null, trading_style: 'swing',
  current_price: null, change_pct: null, market_value: null, market_value_cny: null,
  pnl: null, pnl_pct: null, daily_pnl: null, daily_pnl_pct: null, currency: 'AUD',
}

const base = (pos: Record<string, unknown>, exchangeRates?: Record<string, number>) => ({
  accounts: [{
    id: 1, name: 'A', available_funds: 0, total_market_value: 0, total_cost: 0,
    total_pnl: 0, total_pnl_pct: 0, total_daily_pnl: 0, total_assets: 0, positions: [pos],
  }],
  total: {
    total_market_value: 0, total_cost: 0, total_pnl: 0, total_pnl_pct: 0,
    total_daily_pnl: 0, available_funds: 0, total_assets: 0,
  },
  exchange_rates: exchangeRates,
})

const quote = { 'AU:VAS': { current_price: 100, change_pct: 1, daily_move_status: 'current', quote_date: null } }

describe('mergePortfolioQuotes currency-keyed valuation', () => {
  it('values AU positions via AUD_CNY, never 1:1', () => {
    const merged = mergePortfolioQuotes(
      base(auPosition, { HKD_CNY: 0.92, USD_CNY: 7.25, AUD_CNY: 4.7 }) as never,
      quote,
    )
    const pos = merged!.accounts[0].positions[0]
    expect(pos.market_value).toBe(100 * 10)
    expect(pos.market_value_cny).toBeCloseTo(100 * 10 * 4.7, 6)
    expect(pos.exchange_rate).toBe(4.7)
    expect(merged!.total.valuation_complete).toBe(true)
  })

  it('marks AU incomplete when AUD_CNY missing (no hardcoded fallback)', () => {
    const merged = mergePortfolioQuotes(
      base(auPosition, { HKD_CNY: 0.92, USD_CNY: 7.25 }) as never,
      quote,
    )
    const pos = merged!.accounts[0].positions[0]
    expect(pos.market_value).toBe(100 * 10) //  native value kept
    expect(pos.market_value_cny).toBeNull()
    expect(pos.pnl).toBeNull()
    expect(merged!.total.valuation_complete).toBe(false)
    //  unvalued position excluded from CNY totals
    expect(merged!.total.total_market_value).toBe(0)
  })

  it('keeps the HKD fallback when the payload lacks HKD_CNY', () => {
    const hkPosition = { ...auPosition, symbol: '0700', name: 'Tencent', market: 'HK', currency: 'HKD' }
    const merged = mergePortfolioQuotes(
      base(hkPosition, { USD_CNY: 7.25 }) as never,
      { 'HK:0700': { current_price: 100, change_pct: 1, daily_move_status: 'current', quote_date: null } },
    )
    const pos = merged!.accounts[0].positions[0]
    expect(pos.market_value_cny).toBeCloseTo(100 * 10 * 0.92, 6)
  })

  it('keeps CN positions at rate 1 without exchange rates', () => {
    const cnPosition = { ...auPosition, symbol: '600519', name: 'Moutai', market: 'CN', currency: 'CNY' }
    const merged = mergePortfolioQuotes(
      base(cnPosition) as never,
      { 'CN:600519': { current_price: 100, change_pct: 1, daily_move_status: 'current', quote_date: null } },
    )
    const pos = merged!.accounts[0].positions[0]
    expect(pos.market_value_cny).toBe(100 * 10)
    expect(merged!.total.valuation_complete).toBe(true)
  })
})
