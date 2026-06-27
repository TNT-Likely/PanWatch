import { Bell, TrendingDown, TrendingUp } from 'lucide-react'
import { Button } from '@panwatch/base-ui/components/ui/button'
import type { ChatPendingAction } from '@panwatch/api'

const TYPE_ICONS = {
  create_price_alert: Bell,
  add_position: TrendingUp,
  reduce_position: TrendingDown,
} as const

const STATUS_LABELS: Record<string, string> = {
  pending: '待确认',
  confirmed: '已执行',
  cancelled: '已取消',
  expired: '已过期',
  failed: '失败',
}

export default function ChatActionCard(props: {
  action: ChatPendingAction
  loading?: boolean
  onConfirm: (actionId: string) => void
  onCancel: (actionId: string) => void
}) {
  const { action, loading } = props
  const Icon = TYPE_ICONS[action.type] || Bell
  const lines = action.preview?.lines || []
  const warnings = action.preview?.warnings || []
  const isPending = action.status === 'pending'

  return (
    <div className="mt-2 rounded-lg border border-border/60 bg-background/80 p-3 text-[12px]">
      <div className="flex items-start gap-2">
        <div className="mt-0.5 rounded-md bg-primary/10 p-1.5 text-primary">
          <Icon className="h-3.5 w-3.5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium text-foreground">
              {action.preview?.title || '待确认操作'}
            </span>
            <span className="shrink-0 text-[10px] text-muted-foreground">
              {STATUS_LABELS[action.status] || action.status}
            </span>
          </div>
          {lines.length > 0 && (
            <ul className="mt-1.5 space-y-0.5 text-muted-foreground">
              {lines.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}
          {warnings.length > 0 && (
            <div className="mt-1.5 rounded-md bg-amber-500/10 px-2 py-1 text-amber-700 dark:text-amber-300">
              {warnings.join('；')}
            </div>
          )}
          {action.status === 'confirmed' && action.result && (
            <div className="mt-1.5 text-emerald-600 dark:text-emerald-400">
              操作已成功执行
            </div>
          )}
        </div>
      </div>
      {isPending && (
        <div className="mt-3 flex justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 px-3 text-[11px]"
            disabled={loading}
            onClick={() => props.onCancel(action.id)}
          >
            取消
          </Button>
          <Button
            type="button"
            size="sm"
            className="h-7 px-3 text-[11px]"
            disabled={loading}
            onClick={() => props.onConfirm(action.id)}
          >
            {loading ? '执行中...' : '确认执行'}
          </Button>
        </div>
      )}
    </div>
  )
}
