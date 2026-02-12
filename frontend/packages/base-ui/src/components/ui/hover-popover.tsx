import * as React from 'react'
import { cn } from '../../cn'

interface HoverPopoverProps {
  trigger: React.ReactNode
  title?: React.ReactNode
  content: React.ReactNode
  side?: 'top' | 'bottom'
  align?: 'start' | 'center' | 'end'
  className?: string
  popoverClassName?: string
  openOnFocus?: boolean
}

export function HoverPopover({
  trigger,
  title,
  content,
  side = 'top',
  align = 'center',
  className,
  popoverClassName,
  openOnFocus = false,
}: HoverPopoverProps) {
  const sideClass = side === 'top' ? 'bottom-full mb-2' : 'top-full mt-2'
  const alignClass =
    align === 'start' ? 'left-0' :
    align === 'end' ? 'right-0' :
    'left-1/2 -translate-x-1/2'

  return (
    <span className={cn('relative inline-flex group', className)}>
      {/* tabIndex: allow keyboard focus to open popover via focus-within (also helps on mobile tap) */}
      <span tabIndex={0} className="outline-none">
        {trigger}
      </span>
      <span
        className={cn(
          'absolute z-50',
          sideClass,
          alignClass,
          'opacity-0 pointer-events-none translate-y-1 scale-[0.98] transition-all duration-150',
          'group-hover:opacity-100 group-hover:pointer-events-auto group-hover:translate-y-0 group-hover:scale-100',
          openOnFocus && 'group-focus-within:opacity-100 group-focus-within:pointer-events-auto group-focus-within:translate-y-0 group-focus-within:scale-100',
        )}
      >
        <span className={cn('w-[22rem] max-w-[90vw] block rounded-xl bg-card border border-border shadow-[0_16px_60px_rgba(0,0,0,0.18)] p-3', popoverClassName)}>
          {title && (
            <div className="text-[12px] font-semibold text-foreground mb-1">
              {title}
            </div>
          )}
          <div className="text-[11px] leading-relaxed text-muted-foreground max-h-72 overflow-y-auto pr-1">
            {content}
          </div>
        </span>
      </span>
    </span>
  )
}
