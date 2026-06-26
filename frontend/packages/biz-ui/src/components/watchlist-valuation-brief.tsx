import { cn } from '@panwatch/base-ui'
import type { LmdReportSnapshot } from '@panwatch/api'
import type { MouseEvent } from 'react'

function formatPe(value: number | null | undefined): string | null {
  if (value == null) return null
  if (value < 0) return '负'
  return value.toFixed(1)
}

function formatPct(value: number | null | undefined): string | null {
  if (value == null) return null
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(1)}%`
}

function buildMetricChips(
  snapshot: LmdReportSnapshot | null | undefined,
  quotePe?: number | null,
) {
  const pe = snapshot?.pe_ttm ?? quotePe ?? null
  const chips: Array<{ key: string; label: string; value: string; tone?: 'up' | 'down' | 'muted' }> = []

  const peText = formatPe(pe)
  if (peText) chips.push({ key: 'pe', label: 'PE', value: peText })

  if (snapshot?.forward_pe != null) {
    chips.push({ key: 'fpe', label: '前瞻', value: `${snapshot.forward_pe.toFixed(0)}x` })
  }
  if (snapshot?.pb != null) {
    chips.push({ key: 'pb', label: 'PB', value: `${snapshot.pb.toFixed(1)}x` })
  }
  if (snapshot?.consensus_eps != null) {
    chips.push({ key: 'eps', label: 'EPS', value: snapshot.consensus_eps.toFixed(2) })
  }
  if (snapshot?.revenue_yoy_pct != null) {
    chips.push({
      key: 'rev',
      label: '营收',
      value: formatPct(snapshot.revenue_yoy_pct) || '--',
      tone: snapshot.revenue_yoy_pct > 0 ? 'up' : snapshot.revenue_yoy_pct < 0 ? 'down' : 'muted',
    })
  }
  if (snapshot?.profit_yoy_pct != null) {
    chips.push({
      key: 'profit',
      label: '净利',
      value: formatPct(snapshot.profit_yoy_pct) || '--',
      tone: snapshot.profit_yoy_pct > 0 ? 'up' : snapshot.profit_yoy_pct < 0 ? 'down' : 'muted',
    })
  }
  if (snapshot?.gross_margin_pct != null) {
    chips.push({ key: 'gm', label: '毛利', value: `${snapshot.gross_margin_pct.toFixed(1)}%` })
  }
  if (snapshot?.roe_pct != null) {
    chips.push({ key: 'roe', label: 'ROE', value: `${snapshot.roe_pct.toFixed(1)}%` })
  }
  if (snapshot?.valuation_score != null) {
    chips.push({ key: 'val', label: '估值', value: `${snapshot.valuation_score}分` })
  } else if (snapshot?.valuation_verdict) {
    chips.push({ key: 'val', label: '估值', value: snapshot.valuation_verdict })
  }
  if (snapshot?.expectation_hint) {
    chips.push({ key: 'exp', label: '预期', value: snapshot.expectation_hint })
  }

  return chips
}

export function WatchlistValuationBrief(props: {
  snapshot?: LmdReportSnapshot | null
  quotePe?: number | null
  compact?: boolean
  className?: string
  onClick?: () => void
  onEnsureReport?: () => void
}) {
  const { snapshot, quotePe, compact = true, className, onClick, onEnsureReport } = props
  const hasLmdReport = !!snapshot?.has_report
  const chips = buildMetricChips(snapshot, quotePe)
  const showPending = !hasLmdReport

  if (chips.length === 0 && !showPending) return null

  const title = snapshot?.report_date
    ? `老马视角 · ${snapshot.report_date}`
    : showPending
      ? '点击排队生成老马视角报告'
      : '老马视角估值快照'

  const handleClick = (e: MouseEvent) => {
    e.stopPropagation()
    if (showPending && onEnsureReport) {
      onEnsureReport()
      return
    }
    onClick?.()
  }

  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-1',
        (onClick || onEnsureReport) && 'cursor-pointer',
        className,
      )}
      title={title}
      onClick={handleClick}
    >
      {showPending && (
        <span
          className={cn(
            'inline-flex items-center rounded bg-amber-500/10 px-1 py-px text-amber-700 dark:text-amber-300',
            compact ? 'text-[9px]' : 'text-[10px]',
          )}
        >
          待生成老马报告
        </span>
      )}
      {chips.map((chip) => (
        <span
          key={chip.key}
          className={cn(
            'inline-flex items-center gap-0.5 rounded bg-muted/50 px-1 py-px font-mono',
            compact ? 'text-[9px]' : 'text-[10px]',
            chip.tone === 'up' && 'text-rose-600 dark:text-rose-400',
            chip.tone === 'down' && 'text-emerald-600 dark:text-emerald-400',
            chip.tone === 'muted' && 'text-muted-foreground',
          )}
        >
          <span className="text-muted-foreground font-sans">{chip.label}</span>
          <span>{chip.value}</span>
        </span>
      ))}
    </div>
  )
}
