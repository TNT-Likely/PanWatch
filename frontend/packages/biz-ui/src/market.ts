export interface MarketBadgeInfo {
  style: string
  label: string
}

export function getMarketBadge(market: string): MarketBadgeInfo {
  if (market === 'HK') return { style: 'bg-orange-500/10 text-orange-600', label: '港股' }
  if (market === 'US') return { style: 'bg-stock-down/10 text-stock-down', label: '美股' }
  return { style: 'bg-blue-500/10 text-blue-600', label: 'A股' }
}
