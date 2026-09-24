import { cn } from '@/lib/utils'

interface PageHeaderProps {
  title: React.ReactNode
  description?: React.ReactNode
  /** 右侧操作区（刷新/新建等按钮组）。 */
  actions?: React.ReactNode
  className?: string
}

/** 页面标题区：标题 + 描述 + 右侧操作，全站统一头部。 */
export function PageHeader({ title, description, actions, className }: PageHeaderProps) {
  return (
    <div className={cn('mb-4 flex flex-wrap items-start justify-between gap-3', className)}>
      <div className="min-w-0">
        <h1 className="text-headline font-bold tracking-tight text-foreground">{title}</h1>
        {description && <p className="mt-1 text-secondary text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}
