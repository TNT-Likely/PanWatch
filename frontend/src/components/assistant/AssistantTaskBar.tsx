import { useEffect, useState } from 'react'
import { AlertCircle, CheckCircle2, Loader2, Square } from 'lucide-react'
import type { AssistantStreamError, AssistantTaskSnapshot } from '@panwatch/api'
import { useTranslation } from 'react-i18next'
import { isActiveTask } from '@/hooks/useAssistantTask'
import { assistantRecovery } from '@/lib/assistant-recovery'

interface AssistantTaskBarProps {
  snapshot: AssistantTaskSnapshot | null
  error: AssistantStreamError | null
  disconnected: boolean
  control: 'stopping' | 'retrying' | 'checking' | null
  controlError: boolean
  onStop: () => void
  onRetry: () => void
  onReconnect: () => void
  onConfigure: () => void
  onPermissions: () => void
  onContext: () => void
  onRevise: () => void
  onReview: () => void
  onBackground?: () => void
}

export function AssistantTaskBar({ snapshot, error, disconnected, control, controlError, onStop, onRetry, onReconnect, onConfigure, onPermissions, onContext, onRevise, onReview, onBackground }: AssistantTaskBarProps) {
  const { t } = useTranslation('configuration')
  const taskT = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const tr = (key: string) => taskT(`assistantPage.task.${key}`)
  const [now, setNow] = useState(Date.now())
  const active = snapshot != null && isActiveTask(snapshot.status)
  useEffect(() => {
    if (!active) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [active, snapshot?.started_at])
  if (!snapshot && !error) return null
  const stopped = snapshot?.status === 'cancelled'
  const failed = snapshot?.status === 'failed' || snapshot?.status === 'expired' || snapshot?.status === 'dead_letter' || error != null
  const code = snapshot?.error_code || error?.code || 'assistant_unknown_error'
  const recovery = assistantRecovery(code)
  const startedAt = Date.parse(snapshot?.started_at || snapshot?.created_at || '')
  const durationMs = active && Number.isFinite(startedAt) ? now - startedAt : snapshot?.duration_ms || 0
  const seconds = Math.max(0, Math.floor(durationMs / 1000))
  const Icon = failed || disconnected ? AlertCircle : active ? Loader2 : stopped ? Square : CheckCircle2
  const buttonClass = 'rounded-md border border-border bg-background px-2.5 py-1.5 text-[11px] font-medium text-foreground hover:bg-accent disabled:opacity-50'
  return (
    <section data-testid="assistant-task-bar" className="shrink-0 border-t border-border/50 bg-card px-3 py-2 text-[12px] sm:px-4">
      <div className="flex flex-wrap items-center gap-2">
        <div role="status" className="flex min-w-0 flex-1 items-center gap-1.5 font-medium">
          <Icon className={`h-3.5 w-3.5 shrink-0 ${active && !disconnected ? 'animate-spin' : ''}`} />
          <span>{control ? tr(control) : disconnected ? tr('disconnected') : snapshot ? tr(`statuses.${snapshot.status}`) : tr('statuses.failed')}</span>
        </div>
        {snapshot?.current_step != null && snapshot.current_step > 0 && active && (
          <span className="text-muted-foreground">{taskT('assistantPage.task.step', { count: snapshot.current_step })}</span>
        )}
        {seconds > 0 && <span aria-hidden="true" className="tabular-nums text-muted-foreground">{seconds}s</span>}
        {active && <button type="button" onClick={onStop} disabled={control !== null} className={buttonClass}>{tr('stop')}</button>}
        {active && onBackground && <button type="button" onClick={onBackground} disabled={control !== null} className={buttonClass}>{taskT('assistantPage.activity.runInBackground')}</button>}
        {(disconnected || controlError) && <button type="button" onClick={onReconnect} disabled={control !== null} className={buttonClass}>{tr('checkStatus')}</button>}
        {(failed || stopped) && snapshot?.can_retry && recovery === 'retry' && error?.retryable !== false && (
          <button type="button" onClick={onRetry} disabled={control !== null} className={buttonClass}>{tr('retry')}</button>
        )}
        {failed && recovery === 'configuration' && <button type="button" onClick={onConfigure} className={buttonClass}>{tr('configure')}</button>}
        {failed && recovery === 'permissions' && <button type="button" onClick={onPermissions} className={buttonClass}>{tr('permissions')}</button>}
        {failed && recovery === 'context' && <button type="button" onClick={onContext} className={buttonClass}>{tr('context')}</button>}
        {failed && (recovery === 'revise' || recovery === 'context') && <button type="button" onClick={onRevise} className={buttonClass}>{tr('revise')}</button>}
        {(failed || stopped) && snapshot?.retry_blocked_reason === 'tools_already_started' && (
          <button type="button" onClick={onReview} className={buttonClass}>{tr('review')}</button>
        )}
        {(failed || stopped) && snapshot?.retry_blocked_reason === 'worker_stopping' && (
          <button type="button" onClick={onReconnect} disabled={control !== null} className={buttonClass}>{tr('checkStatus')}</button>
        )}
      </div>
      {failed && !disconnected && <p className="mt-1.5 text-destructive">{taskT(`assistantPage.errors.${code}`, { defaultValue: taskT('assistantPage.requestFailed') })}</p>}
      {disconnected && <p className="mt-1.5 text-muted-foreground">{tr('connectionHint')}</p>}
      {stopped && <p className="mt-1.5 text-muted-foreground">{tr('stoppedHint')}</p>}
      {(failed || stopped) && snapshot?.retry_blocked_reason === 'tools_already_started' && <p className="mt-1.5 text-muted-foreground">{tr('reviewHint')}</p>}
      {snapshot?.retry_blocked_reason === 'worker_stopping' && <p className="mt-1.5 text-muted-foreground">{tr('workerStoppingHint')}</p>}
      {controlError && <p role="alert" className="mt-1.5 text-destructive">{tr('controlFailed')}</p>}
    </section>
  )
}
