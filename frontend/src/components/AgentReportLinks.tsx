import type { InsightTab, ReportTab } from '@panwatch/biz-ui/components/stock-insight-modal'

export interface ReportDestination { tab: InsightTab; reportTab?: ReportTab }

export function agentReportDestination(name: string): ReportDestination {
  if (name === 'tradingagents') return { tab: 'deep' }
  if (name === 'intraday_monitor') return { tab: 'suggestions' }
  if (name === 'daily_report' || name === 'premarket_outlook' || name === 'news_digest') {
    return { tab: 'reports', reportTab: name }
  }
  return { tab: 'reports' }
}

export function AgentReportLinks({ agents, labels, running, onOpen }: {
  agents: { agent_name: string }[]
  labels: { name: string; display_name: string }[]
  running?: string | null
  onOpen: (destination: ReportDestination) => void
}) {
  return <div className="flex items-center gap-1.5 flex-wrap">
    {agents.length ? agents.map(agent => <button
      key={agent.agent_name}
      type="button"
      className="text-[11px] text-primary hover:underline"
      onClick={event => { event.stopPropagation(); onOpen(agentReportDestination(agent.agent_name)) }}
    >
      {labels.find(label => label.name === agent.agent_name)?.display_name || agent.agent_name}
      {running === agent.agent_name && <span className="text-amber-600"> · 执行中</span>}
    </button>) : <button type="button" className="text-[11px] text-primary hover:underline"
      onClick={event => { event.stopPropagation(); onOpen({ tab: 'reports' }) }}
    >查看报告 / 立即分析</button>}
  </div>
}
