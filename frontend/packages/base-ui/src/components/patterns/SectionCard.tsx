import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Skeleton } from '../ui/skeleton'
import { EmptyState, ErrorState } from './StateViews'

interface SectionCardProps {
  title?: React.ReactNode
  description?: React.ReactNode
  actions?: React.ReactNode
  /** 加载态：内容渲染为骨架行。 */
  loading?: boolean
  /** 错误：传 message（string）或自定义节点；提供 onRetry 时带重试按钮。 */
  error?: React.ReactNode
  onRetry?: () => void
  retryLabel?: string
  /** 空态：非 loading/error 且 children 为空时渲染。传 boolean 或自定义节点。 */
  empty?: React.ReactNode | boolean
  emptyIcon?: LucideIcon
  /** 数据为空的判定：默认取 !children。 */
  isEmpty?: boolean
  padded?: boolean
  className?: string
  contentClassName?: string
  children?: React.ReactNode
}

/**
 * 数据区块统一容器：标题行（title + actions）+ loading/error/empty 三态 + 内容。
 * 替代各页手写的「卡片 + 转圈 + 三元空态」样板。
 */
export function SectionCard({
  title,
  description,
  actions,
  loading,
  error,
  onRetry,
  retryLabel,
  empty,
  emptyIcon,
  isEmpty,
  padded = true,
  className,
  contentClassName,
  children,
}: SectionCardProps) {
  const emptyResolved = isEmpty ?? !children
  const showEmpty = !loading && !error && empty && (emptyResolved === true || emptyResolved)
  return (
    <section className={cn('card', className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 px-4 pt-4 md:px-5">
          <div className="min-w-0">
            <h2 className="text-body font-semibold text-foreground">{title}</h2>
            {description && <p className="mt-0.5 text-caption text-muted-foreground">{description}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-1.5">{actions}</div>}
        </header>
      )}
      <div className={cn(padded && 'p-4 md:p-5', title && padded && 'pt-3', contentClassName)}>
        {loading ? (
          <div className="space-y-2" aria-busy="true">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        ) : error ? (
          <ErrorState message={error} onRetry={onRetry} retryLabel={retryLabel} />
        ) : showEmpty ? (
          empty === true ? (
            <EmptyState icon={emptyIcon} title="暂无数据" />
          ) : (
            empty
          )
        ) : (
          children
        )}
      </div>
    </section>
  )
}
