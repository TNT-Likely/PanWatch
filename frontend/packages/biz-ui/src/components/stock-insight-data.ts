import { instrumentIdentity } from '../instrument-identity'

export function reportSymbolQueries(symbol: string, market: string): string[] {
  const target = instrumentIdentity(symbol, market)
  if (!target) return []
  return [...new Set([
    ...(market === 'TSX' || market === 'TSXV' ? [`${market}:${target.symbol}`] : []),
    target.symbol,
    symbol,
  ])]
}

interface ScopedReport {
  stock_symbol: string
  context_payload?: Record<string, unknown> | null
  context_summary?: Record<string, unknown> | null
  suggestions?: Record<string, unknown> | null
}

/** Bare alphabetic tickers and free text cannot distinguish US/Canadian listings. */
export function reportMatchesStock(report: ScopedReport, symbol: string, market: string): boolean {
  const target = instrumentIdentity(symbol, market)
  if (!target) return false
  const direct = report.stock_symbol !== '*'
  const row = direct ? instrumentIdentity(report.stock_symbol) : null
  if (direct && row?.market && row.market !== target.market) return false
  if (direct && instrumentIdentity(report.stock_symbol, target.market)?.symbol !== target.symbol) return false

  for (const source of [report.context_payload, report.context_summary, report.suggestions]) {
    let matching = false
    let conflicting = false
    for (const [key, value] of Object.entries(source || {})) {
      const item = value && typeof value === 'object' ? value as Record<string, unknown> : {}
      const recordedMarket = typeof item.market === 'string' ? item.market : null
      const identity = instrumentIdentity(key, recordedMarket)
      // A contradictory qualified key and payload is never trustworthy.
      if (!identity) {
        if (instrumentIdentity(key, target.market)?.symbol === target.symbol) conflicting = true
        continue
      }
      const comparable = instrumentIdentity(identity.symbol, target.market)
      if (comparable?.symbol !== target.symbol) continue
      if (identity.market) {
        if (identity.market === target.market) matching = true
        else if (!(direct && row?.market && instrumentIdentity(key)?.market)) conflicting = true
      }
    }
    if (conflicting && (direct || !matching)) return false
    if (matching) return true
  }
  if (row?.market === target.market) return true
  // Retain numeric legacy mainland/HK records; never infer US from a bare ticker.
  if (!((target.market === 'CN' && /^\d{6}$/.test(target.symbol))
    || (target.market === 'HK' && /^\d{5}$/.test(target.symbol)))) return false
  return direct || Object.keys(report.suggestions || {}).some(key => key === target.symbol)
}

export function reportSuggestionForStock(report: ScopedReport, symbol: string, market: string): Record<string, unknown> | null {
  const target = instrumentIdentity(symbol, market)
  if (!target) return null
  for (const [key, value] of Object.entries(report.suggestions || {})) {
    if (!value || typeof value !== 'object') continue
    const suggestion = value as Record<string, unknown>
    const recordedMarket = typeof suggestion.market === 'string' ? suggestion.market : null
    const nativeAlias = !recordedMarket ? instrumentIdentity(key, target.market) : null
    const identity = instrumentIdentity(key, recordedMarket)
      || (nativeAlias ? { symbol: nativeAlias.symbol, market: null } : null)
    if (!identity || (identity.market && identity.market !== target.market)) continue
    const candidate = {
      ...report,
      stock_symbol: identity.market ? `${identity.market}:${identity.symbol}` : key,
      suggestions: { [key]: suggestion },
    }
    if (reportMatchesStock(candidate, symbol, market)) return suggestion
  }
  return null
}

export interface InsightPortfolioPosition {
  symbol: string
  market: string
  quantity: number
  cost_price: number
  currency?: string | null
  market_value?: number | null
  market_value_cny: number | null
  pnl: number | null
  valuation_status?: string
}

export interface InsightHolding {
  quantity: number
  unitCost: number | null
  currency: string | null
  nativeMarketValue: number | null
  marketValue: number | null
  pnl: number | null
  valuationIncomplete: boolean
}

export function aggregateInsightHolding(
  positions: InsightPortfolioPosition[], symbol: string, market: string,
): InsightHolding | null {
  const target = instrumentIdentity(symbol, market)
  if (!target) return null
  const matching = positions.filter(position => {
    const identity = instrumentIdentity(position.symbol, position.market)
    return identity?.market === target.market && identity.symbol === target.symbol && position.quantity > 0
  })
  if (!matching.length) return null
  const sum = (values: Array<number | null | undefined>) => values.every(value => value != null && Number.isFinite(value))
    ? values.reduce<number>((total, value) => total + value!, 0) : null
  const quantity = matching.reduce((total, position) => total + position.quantity, 0)
  const currencies = new Set(matching.map(position => position.currency?.trim().toUpperCase()
    || (market === 'CN' ? 'CNY' : market === 'HK' ? 'HKD' : market === 'US' ? 'USD' : null)))
  const sameCurrency = currencies.size === 1
  const currency = sameCurrency ? [...currencies][0] : null
  const cost = sum(matching.map(position => position.cost_price * position.quantity))
  const marketValue = sum(matching.map(position => position.market_value_cny))
  const pnl = sum(matching.map(position => position.pnl))
  return {
    quantity,
    unitCost: sameCurrency && cost != null ? cost / quantity : null,
    currency,
    nativeMarketValue: sameCurrency ? sum(matching.map(position => position.market_value)) : null,
    marketValue,
    pnl,
    valuationIncomplete: !currency || marketValue == null || pnl == null || matching.some(position =>
      position.valuation_status != null && position.valuation_status !== 'available'),
  }
}
