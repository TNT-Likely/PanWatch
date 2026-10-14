import type { QlibBasketItem, QlibPrediction, StockItem } from '@panwatch/api'

export interface OpportunityPosition {
  stock_id: number
  quantity: number
}
export interface OpportunityCard {
  prediction: QlibPrediction
  symbol: string
  name: string | null
  watched: boolean | null
  held: boolean | null
  selected: boolean
}
export interface OpportunityFilters {
  source: string
  holding: string
  top: string
  availability: string
  search: string
}
export const defaultOpportunityFilters: OpportunityFilters = {
  source: 'all',
  holding: 'all',
  top: 'all',
  availability: 'all',
  search: '',
}

export function savedOpportunityFilters(value: unknown): OpportunityFilters {
  if (!value || typeof value !== 'object')
    return { ...defaultOpportunityFilters }
  const candidate = value as Record<string, unknown>
  const choices = {
    source: ['all', 'watched'],
    holding: ['all', 'held', 'unheld'],
    top: ['all', '10', '20', '50'],
    availability: ['all', 'tradable', 'blocked'],
  }
  const result = { ...defaultOpportunityFilters }
  for (const key of Object.keys(choices) as (keyof typeof choices)[]) {
    if (
      typeof candidate[key] === 'string' &&
      choices[key].includes(candidate[key])
    )
      result[key] = candidate[key]
  }
  if (typeof candidate.search === 'string')
    result.search = candidate.search.slice(0, 120)
  return result
}

// Display joins only: never modify Qlib ranking, infer risk, or join a demo symbol
// to a real company/holding. CN symbols elsewhere use the six-digit form.
export function cnSymbol(symbol: string) {
  return symbol
    .trim()
    .toUpperCase()
    .replace(/^(SH|SZ)/, '')
    .replace(/\.(SH|SZ)$/, '')
}
export function opportunityCards(
  predictions: QlibPrediction[],
  options: {
    synthetic: boolean
    topk: number
    stocks: StockItem[] | null
    positions: OpportunityPosition[] | null
    basket: QlibBasketItem[]
  },
): OpportunityCard[] {
  const securities = new Map(
    (options.stocks ?? [])
      .filter(
        (stock) =>
          stock.market.toUpperCase() === 'CN' && stock.asset_type !== 'etf',
      )
      .map((stock) => [cnSymbol(stock.symbol), stock]),
  )
  const selected = new Set(
    predictions
      .filter((item) => item.tradable)
      .slice()
      .sort((a, b) => a.rank - b.rank)
      .slice(0, options.topk)
      .map((item) => item.symbol),
  )
  return predictions
    .slice()
    .sort((a, b) => a.rank - b.rank)
    .map((prediction) => {
      const symbol = cnSymbol(prediction.symbol)
      const stock = securities.get(symbol)
      return {
        prediction,
        symbol,
        name: options.synthetic ? null : (stock?.name ?? null),
        watched: options.synthetic
          ? options.basket.some((item) => item.symbol === prediction.symbol)
          : options.stocks == null
            ? null
            : !!stock,
        held: options.synthetic
          ? null
          : options.stocks == null || options.positions == null
            ? null
            : !!stock &&
              options.positions.some(
                (item) => item.stock_id === stock.id && item.quantity > 0,
              ),
        selected: selected.has(prediction.symbol),
      }
    })
}
export function filterOpportunityCards(
  cards: OpportunityCard[],
  filters: OpportunityFilters,
) {
  const search = filters.search.trim().toLowerCase()
  const cutoff = Number(filters.top)
  return cards.filter((card) => {
    if (filters.source === 'watched' && card.watched !== true) return false
    if (filters.holding === 'held' && card.held !== true) return false
    if (filters.holding === 'unheld' && card.held !== false) return false
    if (filters.availability === 'tradable' && !card.prediction.tradable)
      return false
    if (filters.availability === 'blocked' && card.prediction.tradable)
      return false
    // Top-X means rank among the full frozen pool, not percentile rescaling
    // after other filters. At least one candidate remains for a small pool.
    if (
      Number.isFinite(cutoff) &&
      cutoff > 0 &&
      card.prediction.rank >
        Math.max(1, Math.ceil((cards.length * cutoff) / 100))
    )
      return false
    return (
      !search ||
      `${card.name ?? ''} ${card.symbol} ${card.prediction.symbol}`
        .toLowerCase()
        .includes(search)
    )
  })
}
