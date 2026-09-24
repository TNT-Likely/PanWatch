import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Skeleton } from '../ui/skeleton'

interface StatCardProps {
  /** 指标名（如「总市值」「今日盈亏」）。 */
  label: React.ReactNode
  /** 主数值节点（建议内联 PnlText 或带 tabular-nums 的文本）。 */
  value: React.ReactNode
  /** 次级说明（如口径、同比）。 */
  sub?: React.ReactNode
  icon?: LucideIcon
  /** 加载态：数值/副文案渲染为骨架。 */
  loading?: boolean
  className?: string
}

/** 汇总统计卡：持仓页 6 卡、模拟盘 5 卡等场景的统一容器。 */
export function StatCard({ label, value, sub, icon: Icon, loading, className }: StatCardProps) {
  return (
    <div className={cn('card min-w-0 px-4 py-3.5', className)}>
      <div className="flex items-center gap-1.5 text-body-sm text-muted-foreground">
        {Icon && <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />}
        <span className="truncate">{label}</span>
      </div>
      <div className="mt-1.5 truncate font-mono text-title font-bold leading-tight tabular-nums md:text-headline">
        {loading ? <Skeleton className="h-6 w-20" /> : value}
      </div>
      {sub !== undefined && (
        <div className="mt-1 truncate text-caption text-muted-foreground">
          {loading ? <Skeleton className="h-3.5 w-16" /> : sub}
        </div>
      )}
    </div>
  )
}
