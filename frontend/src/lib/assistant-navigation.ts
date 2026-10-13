export interface AssistantPortfolioTarget {
  view: 'kline'
  symbol: string
  market: 'CN' | 'HK' | 'US' | 'AU'
}

const SYMBOL_PATTERN = /^[A-Za-z0-9.]{1,32}$/

export function parseAssistantPortfolioTarget(
  params: URLSearchParams,
  enabledMarkets?: readonly string[],
): AssistantPortfolioTarget | null {
  if (params.get('view') !== 'kline') return null
  const symbol = (params.get('symbol') || '').trim().toUpperCase()
  const market = (params.get('market') || '').trim().toUpperCase()
  if (!SYMBOL_PATTERN.test(symbol) || !(enabledMarkets ?? ['CN', 'HK', 'US']).includes(market)) {
    return null
  }
  return { view: 'kline', symbol, market: market as AssistantPortfolioTarget['market'] }
}
