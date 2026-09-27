import { Gauge } from 'lucide-react'
import type { ContextUsage } from '@panwatch/api'
import { useTranslation } from 'react-i18next'

interface ContextUsageIndicatorProps {
  usage: ContextUsage | null
  onClick: () => void
}

export function ContextUsageIndicator({ usage, onClick }: ContextUsageIndicatorProps) {
  const { t, i18n } = useTranslation('configuration')
  if (!usage) return null
  const assistantT = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const formatNumber = (value: number) => value.toLocaleString(i18n.resolvedLanguage === 'en-US' ? 'en-US' : 'zh-CN')
  const state = assistantT(`p4.components.contextUsage.states.${usage.state}`)
  const stateClass = usage.state === 'needs_compression'
    ? 'text-rose-600 dark:text-rose-400'
    : usage.state === 'warning'
      ? 'text-amber-600 dark:text-amber-400'
      : 'text-muted-foreground'

  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex min-w-0 items-center gap-1 rounded-md px-1.5 py-1 text-[11px] transition-colors hover:bg-accent/60 ${stateClass}`}
      aria-label={assistantT('p4.components.contextUsage.aria')}
      title={assistantT('p4.components.contextUsage.title', { state, used: formatNumber(usage.total_tokens), budget: formatNumber(usage.budget_tokens) })}
    >
      <Gauge className="h-3.5 w-3.5 shrink-0" />
      <span className="tabular-nums">{formatNumber(usage.total_tokens)} / {formatNumber(usage.budget_tokens)}</span>
    </button>
  )
}
