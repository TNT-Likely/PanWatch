import type { MouseEventHandler, ReactNode } from 'react'
import { cn } from '@panwatch/base-ui'

export type BadgeChipSize = 'xs' | 'sm' | 'md' | 'lg'

const sizeClassMap: Record<BadgeChipSize, string> = {
  xs: 'text-mini px-1.5 py-0.5',
  sm: 'text-caption px-2 py-0.5',
  md: 'text-body-sm px-2.5 py-1',
  lg: 'text-body px-3 py-1.5',
}

interface BadgeChipProps {
  label: ReactNode
  className?: string
  size?: BadgeChipSize
  aiTag?: boolean
  title?: string
  onClick?: MouseEventHandler<HTMLButtonElement>
}

export function BadgeChip({
  label,
  className,
  size = 'md',
  aiTag = false,
  title,
  onClick,
}: BadgeChipProps) {
  const sharedClass = cn(
    'relative inline-flex items-center rounded font-medium whitespace-nowrap transition-opacity',
    sizeClassMap[size],
    className,
  )

  const aiTagNode = aiTag ? (
    <span className="pointer-events-none absolute top-0 left-0 -translate-x-1/2 -translate-y-1/2 text-mini leading-none px-1.5 py-[2px] rounded-sm bg-primary text-white uppercase shadow-sm ring-1 ring-black/20">
      AI
    </span>
  ) : null

  if (onClick) {
    return (
      <button type="button" onClick={onClick} title={title} className={cn(sharedClass, 'cursor-pointer hover:opacity-80')}>
        {label}
        {aiTagNode}
      </button>
    )
  }

  return (
    <span title={title} className={sharedClass}>
      {label}
      {aiTagNode}
    </span>
  )
}
