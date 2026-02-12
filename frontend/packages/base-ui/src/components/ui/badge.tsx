import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '../../cn'

const badgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[11px] font-medium transition-colors',
  {
    variants: {
      variant: {
        default: 'bg-primary/8 text-primary',
        secondary: 'bg-secondary text-secondary-foreground',
        destructive: 'bg-destructive/8 text-destructive',
        outline: 'border border-border text-muted-foreground',
        success: 'bg-emerald-500/10 text-emerald-600',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  )
}

export { Badge, badgeVariants }
