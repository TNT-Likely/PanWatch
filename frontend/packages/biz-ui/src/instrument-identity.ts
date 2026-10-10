interface InstrumentIdentity {
  symbol: string
  market: string | null
}

const markets = new Set(['CN', 'HK', 'US', 'TSX', 'TSXV'])

// Keep Canadian aliases aligned with marketdata.Symbol, including preferred
// shares whose final .V is a class name rather than the venture exchange.
export function instrumentIdentity(raw: string, explicitMarket?: string | null): InstrumentIdentity | null {
  let symbol = String(raw || '').trim().toUpperCase()
  let market = explicitMarket?.trim().toUpperCase() || null
  if (market && !markets.has(market)) return null
  if (symbol.includes(':')) {
    const parts = symbol.split(':')
    if (parts.length !== 2 || !markets.has(parts[0]) || (market && market !== parts[0])) return null
    market = parts[0]
    symbol = parts[1]
  }
  const nativePreferred = /^[A-Z0-9][A-Z0-9-]*\.P[RF]\.[A-Z]+$/.test(symbol)
  if (nativePreferred && market !== 'TSX') return null
  const suffixMarket = nativePreferred ? null : symbol.endsWith('.TO') ? 'TSX' : symbol.endsWith('.V') ? 'TSXV' : null
  if (suffixMarket) {
    if (market && market !== suffixMarket) return null
    market = suffixMarket
    symbol = symbol.slice(0, suffixMarket === 'TSX' ? -3 : -2)
  }
  if (market === 'TSX' || market === 'TSXV') {
    const preferred = symbol.match(/^([A-Z0-9][A-Z0-9-]*)\.PR\.([A-Z]+)$/)
    if (preferred) {
      if (market !== 'TSX') return null
      symbol = `${preferred[1]}-P${preferred[2]}`
    } else if (/\.(?:PR|PF)(?:\.|$)/.test(symbol)) return null
    symbol = symbol.replace(/\./g, '-')
    if (!/^[A-Z0-9]+(?:-[A-Z0-9]+)*$/.test(symbol) || symbol.length > 20) return null
  }
  return /^[A-Z0-9.\-]+$/.test(symbol) ? { symbol, market } : null
}
