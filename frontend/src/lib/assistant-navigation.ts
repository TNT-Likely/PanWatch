export interface AssistantPortfolioTarget {
  view: 'kline'
  symbol: string
  market: 'CN' | 'HK' | 'US'
}

const SYMBOL_PATTERN = /^[A-Za-z0-9.]{1,32}$/
const MARKETS = new Set<AssistantPortfolioTarget['market']>(['CN', 'HK', 'US'])

export function parseAssistantPortfolioTarget(
  params: URLSearchParams,
): AssistantPortfolioTarget | null {
  if (params.get('view') !== 'kline') return null
  const symbol = (params.get('symbol') || '').trim().toUpperCase()
  const market = (params.get('market') || '').trim().toUpperCase()
  if (!SYMBOL_PATTERN.test(symbol) || !MARKETS.has(market as AssistantPortfolioTarget['market'])) {
    return null
  }
  return { view: 'kline', symbol, market: market as AssistantPortfolioTarget['market'] }
}
