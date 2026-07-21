export interface MarketBadgeInfo {
  style: string
  label: string
}

export function getMarketBadge(market: string): MarketBadgeInfo {
  if (market === 'HK') return { style: 'bg-orange-500/10 text-orange-600', label: '港股' }
  if (market === 'US') return { style: 'bg-green-500/10 text-green-600', label: '美股' }
  if (market === 'FUND') return { style: 'bg-purple-500/10 text-purple-600', label: '基金' }
  return { style: 'bg-blue-500/10 text-blue-600', label: 'A股' }
}

/**
 * 判断基金类型：ETF（场内）或 场外
 * ETF场内基金代码规则：
 * - 上交所ETF: 51xxxx, 56xxxx, 58xxxx
 * - 深交所ETF: 15xxxx, 16xxxx
 */
export function getFundType(code: string): 'ETF' | '场外' {
  const c = (code || '').trim()
  if (c.startsWith('51') || c.startsWith('56') || c.startsWith('58')) return 'ETF'
  if (c.startsWith('15') || c.startsWith('16')) return 'ETF'
  return '场外'
}

/**
 * 获取股票/基金的详细交易所标识
 */
export function getExchangeLabel(code: string, market: string): string {
  if (market === 'HK') return '港股'
  if (market === 'US') return '美股'
  if (market === 'FUND') return getFundType(code)
  // A股判断
  const c = (code || '').trim()
  if (c.startsWith('6') || c.startsWith('5')) return '沪A'
  if (c.startsWith('0') || c.startsWith('3')) return '深A'
  if (c.startsWith('83') || c.startsWith('87') || c.startsWith('88') || c.startsWith('920')) return '北交'
  return 'A股'
}
