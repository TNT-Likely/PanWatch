export interface MarketBadgeInfo {
  style: string
  label: string
}

type Translate = (key: string) => string

export function getMarketBadge(market: string, t: Translate): MarketBadgeInfo {
  if (market === 'HK') return { style: 'bg-orange-500/10 text-orange-600', label: t('HK') }
  if (market === 'US') return { style: 'bg-green-500/10 text-green-600', label: t('US') }
  if (market === 'TSX' || market === 'TSXV') return { style: 'bg-violet-500/10 text-violet-600', label: t(market) }
  return { style: 'bg-blue-500/10 text-blue-600', label: t('CN') }
}
