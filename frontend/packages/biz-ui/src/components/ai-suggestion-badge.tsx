import type { MouseEventHandler } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '@panwatch/base-ui'
import { BadgeChip, type BadgeChipSize } from '@panwatch/biz-ui/components/badge-chip'
import { normalizeSuggestionAction, resolveSuggestionColorClass } from '@panwatch/biz-ui/components/suggestion-action'

interface AiSuggestionBadgeProps {
  action?: string
  actionLabel?: string
  isAI?: boolean
  isExpired?: boolean
  size?: BadgeChipSize
  className?: string
  title?: string
  onClick?: MouseEventHandler<HTMLButtonElement>
}

export function AiSuggestionBadge({
  action,
  actionLabel,
  isAI = false,
  isExpired = false,
  size = 'md',
  className,
  title,
  onClick,
}: AiSuggestionBadgeProps) {
  const { t } = useTranslation('bizUi')
  const normalized = normalizeSuggestionAction(action, actionLabel)
  const label = normalized
    ? t(`kline.actions.${normalized}`)
    : String(actionLabel || '').trim() || t('kline.actions.watch')
  const colorClass = resolveSuggestionColorClass(action, actionLabel)
  return (
    <BadgeChip
      label={label}
      aiTag={isAI}
      size={size}
      title={title}
      onClick={onClick}
      className={cn(colorClass, isExpired && 'opacity-50', className)}
    />
  )
}
