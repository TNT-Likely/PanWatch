import { AlertCircle, CheckCircle2, FileClock, ListTree, PauseCircle, Wrench } from 'lucide-react'
import type { AssistantTraceEvent } from '@panwatch/api'

interface TraceTimelineProps {
  events: AssistantTraceEvent[]
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

export function TraceTimeline({ events }: TraceTimelineProps) {
  if (events.length === 0) return null
  return (
    <section data-testid="assistant-trace" className="rounded-lg border border-border/50 bg-background/70 px-3 py-2 text-[11px]">
      <div className="mb-1.5 flex items-center gap-1.5 font-medium text-muted-foreground">
        <FileClock className="h-3.5 w-3.5" />执行记录
      </div>
      <ol className="space-y-1.5">
        {events.map((event, index) => {
          const { label, icon: Icon } = describe(event)
          return (
            <li key={`${event.id ?? index}-${event.event}-${index}`} className="flex items-center gap-2 text-foreground">
              <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span>{label}</span>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
