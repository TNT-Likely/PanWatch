import type { MouseEventHandler } from 'react'
import { cn } from '@panwatch/base-ui'
import { BadgeChip, type BadgeChipSize } from '@panwatch/biz-ui/components/badge-chip'
import { resolveSuggestionColorClass, resolveSuggestionLabel } from '@panwatch/biz-ui/components/suggestion-action'

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
  const label = resolveSuggestionLabel(action, actionLabel)
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
