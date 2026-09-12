import { RefreshCw, ShieldAlert } from 'lucide-react'

interface ActionStatusCardProps {
  status: 'needs_retry'
  message: string
  onRetry: () => void
}

/** A recoverable action state; expected execution misses are not error bubbles. */
export function ActionStatusCard({ status, message, onRetry }: ActionStatusCardProps) {
  if (status !== 'needs_retry') return null

  return (
    <section
      className="max-w-[85%] rounded-xl border border-amber-500/30 bg-amber-500/5 px-3 py-3 text-[13px]"
      role="status"
      aria-label="尚未执行"
    >
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 rounded-md bg-amber-500/10 p-1.5 text-amber-600 dark:text-amber-400">
          <ShieldAlert className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h4 className="font-medium text-foreground">尚未执行</h4>
          <p className="mt-1 text-muted-foreground">{message}</p>
          <button
            type="button"
            onClick={onRetry}
            className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-primary px-2.5 py-1.5 text-[12px] font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            重试执行
          </button>
        </div>
      </div>
    </section>
  )
}
