import { fetchAPI } from '@panwatch/api'

// Country/region categories are presentation-only. Persisted securities and API
// requests keep their real exchange (in particular, TSX versus TSXV).
export const MARKET_CATEGORIES = ['CN', 'HK', 'US', 'CA'] as const
export type MarketCategory = typeof MARKET_CATEGORIES[number]

export function matchesMarketCategory(market: string, category: string): boolean {
  if (!category) return true
  return category === 'CA' ? market === 'TSX' || market === 'TSXV' : market === category
}

export async function searchStocksByMarketCategory<T extends { market: string; symbol: string }>(
  query: string,
  category: string,
  options?: RequestInit,
): Promise<{ results: T[]; failedMarkets: string[] }> {
  const markets = category === 'CA' ? ['TSX', 'TSXV'] : [category]
  const batches = await Promise.allSettled(markets.map(market => fetchAPI<T[]>(
    `/stocks/search?q=${encodeURIComponent(query)}${market ? `&market=${encodeURIComponent(market)}` : ''}`,
    options,
  )))
  const failedMarkets = markets.filter((_, index) => batches[index].status === 'rejected')
  if (failedMarkets.length === markets.length) {
    throw (batches[0] as PromiseRejectedResult).reason
  }
  const seen = new Set<string>()
  const results = batches.flatMap(batch => batch.status === 'fulfilled' ? batch.value : []).filter(stock => {
    // Never collapse identical ticker strings from different exchanges.
    const key = `${stock.market}:${stock.symbol}`
    if (!matchesMarketCategory(stock.market, category) || seen.has(key)) return false
    seen.add(key)
    return true
  })
  return { results, failedMarkets }
}
