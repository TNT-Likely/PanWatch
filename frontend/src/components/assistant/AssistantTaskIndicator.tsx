import { Clock3, Loader2, PauseCircle } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { AssistantTaskStatus } from '@panwatch/api'

export function AssistantTaskIndicator({ status }: { status?: AssistantTaskStatus }) {
  const { t } = useTranslation('configuration')
  if (!status || ['completed', 'failed', 'cancelled', 'expired', 'dead_letter'].includes(status)) return null
  const waiting = status === 'awaiting_approval' || status === 'waiting_retry'
  const Icon = status === 'awaiting_approval' ? PauseCircle : status === 'waiting_retry' || status === 'queued' ? Clock3 : Loader2
  return <span role="status" aria-label={t(`assistantPage.task.statuses.${status}`) as string} className="shrink-0 text-primary" data-testid="conversation-task-indicator">
    <Icon aria-hidden className={`h-3.5 w-3.5 ${!waiting && Icon === Loader2 ? 'animate-spin motion-reduce:animate-none' : ''}`} />
  </span>
}
