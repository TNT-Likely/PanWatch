/** 股票外部行情链接生成，逻辑与后端 src/core/stock_link.py 对齐。 */

export type StockLinkPlatform = 'xueqiu'

const DEFAULT_PLATFORM: StockLinkPlatform = 'xueqiu'

function getCnExchange(symbol: string): 'SH' | 'SZ' | 'BJ' {
  const sym = String(symbol || '').trim()
  if (sym.startsWith('920') || sym.startsWith('83') || sym.startsWith('87') || sym.startsWith('88')) {
    return 'BJ'
  }
  if (sym.startsWith('5') || sym.startsWith('6') || sym.startsWith('900')) {
    return 'SH'
  }
  return 'SZ'
}

function buildXueqiuStockUrl(symbol: string, market: string): string {
  const sym = String(symbol || '').trim()
  const m = String(market || 'CN').trim().toUpperCase()
  if (m === 'US' || m === 'HK') {
    return `https://xueqiu.com/S/${sym}`
  }
  const prefix = getCnExchange(sym)
  return `https://xueqiu.com/S/${prefix}${sym}`
}

export function buildStockUrl(
  symbol: string,
  market: string,
  platform: StockLinkPlatform = DEFAULT_PLATFORM,
): string {
  if (platform === 'xueqiu') {
    return buildXueqiuStockUrl(symbol, market)
  }
  return buildXueqiuStockUrl(symbol, market)
}

export function getStockLinkPlatformLabel(platform: StockLinkPlatform = DEFAULT_PLATFORM): string {
  if (platform === 'xueqiu') return '雪球'
  return '雪球'
}
