import { Check, Loader2, Minimize2, X } from 'lucide-react'
import type { AssistantContextDetail, AssistantContextSnapshot, ContextUsage } from '@panwatch/api'
import { useTranslation } from 'react-i18next'

interface ContextPanelProps {
  detail: AssistantContextDetail | null
  loading: boolean
  compressing: boolean
  error?: string
  onCompress: (mode: AssistantContextSnapshot['mode']) => void
  onClose?: () => void
}

function usagePercent(usage: ContextUsage): number {
  return Math.min(100, Math.round((usage.total_tokens / usage.budget_tokens) * 100))
}

export function ContextPanel({ detail, loading, compressing, error, onCompress, onClose }: ContextPanelProps) {
  const { t } = useTranslation('configuration')
  const contextT = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const tr = (key: string, options?: Record<string, unknown>) => contextT(`p4.components.contextPanel.${key}`, options)
  const sectionNames = ['system', 'summary', 'page_context', 'tool_definitions', 'history', 'recent_messages']
  const modes: Array<{ mode: AssistantContextSnapshot['mode']; key: string }> = [
    { mode: 'balanced', key: 'balanced' },
    { mode: 'preserve_details', key: 'preserve_details' },
    { mode: 'handoff', key: 'handoff' },
  ]
  const usageLabel = detail?.usage.measurement === 'provider'
    ? tr('measurement.provider')
    : detail?.usage.measurement === 'tokenizer'
      ? tr('measurement.tokenizer')
      : tr('measurement.estimate')
  return (
    <section data-testid="assistant-context-panel" className="border-b border-border/40 bg-background px-4 py-3 text-[12px]">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-medium text-foreground">{tr('title')}</h3>
          <p className="mt-0.5 text-muted-foreground">{tr('description')}</p>
        </div>
        {onClose && (
          <button type="button" onClick={onClose} className="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground" aria-label={tr('close')}>
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {loading && <div className="py-5 text-muted-foreground">{tr('measuring')}</div>}
      {!loading && detail && (
        <>
          <div className="mt-3 flex items-center justify-between gap-3">
            <span className="font-medium tabular-nums">{usageLabel}：{detail.usage.total_tokens.toLocaleString()} / {detail.usage.budget_tokens.toLocaleString()}</span>
            <span className={detail.status === 'needs_compression' ? 'text-rose-600' : detail.status === 'warning' ? 'text-amber-600' : 'text-emerald-600'}>
              {detail.status === 'needs_compression' ? tr('needsCompression') : detail.status === 'warning' ? tr('warning') : tr('normal')} · {usagePercent(detail.usage)}%
            </span>
          </div>
          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className={detail.status === 'needs_compression' ? 'h-full bg-rose-500' : detail.status === 'warning' ? 'h-full bg-amber-500' : 'h-full bg-emerald-500'}
              style={{ width: `${usagePercent(detail.usage)}%` }}
            />
          </div>
          <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5">
            {detail.usage.sections.filter((section) => section.tokens > 0).map((section) => (
              <div key={section.name} className="flex min-w-0 items-center justify-between gap-2 text-muted-foreground">
                <span className="truncate">{sectionNames.includes(section.name) ? tr(`sections.${section.name}`) : section.name}</span>
                <span className="shrink-0 tabular-nums text-foreground">{section.tokens.toLocaleString()}</span>
              </div>
            ))}
          </div>
          {detail.snapshot && (
            <div className="mt-3 border-t border-border/40 pt-2 text-muted-foreground">
              <div className="flex items-center gap-1.5"><Check className="h-3.5 w-3.5 text-emerald-600" />{tr('snapshot', { version: detail.snapshot.version })}</div>
              {detail.snapshot.summary.goal.length > 0 && <p className="mt-1 truncate">{tr('goal', { value: detail.snapshot.summary.goal[0] })}</p>}
              {detail.snapshot.summary.current_state && <p className="truncate">{tr('state', { value: detail.snapshot.summary.current_state })}</p>}
              {detail.snapshot.summary.open_items.length > 0 && <p className="truncate">{tr('todo', { value: detail.snapshot.summary.open_items[0] })}</p>}
            </div>
          )}
          {detail.last_compression && (
            <div className="mt-3 border-t border-border/40 pt-2 text-muted-foreground">
              <div className={detail.last_compression.status === 'no_gain' ? 'text-amber-600' : 'text-emerald-600'}>
                {tr(`statuses.${detail.last_compression.status}`)}
              </div>
              {detail.last_compression.status === 'compressed' && (
                <p className="mt-1">
                  {tr('savedTokens', { before: detail.last_compression.usage_before.total_tokens.toLocaleString(), after: detail.last_compression.usage_after.total_tokens.toLocaleString(), saved: detail.last_compression.saved_tokens.toLocaleString(), percent: detail.last_compression.saved_percent })}
                </p>
              )}
            </div>
          )}
          <div className="mt-3 flex flex-wrap gap-1.5">
            {modes.map(({ mode, key }) => (
              <button
                key={mode}
                type="button"
                disabled={compressing}
                onClick={() => onCompress(mode)}
                className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2 py-1.5 text-[11px] text-foreground transition-colors hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
              >
                {compressing && mode === 'balanced' ? <Loader2 className="h-3 w-3 animate-spin" /> : <Minimize2 className="h-3 w-3" />}
                {compressing && mode === 'balanced' ? tr('compressing') : tr(`modes.${key}`)}
              </button>
            ))}
          </div>
        </>
      )}
      {!loading && !detail && <p className="py-4 text-muted-foreground">{tr('empty')}</p>}
      {error && <p className="mt-2 text-rose-600">{error}</p>}
    </section>
  )
}
