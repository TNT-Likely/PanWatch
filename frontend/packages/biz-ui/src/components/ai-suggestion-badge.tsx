import type { MouseEventHandler } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '@panwatch/base-ui'
import { BadgeChip, type BadgeChipSize } from '@panwatch/biz-ui/components/badge-chip'
import { suggestionPresentation, type SuggestionStateInput } from '@panwatch/biz-ui/components/suggestion-action'

interface AiSuggestionBadgeProps extends SuggestionStateInput {
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
  status, review_required, attention_required, rating_raw, meta, action_label,
}: AiSuggestionBadgeProps) {
  const { t } = useTranslation('bizUi')
  const view = suggestionPresentation({ action, action_label: action_label || actionLabel, status, review_required, attention_required, rating_raw, meta })
  const label = (t as unknown as (key: string) => string)(view.labelKey)
  const colorClass = view.colorClass
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
