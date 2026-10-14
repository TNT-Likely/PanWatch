import { describe, expect, it } from 'vitest'
import type { QlibPrediction, StockItem } from '@panwatch/api'
import {
  cnSymbol,
  defaultOpportunityFilters,
  filterOpportunityCards,
  opportunityCards,
  savedOpportunityFilters,
} from '@/lib/opportunity-view'

const predictions: QlibPrediction[] = Array.from({ length: 8 }, (_, i) => ({
  symbol: `SH60000${i}`,
  market: 'CN',
  rank: i + 1,
  rank_percentile: 100 - (i * 100) / 7,
  raw_score: 1 - i / 2,
  rank_change: null,
  tradable: i !== 1,
  reference_close: 12,
  factor_inputs: { KMID: 0.1 },
  explanation_kind: 'processed_inputs',
}))
const stocks: StockItem[] = [
  { id: 1, symbol: '600000', name: 'Known stock', market: 'CN' },
  { id: 2, symbol: 'SH600001', name: 'Other stock', market: 'CN' },
  { id: 3, symbol: '600002', name: 'US collision', market: 'US' },
]
const options = {
  synthetic: false,
  topk: 2,
  stocks,
  positions: [{ stock_id: 1, quantity: 100 }],
  basket: [],
}
describe('opportunity display adapter', () => {
  it('recovers invalid persisted filters without reviving old score thresholds', () => {
    expect(savedOpportunityFilters(null)).toEqual(defaultOpportunityFilters)
    expect(
      savedOpportunityFilters({ source: 'market_scan', top: '70', search: 7 }),
    ).toEqual(defaultOpportunityFilters)
  })
  it('normalizes supported CN symbol formats for display joins', () => {
    expect(cnSymbol('sh600000')).toBe('600000')
    expect(cnSymbol('600000.SH')).toBe('600000')
    expect(cnSymbol('000001')).toBe('000001')
  })
  it('enriches names and real holding states without changing scores or rank', () => {
    const cards = opportunityCards(predictions.slice().reverse(), options)
    expect(cards[0]).toMatchObject({
      name: 'Known stock',
      watched: true,
      held: true,
      selected: true,
    })
    expect(cards[1]).toMatchObject({
      watched: true,
      held: false,
      selected: false,
    })
    expect(cards[2]).toMatchObject({
      name: null,
      watched: false,
      selected: true,
    })
    expect(cards.map((card) => card.prediction)).toEqual(predictions)
  })
  it('never attaches real names, positions or real follows to synthetic symbols', () => {
    const cards = opportunityCards(predictions, {
      ...options,
      synthetic: true,
      basket: [{ symbol: 'SH600003', run_id: 'demo' }],
    })
    expect(cards[0]).toMatchObject({ name: null, watched: false, held: null })
    expect(cards[3].watched).toBe(true)
  })
  it('keeps failed metadata unknown rather than inventing not-held status', () => {
    const cards = opportunityCards(predictions, {
      ...options,
      stocks: null,
      positions: null,
    })
    expect(cards[0]).toMatchObject({ name: null, watched: null, held: null })
    expect(
      filterOpportunityCards(cards, {
        ...defaultOpportunityFilters,
        holding: 'unheld',
      }),
    ).toEqual([])
  })
  it('filters by rank position in the entire frozen pool, not rescaled percentiles', () => {
    const cards = opportunityCards(predictions, options)
    expect(
      filterOpportunityCards(cards, {
        ...defaultOpportunityFilters,
        top: '10',
      }).map((card) => card.prediction.rank),
    ).toEqual([1])
    expect(
      filterOpportunityCards(cards, {
        ...defaultOpportunityFilters,
        top: '20',
      }).map((card) => card.prediction.rank),
    ).toEqual([1, 2])
    expect(
      filterOpportunityCards(cards, {
        ...defaultOpportunityFilters,
        top: '20',
        availability: 'tradable',
      }).map((card) => card.prediction.rank),
    ).toEqual([1])
  })
  it('applies watchlist, holdings, name/symbol and historical availability filters', () => {
    const cards = opportunityCards(predictions, options)
    expect(
      filterOpportunityCards(cards, {
        ...defaultOpportunityFilters,
        source: 'watched',
        holding: 'unheld',
      }).map((card) => card.symbol),
    ).toEqual(['600001'])
    expect(
      filterOpportunityCards(cards, {
        ...defaultOpportunityFilters,
        search: ' known STOCK ',
      }).map((card) => card.symbol),
    ).toEqual(['600000'])
    expect(
      filterOpportunityCards(cards, {
        ...defaultOpportunityFilters,
        search: 'SH600002',
      }).map((card) => card.symbol),
    ).toEqual(['600002'])
    expect(
      filterOpportunityCards(cards, {
        ...defaultOpportunityFilters,
        availability: 'blocked',
      }).map((card) => card.symbol),
    ).toEqual(['600001'])
  })
})
