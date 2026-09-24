import type { LucideIcon } from 'lucide-react'
import { RefreshCw } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '../ui/button'

interface EmptyStateProps {
  icon?: LucideIcon
  /** 一句话说明空的是什么。 */
  title: React.ReactNode
  /** 引导文案：说明下一步能做什么。 */
  description?: React.ReactNode
  /** 行动入口（如「添加持仓」按钮）。 */
  action?: React.ReactNode
  className?: string
}

/** 空态：引导型（做什么能看见数据），而非单纯「暂无数据」。 */
export function EmptyState({ icon: Icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-2 px-6 py-10 text-center', className)}>
      {Icon && (
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent/60 text-muted-foreground">
          <Icon className="h-5 w-5" aria-hidden="true" />
        </div>
      )}
      <div className="text-body font-medium text-foreground">{title}</div>
      {description && <div className="max-w-sm text-secondary text-muted-foreground">{description}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

interface ErrorStateProps {
  /** 错误说明：string 或自定义节点。 */
  message: React.ReactNode
  onRetry?: () => void
  retryLabel?: string
  className?: string
}

/** 错误态：说明 + 重试入口（替代静默 console.warn）。 */
export function ErrorState({ message, onRetry, retryLabel = '重试', className }: ErrorStateProps) {
  return (
    <div
      className={cn('flex flex-col items-center justify-center gap-2 px-6 py-8 text-center', className)}
      role="alert"
    >
      <div className="text-body font-medium text-destructive">加载失败</div>
      {message && <div className="max-w-sm text-secondary text-muted-foreground">{message}</div>}
      {onRetry && (
        <Button variant="secondary" size="sm" className="mt-2" onClick={onRetry}>
          <RefreshCw className="h-3.5 w-3.5" />
          {retryLabel}
        </Button>
      )}
    </div>
  )
}

interface LoadingStateProps {
  label?: string
  className?: string
}

/** 行内加载态：转圈 + 文案（路由级与区块内通用）。 */
export function LoadingState({ label = '加载中…', className }: LoadingStateProps) {
  return (
    <div className={cn('flex items-center justify-center gap-2 px-6 py-10 text-secondary text-muted-foreground', className)}>
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-primary/30 border-t-primary" aria-hidden="true" />
      <span role="status">{label}</span>
    </div>
  )
}
