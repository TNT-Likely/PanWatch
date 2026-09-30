import { Loader2, AlertCircle, CheckCircle2, PauseCircle } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { AssistantNotification } from '@panwatch/api'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import type { useAssistantActivity } from '@/hooks/useAssistantActivity'
import { formatDate } from '@/i18n/format'

interface Props {
  progressOnly?: boolean
  open: boolean
  onOpenChange: (open: boolean) => void
  monitor: ReturnType<typeof useAssistantActivity>
  onOpenConversation: (conversationId: number, notificationId?: number) => void
}

export default function AssistantActivityPanel({ open, onOpenChange, monitor, onOpenConversation, progressOnly = false }: Props) {
  const { activity, disconnected, loading, reading, readError, refresh, markRead } = monitor
  const { t } = useTranslation('configuration')
  const tr = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const buttonClass = 'rounded-lg border border-border bg-background px-2.5 py-1.5 text-[12px] font-medium text-foreground hover:bg-accent disabled:opacity-50'
  const title = (item: { title: string }) => item.title || tr('assistantPage.newResearch')
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="assistant-activity-panel" className="p-4 sm:p-6">
        <DialogHeader className="pr-10">
          <DialogTitle>{tr(progressOnly ? 'notifications.tasksTitle' : 'assistantPage.activity.title')}</DialogTitle>
          <DialogDescription>{tr('assistantPage.activity.description')}</DialogDescription>
        </DialogHeader>
        {disconnected && <p role="status" className="mb-3 rounded-lg bg-muted p-3 text-[12px] text-muted-foreground">{tr('assistantPage.activity.disconnected')}</p>}
        {!progressOnly && <div className="mb-4 flex items-center justify-between gap-2">
          <span className="text-[12px] text-muted-foreground">{tr('assistantPage.activity.unread', { count: activity.unread_count })}</span>
          <div className="flex gap-2">
            <button type="button" className={buttonClass} onClick={() => void refresh()}>{tr('assistantPage.activity.refresh')}</button>
            <button type="button" className={buttonClass} disabled={reading || activity.unread_count === 0} onClick={() => void markRead({ through_id: activity.notification_cursor })}>{tr('assistantPage.activity.markAllRead')}</button>
          </div>
        </div>}
        {loading && <p className="py-3 text-[12px] text-muted-foreground">{tr('assistantPage.activity.loading')}</p>}
        {readError && <p role="alert" className="mb-3 text-[12px] text-destructive">{tr('assistantPage.activity.readFailed')}</p>}
        <h3 className="mb-2 text-[13px] font-semibold">{tr('assistantPage.activity.activeTitle', { count: activity.active_tasks.length })}</h3>
        {activity.active_tasks.length === 0 ? <p className="mb-4 text-[12px] text-muted-foreground">{tr('assistantPage.activity.noActive')}</p> : (
          <ul className="mb-5 space-y-2">
            {activity.active_tasks.map((task) => (
              <li key={task.id} data-task-id={task.id} className="flex items-center gap-3 rounded-xl border border-border bg-background p-3">
                {task.status === 'awaiting_approval' ? <PauseCircle className="h-4 w-4 shrink-0 text-primary" /> : <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" />}
                <div className="min-w-0 flex-1">
                  <p title={title(task)} className="truncate text-[13px] font-medium">{title(task)}</p>
                  <p className="mt-1 text-[11px] text-muted-foreground">{tr(`assistantPage.task.statuses.${task.status}`)}{task.current_step > 0 ? ` · ${tr('assistantPage.task.step', { count: task.current_step })}` : ''}</p>
                </div>
                <button type="button" onClick={() => onOpenConversation(task.conversation_id)} className={buttonClass} aria-label={`${tr('assistantPage.activity.viewProgress')}: ${title(task)}`}>{tr('assistantPage.activity.viewProgress')}</button>
              </li>
            ))}
          </ul>
        )}
        {!progressOnly && <><h3 className="mb-2 text-[13px] font-semibold">{tr('assistantPage.activity.notificationsTitle')}</h3>
        {activity.notifications.length === 0 ? <p className="text-[12px] text-muted-foreground">{tr('assistantPage.activity.noNotifications')}</p> : (
          <ul className="space-y-2">
            {activity.notifications.map((item: AssistantNotification) => {
              const Icon = item.kind === 'failed' ? AlertCircle : item.kind === 'awaiting_approval' ? PauseCircle : CheckCircle2
              return (
                <li key={item.id} data-notification-id={item.id} className={`flex items-start gap-3 rounded-xl border p-3 ${item.read_at ? 'border-border bg-background' : 'border-primary/20 bg-primary/5'}`}>
                  <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${item.kind === 'failed' ? 'text-destructive' : 'text-primary'}`} />
                  <div className="min-w-0 flex-1">
                    <p title={title(item)} className="truncate text-[13px] font-medium">{title(item)}</p>
                    <p className="mt-1 text-[12px] text-muted-foreground">{tr(`assistantPage.activity.outcomes.${item.kind}`)}</p>
                    {item.created_at && <p className="mt-1 text-[10px] text-muted-foreground">{formatDate(item.created_at)}</p>}
                  </div>
                  <button type="button" onClick={() => onOpenConversation(item.conversation_id, item.id)} className={buttonClass} aria-label={`${tr('assistantPage.activity.viewConversation')}: ${title(item)}`}>{tr('assistantPage.activity.viewConversation')}</button>
                </li>
              )
            })}
          </ul>
        )}</>}
      </DialogContent>
    </Dialog>
  )
}
