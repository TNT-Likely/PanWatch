import { ExternalLink } from 'lucide-react'
import { useKlineExternalLinkEnabled } from '../hooks/use-kline-external-link'
import { buildStockUrl, getStockLinkPlatformLabel } from '../lib/stock-link'

export default function StockExternalLink(props: {
  symbol: string
  market: string
  className?: string
  showLabel?: boolean
}) {
  const enabled = useKlineExternalLinkEnabled()
  const symbol = String(props.symbol || '').trim()
  if (!enabled || !symbol) return null

  const url = buildStockUrl(symbol, props.market)
  const label = getStockLinkPlatformLabel()
  const showLabel = props.showLabel !== false

  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={`在${label}查看 K 线`}
      title={`在${label}查看 K 线`}
      className={
        props.className
        || 'inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground'
      }
      onClick={(e) => e.stopPropagation()}
    >
      {showLabel ? <span>{label}</span> : null}
      <ExternalLink className="w-3 h-3 shrink-0" />
    </a>
  )
}
