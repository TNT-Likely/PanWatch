import { AlertCircle, CheckCircle2, ChevronDown, FileClock, ListTree, PauseCircle, Wrench } from 'lucide-react'
import { useState } from 'react'
import type { AssistantTraceEvent } from '@panwatch/api'

interface TraceTimelineProps {
  events: AssistantTraceEvent[]
  live?: boolean
}

function describe(event: AssistantTraceEvent): { label: string; icon: typeof FileClock } {
  const name = typeof event.data.name === 'string' ? event.data.name : ''
  switch (event.event) {
    case 'context_prepared': return { label: event.data.compressed ? '上下文已压缩并准备' : '上下文已准备', icon: FileClock }
    case 'step_updated': return { label: `执行步骤 ${event.data.step || ''}`, icon: ListTree }
    case 'tool_call_start': return { label: `调用工具：${name}`, icon: Wrench }
    case 'tool_result': return { label: event.data.ok ? `工具完成：${name}` : `工具失败：${name}`, icon: event.data.ok ? CheckCircle2 : AlertCircle }
    case 'approval_required': return { label: '等待用户审批', icon: PauseCircle }
    case 'paused': return { label: '任务已暂停', icon: PauseCircle }
    case 'done': return { label: '任务完成', icon: CheckCircle2 }
    case 'error': return { label: '任务失败', icon: AlertCircle }
    default: return { label: '任务已启动', icon: FileClock }
  }
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

function summary(events: AssistantTraceEvent[]): string {
  const toolCalls = events.filter((event) => event.event === 'tool_call_start').length
  const latest = [...events].reverse().find((event) => ['done', 'error', 'paused'].includes(event.event))
  const status = latest?.event === 'done'
    ? '已完成'
    : latest?.event === 'error'
    ? '已失败'
    : latest?.event === 'paused'
    ? '等待继续'
    : '执行中'
  return toolCalls > 0 ? `${status} · ${toolCalls} 次工具调用` : status
}

export function TraceTimeline({ events, live = false }: TraceTimelineProps) {
  const [expanded, setExpanded] = useState(live)
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
        <span>执行记录</span>
        <span className="min-w-0 flex-1 truncate text-[10px] font-normal">{summary(events)}</span>
        <ChevronDown className={`h-3.5 w-3.5 shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`} />
      </button>
      {expanded && (
        <ol className="mt-2 space-y-1.5 border-t border-border/40 pt-2">
          {events.map((event, index) => {
            const { label, icon: Icon } = describe(event)
            const eventDetail = detail(event)
            return (
              <li key={`${event.id ?? index}-${event.event}-${index}`} className="flex items-start gap-2 text-foreground">
                <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <div className="min-w-0">
                  <div>{label}</div>
                  {eventDetail && <div className="break-words text-muted-foreground">{eventDetail}</div>}
                </div>
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
