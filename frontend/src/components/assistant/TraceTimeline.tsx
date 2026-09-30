import { AlertCircle, CheckCircle2, ChevronDown, FileClock, Gauge, ListTree, PauseCircle, Search, Wrench } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { AssistantTraceEvent } from '@panwatch/api'
import { useTranslation } from 'react-i18next'

interface TraceTimelineProps {
  events: AssistantTraceEvent[]
  live?: boolean
  reviewRequest?: number
}

function describeExtensionEvent(event: AssistantTraceEvent, tr: (key: string, options?: Record<string, unknown>) => string): { label: string; icon: typeof FileClock } | null {
  if (event.data.extension !== 'tool_research') return null
  const data = event.data.data || {}
  switch (event.data.event) {
    case 'started': return { label: tr('extension.started'), icon: Search }
    case 'exposure': return {
      label: tr('extension.exposure', { direct: data.direct_tools?.length || 0, loaded: data.loaded_tools?.length || 0 }),
      icon: Search,
    }
    case 'candidates_scored': return { label: tr('extension.candidates', { count: data.candidates?.length || 0 }), icon: Search }
    case 'completed': return { label: tr('extension.completed', { count: data.selected_tools?.length || 0 }), icon: Search }
    case 'searched': return { label: tr('extension.searched', { count: data.selected_tools?.length || 0 }), icon: Search }
    case 'fallback': return { label: tr('extension.fallback'), icon: AlertCircle }
    default: return { label: tr('extension.unknown', { event: event.data.event || 'unknown' }), icon: FileClock }
  }
}

function describe(event: AssistantTraceEvent, tr: (key: string, options?: Record<string, unknown>) => string): { label: string; icon: typeof FileClock } {
  const name = typeof event.data.name === 'string' ? event.data.name : ''
  const displayName = name ? tr(`tools.${name}`, { defaultValue: name }) : name
  const extension = event.event === 'extension_event' ? describeExtensionEvent(event, tr) : null
  if (extension) return extension
  switch (event.event) {
    case 'task_created':
    case 'task_queued': return { label: tr('events.queued'), icon: FileClock }
    case 'retry_scheduled': return { label: tr('events.retrying'), icon: FileClock }
    case 'cancelled': return { label: tr('events.cancelled'), icon: PauseCircle }
    case 'context_prepared': return { label: tr(event.data.compressed ? 'events.contextCompressed' : 'events.contextPrepared'), icon: FileClock }
    case 'step_updated': return { label: tr('events.step', { step: event.data.step || '' }), icon: ListTree }
    case 'tool_call_start': return { label: tr('events.toolStart', { name: displayName }), icon: Wrench }
    case 'tool_result': return {
      label: `${tr(event.data.ok ? 'events.toolDone' : 'events.toolFailed', { name: displayName })}${formatDurationSuffix(event.data.duration_ms)}`,
      icon: event.data.ok ? CheckCircle2 : AlertCircle,
    }
    case 'model_usage': {
      const suffix = formatDurationSuffix(event.data.duration_ms)
      const cache = Number(event.data.cached_input_tokens || 0)
      const reasoning = Number(event.data.reasoning_output_tokens || 0)
      const extras = [
        cache > 0 ? tr('events.cache', { count: cache }) : '',
        reasoning > 0 ? tr('events.reasoning', { count: reasoning }) : '',
      ].filter(Boolean)
      const usageSuffix = extras.length > 0 ? ` · ${extras.join(' · ')}` : ''
      return {
        label: `${tr('events.modelUsage', { input: event.data.input_tokens || 0, output: event.data.output_tokens || 0, extra: usageSuffix })}${suffix}`,
        icon: Gauge,
      }
    }
    case 'approval_required': return { label: tr('events.approval'), icon: PauseCircle }
    case 'paused': return { label: tr('events.paused'), icon: PauseCircle }
    case 'done': return { label: tr('events.done'), icon: CheckCircle2 }
    case 'error': return { label: tr('events.error'), icon: AlertCircle }
    default: return { label: tr('events.started'), icon: FileClock }
  }
}

function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return ''
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1).replace(/\.0$/, '')}s`
}

function formatDurationSuffix(value: unknown): string {
  const duration = formatDuration(Number(value || 0))
  return duration ? ` · ${duration}` : ''
}

function detail(event: AssistantTraceEvent): string {
  if (event.event === 'tool_call_start' && event.data.arguments) {
    return JSON.stringify(event.data.arguments)
  }
  if (event.event === 'tool_result' && typeof event.data.preview === 'string') {
    return event.data.preview
  }
  return ''
}

function summary(events: AssistantTraceEvent[], tr: (key: string, options?: Record<string, unknown>) => string): string {
  const terminal = [...events].reverse().find((event) => ['done', 'error', 'paused', 'cancelled'].includes(event.event))
  const duration = Number(terminal?.data.duration_ms || 0)
  const status = terminal?.event === 'done'
    ? tr('status.done')
    : terminal?.event === 'cancelled'
    ? tr('status.cancelled')
    : terminal?.event === 'error'
    ? tr('status.error')
    : terminal?.event === 'paused'
    ? tr('status.paused')
    : tr('status.running')
  const details = [formatDuration(duration)].filter(Boolean)
  return details.length > 0 ? `${status} · ${details.join(' · ')}` : status
}

function developerSummary(events: AssistantTraceEvent[], tr: (key: string, options?: Record<string, unknown>) => string): string {
  const toolCalls = events.filter((event) => event.event === 'tool_call_start').length
  const totalTokens = events
    .filter((event) => event.event === 'model_usage')
    .reduce((total, event) => total + Number(
      event.data.total_tokens
      || Number(event.data.input_tokens || 0) + Number(event.data.output_tokens || 0),
    ), 0)
  return [
    toolCalls > 0 ? tr('toolCalls', { count: toolCalls }) : '',
    totalTokens > 0 ? `${totalTokens} tokens` : '',
  ].filter(Boolean).join(' · ')
}

function userVisible(event: AssistantTraceEvent): boolean {
  return !['model_usage', 'extension_event'].includes(event.event)
}

export function TraceTimeline({ events, live = false, reviewRequest = 0 }: TraceTimelineProps) {
  const { t } = useTranslation('configuration')
  const traceT = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const tr = (key: string, options?: Record<string, unknown>) => traceT(`p4.components.trace.${key}`, options)
  const [expanded, setExpanded] = useState(live)
  const [developerExpanded, setDeveloperExpanded] = useState(false)
  useEffect(() => {
    if (reviewRequest > 0) { setExpanded(true); setDeveloperExpanded(true) }
  }, [reviewRequest])
  const userEvents = events.filter(userVisible)
  if (events.length === 0) return null
  return (
    <section data-testid="assistant-trace" className="rounded-lg border border-border/50 bg-background/70 px-3 py-2 text-[11px]">
      <button
        type="button"
        className="flex w-full items-center gap-1.5 text-left font-medium text-muted-foreground hover:text-foreground"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <FileClock className="h-3.5 w-3.5 shrink-0" />
        <span>{tr('events.title')}</span>
        <span className="min-w-0 flex-1 truncate text-[10px] font-normal">{summary(events, tr)}</span>
        <ChevronDown className={`h-3.5 w-3.5 shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`} />
      </button>
      {expanded && (
        <ol className="mt-2 space-y-1.5 border-t border-border/40 pt-2">
          {userEvents.map((event, index) => {
            const { label, icon: Icon } = describe(event, tr)
            return (
              <li key={`${event.id ?? index}-${event.event}-${index}`} className="flex items-start gap-2 text-foreground">
                <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <div className="min-w-0">
                  <div>{label}</div>
                </div>
              </li>
            )
          })}
          <li className="border-t border-border/40 pt-1.5">
            <button
              type="button"
              className="flex w-full items-center gap-1.5 text-left text-[10px] text-muted-foreground hover:text-foreground"
              aria-expanded={developerExpanded}
              onClick={() => setDeveloperExpanded((value) => !value)}
            >
              <Wrench className="h-3 w-3" />
              <span>{tr('developer.title')}</span>
              <span className="flex-1 truncate">{developerSummary(events, tr)}</span>
              <ChevronDown className={`h-3 w-3 transition-transform ${developerExpanded ? 'rotate-180' : ''}`} />
            </button>
            {developerExpanded && (
              <ol className="mt-1.5 space-y-1.5 rounded-md bg-muted/30 p-2">
                {events.map((event, index) => {
                  const { label, icon: Icon } = describe(event, tr)
                  const eventDetail = detail(event)
                  return (
                    <li key={`developer-${event.id ?? index}-${event.event}`} className="flex items-start gap-2">
                      <Icon className="h-3 w-3 shrink-0 text-muted-foreground" />
                      <div className="min-w-0">
                        <div>{label}</div>
                        {eventDetail && <div className="break-words font-mono text-[9px] text-muted-foreground">{eventDetail}</div>}
                      </div>
                    </li>
                  )
                })}
              </ol>
            )}
          </li>
        </ol>
      )}
    </section>
  )
}
