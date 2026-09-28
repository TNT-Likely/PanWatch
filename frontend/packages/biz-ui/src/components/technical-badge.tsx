import type { MouseEventHandler } from 'react'
import { cn } from '@aiwatch/base-ui'
import { BadgeChip, type BadgeChipSize } from '@aiwatch/biz-ui/components/badge-chip'
import { normalizeSuggestionAction, type SuggestionAction } from '@aiwatch/biz-ui/components/suggestion-action'

export type TechnicalBadgeTone = 'neutral' | 'bullish' | 'bearish' | 'warning' | 'info' | SuggestionAction

const toneClassMap: Record<TechnicalBadgeTone, string> = {
  neutral: 'bg-accent/50 text-muted-foreground',
  bullish: 'bg-stock-up/10 text-stock-up',
  bearish: 'bg-stock-down/10 text-stock-down',
  warning: 'bg-amber-500/10 text-amber-600 dark:bg-amber-400/15 dark:text-amber-300',
  info: 'bg-blue-500/10 text-blue-600 dark:bg-blue-400/15 dark:text-blue-300',
  // 动作徽章：暗色改「暗底亮字」——亮底白字在暗底上对比 1.67-3.06 不达 AA（审计）
  buy: 'bg-stock-up text-white dark:bg-stock-up/15 dark:text-stock-up',
  add: 'bg-stock-up text-white dark:bg-stock-up/15 dark:text-stock-up',
  reduce: 'bg-stock-down text-white dark:bg-stock-down/15 dark:text-stock-down',
  sell: 'bg-stock-down text-white dark:bg-stock-down/15 dark:text-stock-down',
  hold: 'bg-amber-500 text-white dark:bg-amber-400/15 dark:text-amber-300',
  watch: 'bg-slate-500 text-white dark:bg-slate-400/15 dark:text-slate-300',
  avoid: 'bg-stock-up text-white dark:bg-stock-up/15 dark:text-stock-up',
  alert: 'bg-blue-500 text-white dark:bg-blue-400/15 dark:text-blue-300',
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
