import type { MouseEventHandler } from 'react'
import { cn } from '@panwatch/base-ui'
import { BadgeChip, type BadgeChipSize } from '@panwatch/biz-ui/components/badge-chip'
import { normalizeSuggestionAction, type SuggestionAction } from '@panwatch/biz-ui/components/suggestion-action'

export type TechnicalBadgeTone = 'neutral' | 'bullish' | 'bearish' | 'warning' | 'info' | SuggestionAction

const toneClassMap: Record<TechnicalBadgeTone, string> = {
  neutral: 'bg-accent/50 text-muted-foreground',
  bullish: 'bg-stock-up/10 text-stock-up',
  bearish: 'bg-stock-down/10 text-stock-down',
  warning: 'bg-amber-500/10 text-amber-600',
  info: 'bg-blue-500/10 text-blue-600',
  buy: 'bg-stock-up text-white',
  add: 'bg-stock-up text-white',
  reduce: 'bg-stock-down text-white',
  sell: 'bg-stock-down text-white',
  hold: 'bg-amber-500 text-white',
  watch: 'bg-slate-500 text-white',
  avoid: 'bg-stock-up text-white',
  alert: 'bg-blue-500 text-white',
}

interface TechnicalBadgeProps {
  label: string
  tone?: TechnicalBadgeTone
  size?: BadgeChipSize
  className?: string
  title?: string
  help?: boolean
  onClick?: MouseEventHandler<HTMLButtonElement>
}

export function technicalToneFromSuggestionAction(action?: string, actionLabel?: string): TechnicalBadgeTone {
  return normalizeSuggestionAction(action, actionLabel) || 'watch'
}

export function TechnicalBadge({
  label,
  tone = 'neutral',
  size = 'sm',
  className,
  title,
  help = false,
  onClick,
}: TechnicalBadgeProps) {
  return (
    <BadgeChip
      label={label}
      size={size}
      onClick={onClick}
      title={title}
      className={cn(
        toneClassMap[tone],
        !onClick && help && 'cursor-help hover:opacity-80',
        className,
      )}
    />
  )
}
