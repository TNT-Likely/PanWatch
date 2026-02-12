import * as React from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { DayPicker } from 'react-day-picker'

import { cn } from '../../cn'
import { buttonVariants } from '@panwatch/base-ui/components/ui/button'

export type CalendarProps = React.ComponentProps<typeof DayPicker>

function Calendar({ className, classNames, showOutsideDays = true, ...props }: CalendarProps) {
  return (
    <DayPicker
      showOutsideDays={showOutsideDays}
      className={cn('p-3', className)}
      classNames={
        {
          root: 'w-full',
          months: 'flex flex-col gap-4',
          month: 'space-y-4',
          month_caption: 'flex justify-center pt-1 relative items-center',
          caption_label: 'text-sm font-medium',
          nav: 'flex items-center gap-1',
          button_previous: cn(
            buttonVariants({ variant: 'outline' }),
            'h-7 w-7 bg-transparent p-0 opacity-70 hover:opacity-100 absolute left-1'
          ),
          button_next: cn(
            buttonVariants({ variant: 'outline' }),
            'h-7 w-7 bg-transparent p-0 opacity-70 hover:opacity-100 absolute right-1'
          ),
          dropdowns: 'flex items-center justify-center gap-1.5',
          dropdown_root:
            'relative has-[select:focus-visible]:border-ring has-[select:focus-visible]:ring-ring/40 has-[select:focus-visible]:ring-[3px] rounded-md border border-border bg-background/60',
          dropdown: 'appearance-none bg-transparent px-2 py-1.5 text-sm pr-7',
          chevron: 'w-4 h-4 text-muted-foreground',
          month_grid: 'w-full border-collapse',
          weekdays: 'flex',
          weekday: 'w-9 text-center text-[0.8rem] text-muted-foreground font-normal',
          weeks: 'mt-1',
          week: 'flex w-full',
          day: 'h-9 w-9 p-0 text-center text-sm relative',
          day_button: cn(buttonVariants({ variant: 'ghost' }), 'h-9 w-9 p-0 font-normal'),
          selected:
            'bg-primary text-primary-foreground hover:bg-primary hover:text-primary-foreground',
          today: 'bg-accent text-accent-foreground',
          outside: 'text-muted-foreground opacity-35',
          disabled: 'text-muted-foreground opacity-30',
          hidden: 'invisible',
          range_start: 'bg-primary text-primary-foreground rounded-l-md',
          range_middle: 'bg-primary/10 text-foreground',
          range_end: 'bg-primary text-primary-foreground rounded-r-md',
          ...classNames,
        } as any
      }
      components={{
        Chevron: ({ orientation, ...iconProps }) =>
          orientation === 'left' ? (
            <ChevronLeft {...iconProps} className={cn('w-4 h-4', iconProps.className)} />
          ) : (
            <ChevronRight {...iconProps} className={cn('w-4 h-4', iconProps.className)} />
          ),
      }}
      {...props}
    />
  )
}
Calendar.displayName = 'Calendar'

export { Calendar }
