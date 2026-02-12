import * as React from 'react'
import { cn } from '../../cn'
import { Popover, PopoverContent, PopoverTrigger } from './popover'

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
  const [open, setOpen] = React.useState(false)
  const closeTimer = React.useRef<number | null>(null)

  const clearCloseTimer = React.useCallback(() => {
    if (closeTimer.current != null) {
      window.clearTimeout(closeTimer.current)
      closeTimer.current = null
    }
  }, [])

  const openPopover = React.useCallback(() => {
    clearCloseTimer()
    setOpen(true)
  }, [clearCloseTimer])

  const closePopover = React.useCallback(() => {
    clearCloseTimer()
    closeTimer.current = window.setTimeout(() => setOpen(false), 80)
  }, [clearCloseTimer])

  React.useEffect(() => {
    return () => clearCloseTimer()
  }, [clearCloseTimer])

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <span
          className={cn('inline-flex', className)}
          tabIndex={openOnFocus ? 0 : undefined}
          onMouseEnter={openPopover}
          onMouseLeave={closePopover}
          onFocus={openOnFocus ? openPopover : undefined}
          onBlur={openOnFocus ? closePopover : undefined}
        >
          {trigger}
        </span>
      </PopoverTrigger>
      <PopoverContent
        side={side}
        align={align}
        sideOffset={8}
        onMouseEnter={openPopover}
        onMouseLeave={closePopover}
        className={cn(
          'w-[22rem] max-w-[90vw] rounded-xl border border-border bg-card p-3 shadow-[0_16px_60px_rgba(0,0,0,0.18)]',
          popoverClassName,
        )}
      >
        {title && (
          <div className="text-[12px] font-semibold text-foreground mb-1">
            {title}
          </div>
        )}
        <div className="text-[11px] leading-relaxed text-muted-foreground max-h-72 overflow-y-auto pr-1">
          {content}
        </div>
      </PopoverContent>
    </Popover>
  )
}
