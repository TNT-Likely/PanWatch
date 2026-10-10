export interface AssistantPortfolioTarget {
  view: 'kline'
  symbol: string
  market: 'CN' | 'HK' | 'US' | 'TSX' | 'TSXV'
}

const SYMBOL_PATTERN = /^[A-Za-z0-9.\-]{1,32}$/
const MARKETS = new Set<AssistantPortfolioTarget['market']>(['CN', 'HK', 'US', 'TSX', 'TSXV'])

export function parseAssistantPortfolioTarget(
  params: URLSearchParams,
): AssistantPortfolioTarget | null {
  if (params.get('view') !== 'kline') return null
  const symbol = (params.get('symbol') || '').trim().toUpperCase()
  const market = (params.get('market') || '').trim().toUpperCase()
  if (!SYMBOL_PATTERN.test(symbol) || !MARKETS.has(market as AssistantPortfolioTarget['market'])) {
    return null
  }
  if ((market === 'TSX' && symbol.endsWith('.V')) || (market === 'TSXV' && symbol.endsWith('.TO'))) return null
  const canonicalSymbol = market === 'TSX' ? symbol.replace(/\.TO$/, '') : market === 'TSXV' ? symbol.replace(/\.V$/, '') : symbol
  if (!SYMBOL_PATTERN.test(canonicalSymbol)) return null
  return { view: 'kline', symbol: canonicalSymbol, market: market as AssistantPortfolioTarget['market'] }
}
