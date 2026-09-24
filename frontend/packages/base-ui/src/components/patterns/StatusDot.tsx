import { cn } from '@/lib/utils'

export type StatusDotTone = 'ok' | 'warn' | 'error' | 'neutral'

const TONE_CLS: Record<StatusDotTone, string> = {
  ok: 'bg-success',
  warn: 'bg-warning',
  error: 'bg-destructive',
  neutral: 'bg-muted-foreground/40',
}

interface StatusDotProps {
  tone: StatusDotTone
  /** 附加语义文本（供色盲/读屏场景）。 */
  label?: string
  className?: string
}

/** 状态点：健康度/启用态等三态指示，必须伴随文字或 aria-label 使用。 */
export function StatusDot({ tone, label, className }: StatusDotProps) {
  return (
    <span className={cn('inline-flex items-center gap-1', className)}>
      <span className={cn('inline-block h-1.5 w-1.5 rounded-full', TONE_CLS[tone])} aria-hidden="true" />
      {label && <span className="sr-only">{label}</span>}
    </span>
  )
}
