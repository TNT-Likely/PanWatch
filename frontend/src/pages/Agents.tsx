import { useState, useEffect } from 'react'
import { Play, Power, Clock, Cpu, Bot, Bell, Settings2 } from 'lucide-react'
import { fetchAPI, type AIService, type NotifyChannel } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Badge } from '@panwatch/base-ui/components/ui/badge'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectGroup, SelectLabel, SelectItem } from '@panwatch/base-ui/components/ui/select'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

interface AgentConfig {
  id: number
  name: string
  display_name: string
  description: string
  enabled: boolean
  schedule: string
  execution_mode: string
  ai_model_id: number | null
  notify_channel_ids: number[]
  config: Record<string, unknown>
}

interface StockAgentInfo {
  agent_name: string
  schedule: string
  ai_model_id: number | null
  notify_channel_ids: number[]
}

interface StockConfig {
  id: number
  symbol: string
  name: string
  market: string
  agents: StockAgentInfo[]
}

interface SchedulePreview {
  schedule: string
  timezone: string
  next_runs: string[]
}

interface AgentRun {
  id: number
  agent_name: string
  status: string
  result: string
  error: string
  duration_ms: number
  created_at: string
}

interface AgentsHealth {
  timezone: string
  summary: {
    next_24h_count: number
    recent_failed_count: number
  }
  agents: Array<{
    name: string
    display_name: string
    enabled: boolean
    schedule: string
    execution_mode: string
    next_runs: string[]
    last_run: null | {
      status: string
      created_at: string
      duration_ms: number
      error: string
    }
  }>
}

// 调度类型
type ScheduleType = 'daily' | 'weekdays' | 'interval' | 'cron'

interface ScheduleConfig {
  type: ScheduleType
  time?: string      // HH:MM 格式
  interval?: number  // 分钟数
  cron?: string      // 自定义 cron
}

// cron 转友好配置
function parseCronToConfig(cron: string): ScheduleConfig {
  if (!cron) return { type: 'daily', time: '15:30' }

  const parts = cron.trim().split(/\s+/)
  if (parts.length !== 5) return { type: 'cron', cron }

  const [minute, hour, , , dayOfWeek] = parts

  // 检测间隔模式 */N
  if (minute.startsWith('*/')) {
    const interval = parseInt(minute.slice(2))
    if (!isNaN(interval)) return { type: 'interval', interval }
  }

  // 检测每天或工作日
  const m = parseInt(minute)
  const h = parseInt(hour)
  if (!isNaN(m) && !isNaN(h)) {
    const time = `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}`
    if (dayOfWeek === '1-5') return { type: 'weekdays', time }
    if (dayOfWeek === '*') return { type: 'daily', time }
  }

  return { type: 'cron', cron }
}

// 友好配置转 cron
function configToCron(config: ScheduleConfig): string {
  switch (config.type) {
    case 'daily': {
      const [h, m] = (config.time || '15:30').split(':')
      return `${parseInt(m)} ${parseInt(h)} * * *`
    }
    case 'weekdays': {
      const [h, m] = (config.time || '15:30').split(':')
      return `${parseInt(m)} ${parseInt(h)} * * 1-5`
    }
    case 'interval':
      return `*/${config.interval || 30} * * * *`
    case 'cron':
      return config.cron || '0 15 * * *'
    default:
      return '0 15 * * *'
  }
}

// 友好显示调度
function formatSchedule(cron: string): string {
  const config = parseCronToConfig(cron)
  switch (config.type) {
    case 'daily':
      return `每天 ${config.time}`
    case 'weekdays':
      return `工作日 ${config.time}`
    case 'interval':
      return `每 ${config.interval} 分钟`
    case 'cron':
      return cron
    default:
      return cron
  }
}

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentConfig[]>([])
  const [stocks, setStocks] = useState<StockConfig[]>([])
  const [services, setServices] = useState<AIService[]>([])
  const [channels, setChannels] = useState<NotifyChannel[]>([])
  const [loading, setLoading] = useState(true)
  const [triggering, setTriggering] = useState<string | null>(null)

  const [bindDialogAgent, setBindDialogAgent] = useState<AgentConfig | null>(null)
  const [bindKeyword, setBindKeyword] = useState('')
  const [bindFilter, setBindFilter] = useState<'all' | 'bound' | 'unbound'>('all')
  const [bindSavingStockIds, setBindSavingStockIds] = useState<Set<number>>(new Set())

  const [health, setHealth] = useState<AgentsHealth | null>(null)
  const [healthLoading, setHealthLoading] = useState(false)

  const [previews, setPreviews] = useState<Record<string, SchedulePreview | { error: string }>>({})

  // 调度编辑弹窗
  const [scheduleDialogAgent, setScheduleDialogAgent] = useState<AgentConfig | null>(null)
  const [scheduleConfig, setScheduleConfig] = useState<ScheduleConfig>({ type: 'daily', time: '15:30' })
  const [schedulePreview, setSchedulePreview] = useState<SchedulePreview | { error: string } | null>(null)
  const [schedulePreviewLoading, setSchedulePreviewLoading] = useState(false)

  const [runsOpen, setRunsOpen] = useState<Record<string, boolean>>({})
  const [runsLoading, setRunsLoading] = useState<Record<string, boolean>>({})
  const [runs, setRuns] = useState<Record<string, AgentRun[] | { error: string }>>({})

  const { toast } = useToast()

  const formatPreviewTime = (iso: string, tz?: string): string => {
    try {
      const d = new Date(iso)
      if (isNaN(d.getTime())) return iso
      return d.toLocaleString('zh-CN', {
        timeZone: tz || undefined,
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      })
    } catch {
      return iso
    }
  }

  const load = async () => {
    try {
      const [agentData, stockData, servicesData, channelData] = await Promise.all([
        fetchAPI<AgentConfig[]>('/agents'),
        fetchAPI<StockConfig[]>('/stocks'),
        fetchAPI<AIService[]>('/providers/services'),
        fetchAPI<NotifyChannel[]>('/channels'),
      ])
      setAgents(agentData)
      setStocks(stockData)
      setServices(servicesData)
      setChannels(channelData)

      // 预加载未来触发时间（避免“工作日/周末”语义误解）
      const previewPairs = await Promise.all(agentData.map(async a => {
        if (!a.schedule) return [a.name, { schedule: '', timezone: '', next_runs: [] }] as const
        try {
          const p = await fetchAPI<SchedulePreview>(`/agents/${a.name}/schedule/preview?count=3`)
          return [a.name, p] as const
        } catch (e) {
          const msg = e instanceof Error ? e.message : '预览失败'
          return [a.name, { error: msg }] as const
        }
      }))
      setPreviews(Object.fromEntries(previewPairs))
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  const loadHealth = async () => {
    setHealthLoading(true)
    try {
      const h = await fetchAPI<AgentsHealth>('/agents/health')
      setHealth(h)
    } catch (e) {
      console.error(e)
      setHealth(null)
    } finally {
      setHealthLoading(false)
    }
  }

  useEffect(() => { load(); loadHealth() }, [])

  // 调度编辑弹窗：实时预览未来触发时间（防止工作日/周末语义误解）
  useEffect(() => {
    if (!scheduleDialogAgent) {
      setSchedulePreview(null)
      return
    }

    const cron = configToCron(scheduleConfig)
    const timer = setTimeout(async () => {
      setSchedulePreviewLoading(true)
      try {
        const p = await fetchAPI<SchedulePreview>(`/agents/schedule/preview?schedule=${encodeURIComponent(cron)}&count=5`)
        setSchedulePreview(p)
      } catch (e) {
        const msg = e instanceof Error ? e.message : '预览失败'
        setSchedulePreview({ error: msg })
      } finally {
        setSchedulePreviewLoading(false)
      }
    }, 350)

    return () => clearTimeout(timer)
  }, [scheduleDialogAgent, scheduleConfig])

  const toggleAgent = async (agent: AgentConfig) => {
    await fetchAPI(`/agents/${agent.name}`, {
      method: 'PUT',
      body: JSON.stringify({ enabled: !agent.enabled }),
    })
    load()
  }

  const openBindDialog = (agent: AgentConfig) => {
    setBindDialogAgent(agent)
    setBindKeyword('')
    setBindFilter('all')
  }

  const hasAgentBound = (stock: StockConfig, agentName: string) =>
    (stock.agents || []).some(a => a.agent_name === agentName)

  const getAgentBoundCount = (agentName: string) =>
    stocks.filter(s => hasAgentBound(s, agentName)).length

  const getBoundStocks = (agentName: string) =>
    stocks.filter(s => hasAgentBound(s, agentName))

  const filteredBindStocks = stocks
    .filter(s => {
      const q = bindKeyword.trim().toLowerCase()
      if (!q) return true
      return s.symbol.toLowerCase().includes(q) || s.name.toLowerCase().includes(q)
    })
    .filter(s => {
      if (!bindDialogAgent) return true
      const bound = hasAgentBound(s, bindDialogAgent.name)
      if (bindFilter === 'bound') return bound
      if (bindFilter === 'unbound') return !bound
      return true
    })

  const updateBindSaving = (stockId: number, saving: boolean) => {
    setBindSavingStockIds(prev => {
      const next = new Set(prev)
      if (saving) next.add(stockId)
      else next.delete(stockId)
      return next
    })
  }

  const buildNextAgents = (stock: StockConfig, agentName: string, shouldBind: boolean) => {
    const current = stock.agents || []
    const exists = current.some(a => a.agent_name === agentName)
    if (shouldBind && !exists) {
      return [...current, { agent_name: agentName, schedule: '', ai_model_id: null, notify_channel_ids: [] }]
    }
    if (!shouldBind && exists) {
      return current.filter(a => a.agent_name !== agentName)
    }
    return current
  }

  const toggleStockBindingForAgent = async (stock: StockConfig, agentName: string) => {
    if (!agentName) return
    if (bindSavingStockIds.has(stock.id)) return
    updateBindSaving(stock.id, true)
    try {
      const exists = (stock.agents || []).some(a => a.agent_name === agentName)
      const nextAgents = buildNextAgents(stock, agentName, !exists)

      const updated = await fetchAPI<StockConfig>(`/stocks/${stock.id}/agents`, {
        method: 'PUT',
        // 保留该股票已有 Agent 的 schedule/模型/通知覆盖，仅切换当前 Agent 绑定状态
        body: JSON.stringify({
          agents: nextAgents.map(a => ({
            agent_name: a.agent_name,
            schedule: a.schedule || '',
            ai_model_id: a.ai_model_id ?? null,
            notify_channel_ids: a.notify_channel_ids || [],
          })),
        }),
      })
      setStocks(prev => prev.map(s => (s.id === stock.id ? updated : s)))
    } catch (e) {
      toast(e instanceof Error ? e.message : '切换绑定失败', 'error')
    } finally {
      updateBindSaving(stock.id, false)
    }
  }

  const applyBulkBindingForAgent = async (shouldBind: boolean) => {
    if (!bindDialogAgent) return
    const target = filteredBindStocks.filter(s => hasAgentBound(s, bindDialogAgent.name) !== shouldBind)
    if (target.length === 0) {
      toast(shouldBind ? '当前筛选已全部绑定' : '当前筛选已全部解绑', 'info')
      return
    }

    setBindSavingStockIds(new Set(target.map(s => s.id)))
    try {
      const tasks = target.map(async (stock) => {
        const nextAgents = buildNextAgents(stock, bindDialogAgent.name, shouldBind)
        const updated = await fetchAPI<StockConfig>(`/stocks/${stock.id}/agents`, {
          method: 'PUT',
          body: JSON.stringify({
            agents: nextAgents.map(a => ({
              agent_name: a.agent_name,
              schedule: a.schedule || '',
              ai_model_id: a.ai_model_id ?? null,
              notify_channel_ids: a.notify_channel_ids || [],
            })),
          }),
        })
        return updated
      })
      const updatedList = await Promise.all(tasks)
      const map = new Map(updatedList.map(s => [s.id, s]))
      setStocks(prev => prev.map(s => map.get(s.id) || s))
      toast(shouldBind ? `已绑定 ${updatedList.length} 只` : `已解绑 ${updatedList.length} 只`, 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '批量操作失败', 'error')
    } finally {
      setBindSavingStockIds(new Set())
    }
  }

  const triggerAgent = async (name: string) => {
    setTriggering(name)
    try {
      const res = await fetchAPI<{ queued?: boolean; message?: string }>(`/agents/${name}/trigger`, { method: 'POST' })
      toast(res?.queued ? 'Agent 已提交后台执行' : (res?.message || 'Agent 已触发'), 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '触发失败', 'error')
    } finally {
      setTriggering(null)
    }
  }

  const toggleRuns = async (agentName: string) => {
    const nextOpen = !runsOpen[agentName]
    setRunsOpen(prev => ({ ...prev, [agentName]: nextOpen }))
    if (!nextOpen) return

    if (runs[agentName] || runsLoading[agentName]) return
    setRunsLoading(prev => ({ ...prev, [agentName]: true }))
    try {
      const data = await fetchAPI<AgentRun[]>(`/agents/${agentName}/history?limit=5`)
      setRuns(prev => ({ ...prev, [agentName]: data }))
    } catch (e) {
      const msg = e instanceof Error ? e.message : '加载失败'
      setRuns(prev => ({ ...prev, [agentName]: { error: msg } }))
    } finally {
      setRunsLoading(prev => ({ ...prev, [agentName]: false }))
    }
  }

  const updateAgentModel = async (agent: AgentConfig, modelId: number | null) => {
    await fetchAPI(`/agents/${agent.name}`, {
      method: 'PUT',
      body: JSON.stringify({ ai_model_id: modelId }),
    })
    load()
  }

  const toggleAgentChannel = async (agent: AgentConfig, channelId: number) => {
    const current = agent.notify_channel_ids || []
    const newIds = current.includes(channelId)
      ? current.filter(id => id !== channelId)
      : [...current, channelId]
    await fetchAPI(`/agents/${agent.name}`, {
      method: 'PUT',
      body: JSON.stringify({ notify_channel_ids: newIds }),
    })
    load()
  }

  const openScheduleDialog = (agent: AgentConfig) => {
    setScheduleDialogAgent(agent)
    setScheduleConfig(parseCronToConfig(agent.schedule))
  }

  const saveSchedule = async () => {
    if (!scheduleDialogAgent) return
    const cron = configToCron(scheduleConfig)
    await fetchAPI(`/agents/${scheduleDialogAgent.name}`, {
      method: 'PUT',
      body: JSON.stringify({ schedule: cron }),
    })
    setScheduleDialogAgent(null)
    load()
    toast('调度已更新', 'success')
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <span className="w-5 h-5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div>
      <div className="mb-4 md:mb-8">
        <h1 className="text-[20px] md:text-[22px] font-bold text-foreground tracking-tight">Agent</h1>
        <p className="text-[12px] md:text-[13px] text-muted-foreground mt-0.5 md:mt-1">自动化任务管理与调度</p>
      </div>

      {/* Scheduler Health */}
      <div className="card p-4 mb-4">
        <div className="flex items-center justify-between">
          <div className="text-[13px] font-semibold text-foreground">调度健康</div>
          <Button variant="secondary" size="sm" className="h-8" onClick={loadHealth} disabled={healthLoading}>
            {healthLoading ? (
              <span className="w-3.5 h-3.5 border-2 border-current/30 border-t-current rounded-full animate-spin" />
            ) : (
              <span className="text-[12px]">刷新</span>
            )}
          </Button>
        </div>
        {health ? (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
            <span>时区: <span className="font-mono text-foreground/90">{health.timezone}</span></span>
            <span className="opacity-50">|</span>
            <span>未来 24h 将触发: <span className="font-mono text-foreground/90">{health.summary.next_24h_count}</span></span>
            <span className="opacity-50">|</span>
            <span>最近失败: <span className={`font-mono ${health.summary.recent_failed_count > 0 ? 'text-rose-600' : 'text-foreground/90'}`}>{health.summary.recent_failed_count}</span></span>
          </div>
        ) : (
          <div className="mt-2 text-[12px] text-muted-foreground">—</div>
        )}
      </div>

      {agents.length === 0 ? (
        <div className="card flex flex-col items-center justify-center py-20">
          <div className="w-14 h-14 rounded-xl bg-primary/10 flex items-center justify-center mb-4">
            <Bot className="w-6 h-6 text-primary" />
          </div>
          <p className="text-[15px] font-semibold text-foreground">暂无 Agent</p>
          <p className="text-[13px] text-muted-foreground mt-1.5">启动后台服务后 Agent 会自动注册</p>
        </div>
      ) : (
        <div className="space-y-4">
          {agents.map(agent => {
            const modeLabel = agent.execution_mode === 'single' ? '逐只分析' : '批量分析'
            const preview = previews[agent.name]
            const boundStocks = getBoundStocks(agent.name)
            const boundSummary = boundStocks.length > 0
              ? `${boundStocks.slice(0, 3).map(s => s.name || s.symbol).join('、')}${boundStocks.length > 3 ? '、...更多' : ''}`
              : '未绑定股票'
            return (
              <div key={agent.name} className="card-hover p-4 md:p-6">
                <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4 sm:gap-6">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3">
                      <div className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${agent.enabled ? 'bg-emerald-500' : 'bg-border'}`} />
                      <h3 className="text-[15px] font-semibold text-foreground">{agent.display_name}</h3>
                      <Badge variant="secondary" className="text-[10px]">{modeLabel}</Badge>
                      <button
                        type="button"
                        onClick={() => openBindDialog(agent)}
                        className={`max-w-[320px] truncate px-2 py-0.5 rounded-md border text-[11px] transition-colors ${
                          boundStocks.length > 0
                            ? 'bg-primary/12 border-primary/35 text-primary hover:bg-primary/18'
                            : 'bg-accent/30 border-border/60 text-muted-foreground hover:border-primary/30'
                        }`}
                        title={`${boundSummary}（已绑定 ${getAgentBoundCount(agent.name)} / ${stocks.length}）`}
                      >
                        {boundSummary}
                      </button>
                    </div>
                    <p className="text-[13px] text-muted-foreground mt-2.5 ml-[22px] leading-relaxed">{agent.description}</p>

                    {/* 执行周期 - 可点击编辑 */}
                    <div className="flex items-center gap-2.5 mt-3.5 ml-[22px] flex-wrap">
                      <button
                        onClick={() => openScheduleDialog(agent)}
                        className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-accent/50 hover:bg-accent transition-colors"
                      >
                        <Clock className="w-3.5 h-3.5 text-muted-foreground" />
                        <span className="text-[12px] text-foreground">{formatSchedule(agent.schedule)}</span>
                        <Settings2 className="w-3 h-3 text-muted-foreground/50" />
                      </button>
                    </div>

                    {/* 未来触发时间（按调度时区） */}
                    {'error' in (preview || {}) ? (
                      <div className="mt-2 ml-[22px] text-[11px] text-muted-foreground">
                        未来触发时间：{(preview as { error: string }).error}
                      </div>
                    ) : (preview as SchedulePreview | undefined)?.next_runs?.length ? (
                      <div className="mt-2 ml-[22px] flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                        <span className="opacity-80">未来 3 次：</span>
                        {(preview as SchedulePreview).next_runs.map((t, i) => (
                          <span
                            key={i}
                            className="px-1.5 py-0.5 rounded border border-border/60 bg-accent/30 font-mono"
                            title={t}
                          >
                            {formatPreviewTime(t, (preview as SchedulePreview).timezone)}
                          </span>
                        ))}
                        {(preview as SchedulePreview).timezone ? (
                          <span className="opacity-60">({(preview as SchedulePreview).timezone})</span>
                        ) : null}
                      </div>
                    ) : null}

                    <div className="mt-4 ml-[22px] space-y-3">
                      {/* AI Model select */}
                      <div className="flex items-center gap-2">
                        <Cpu className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
                        <Select
                          value={agent.ai_model_id?.toString() ?? '__default__'}
                          onValueChange={val => updateAgentModel(agent, val === '__default__' ? null : parseInt(val))}
                        >
                          <SelectTrigger className="h-7 text-[12px] w-auto min-w-[140px] px-2.5 bg-accent/50 border-border/50">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="__default__">系统默认</SelectItem>
                            {services.map(svc => (
                              <SelectGroup key={svc.id}>
                                <SelectLabel>{svc.name}</SelectLabel>
                                {svc.models.map(m => (
                                  <SelectItem key={m.id} value={m.id.toString()}>
                                    {m.name}{m.name !== m.model ? ` (${m.model})` : ''}
                                  </SelectItem>
                                ))}
                              </SelectGroup>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>

                      {/* Notify Channel multi-select */}
                      {channels.length > 0 && (
                        <div className="flex items-center gap-2 flex-wrap">
                          <Bell className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
                          {channels.map(ch => {
                            const isSelected = (agent.notify_channel_ids || []).includes(ch.id)
                            return (
                              <button
                                key={ch.id}
                                onClick={() => toggleAgentChannel(agent, ch.id)}
                                className={`text-[11px] px-2 py-0.5 rounded-md border transition-colors ${
                                  isSelected
                                    ? 'bg-primary/10 border-primary/30 text-primary font-medium'
                                    : 'bg-accent/30 border-border/50 text-muted-foreground hover:border-primary/30'
                                }`}
                              >
                                {ch.name}
                              </button>
                            )
                          })}
                          {(agent.notify_channel_ids || []).length === 0 && (
                            <span className="text-[11px] text-muted-foreground">系统默认</span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0 ml-[22px] sm:ml-0">
                    <Button
                      variant="secondary"
                      size="sm"
                      className="h-8"
                      onClick={() => triggerAgent(agent.name)}
                      disabled={!agent.enabled || triggering === agent.name}
                    >
                      {triggering === agent.name ? (
                        <span className="w-3.5 h-3.5 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                      ) : (
                        <Play className="w-3.5 h-3.5" />
                      )}
                      <span className="hidden sm:inline">{triggering === agent.name ? '运行中' : '触发'}</span>
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      className="h-8"
                      onClick={() => toggleRuns(agent.name)}
                    >
                      <span className="text-[12px]">最近运行</span>
                    </Button>
                    <Button
                      variant={agent.enabled ? 'destructive' : 'default'}
                      size="sm"
                      className="h-8"
                      onClick={() => toggleAgent(agent)}
                    >
                      <Power className="w-3.5 h-3.5" />
                      <span className="hidden sm:inline">{agent.enabled ? '停用' : '启用'}</span>
                    </Button>
                  </div>
                </div>

                {runsOpen[agent.name] && (
                  <div className="mt-4 ml-[22px] sm:ml-0 rounded-lg border border-border/40 bg-accent/20 p-3">
                    <div className="flex items-center justify-between">
                      <div className="text-[12px] font-medium text-foreground">最近 5 次运行</div>
                      {runsLoading[agent.name] && (
                        <span className="w-3.5 h-3.5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
                      )}
                    </div>
                    {(() => {
                      const data = runs[agent.name]
                      if (!data) {
                        return <div className="mt-2 text-[11px] text-muted-foreground">加载中…</div>
                      }
                      if ('error' in data) {
                        return <div className="mt-2 text-[11px] text-muted-foreground">{data.error}</div>
                      }
                      if (data.length === 0) {
                        return <div className="mt-2 text-[11px] text-muted-foreground">暂无记录</div>
                      }
                      return (
                        <div className="mt-2 space-y-2">
                          {data.map(r => (
                            <div key={r.id} className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <div className="text-[11px] text-muted-foreground">
                                  <span className={`inline-block w-1.5 h-1.5 rounded-full mr-2 ${r.status === 'failed' ? 'bg-rose-500' : 'bg-emerald-500'}`} />
                                  <span className="font-mono">{r.created_at}</span>
                                  <span className="ml-2 font-mono opacity-70">{Math.round((r.duration_ms || 0) / 1000)}s</span>
                                </div>
                                {r.error ? (
                                  <div className="mt-0.5 text-[11px] text-rose-600 break-words">{r.error}</div>
                                ) : null}
                              </div>
                              <div className="text-[10px] text-muted-foreground/70 font-mono">{r.status}</div>
                            </div>
                          ))}
                        </div>
                      )
                    })()}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* 调度设置弹窗 */}
      <Dialog open={!!scheduleDialogAgent} onOpenChange={open => !open && setScheduleDialogAgent(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>设置执行周期</DialogTitle>
            <DialogDescription>{scheduleDialogAgent?.display_name}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label>调度类型</Label>
              <Select
                value={scheduleConfig.type}
                onValueChange={val => setScheduleConfig({ ...scheduleConfig, type: val as ScheduleType })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="daily">每天定时</SelectItem>
                  <SelectItem value="weekdays">工作日定时</SelectItem>
                  <SelectItem value="interval">固定间隔</SelectItem>
                  <SelectItem value="cron">自定义 Cron</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {(scheduleConfig.type === 'daily' || scheduleConfig.type === 'weekdays') && (
              <div>
                <Label>执行时间</Label>
                <Input
                  type="time"
                  value={scheduleConfig.time || '15:30'}
                  onChange={e => setScheduleConfig({ ...scheduleConfig, time: e.target.value })}
                />
                <p className="text-[11px] text-muted-foreground mt-1">
                  {scheduleConfig.type === 'weekdays' ? '周一至周五' : '每天'}在此时间执行
                </p>
              </div>
            )}

            {scheduleConfig.type === 'interval' && (
              <div>
                <Label>执行间隔（分钟）</Label>
                <Select
                  value={(scheduleConfig.interval || 30).toString()}
                  onValueChange={val => setScheduleConfig({ ...scheduleConfig, interval: parseInt(val) })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="5">每 5 分钟</SelectItem>
                    <SelectItem value="10">每 10 分钟</SelectItem>
                    <SelectItem value="15">每 15 分钟</SelectItem>
                    <SelectItem value="30">每 30 分钟</SelectItem>
                    <SelectItem value="60">每小时</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}

            {scheduleConfig.type === 'cron' && (
              <div>
                <Label>Cron 表达式</Label>
                <Input
                  value={scheduleConfig.cron || ''}
                  onChange={e => setScheduleConfig({ ...scheduleConfig, cron: e.target.value })}
                  placeholder="0 15 * * 1-5"
                  className="font-mono"
                />
                <p className="text-[11px] text-muted-foreground mt-1">
                  格式：分 时 日 月 周（如 0 15 * * 1-5 表示工作日 15:00）
                </p>
              </div>
            )}

            {/* Preview */}
            <div className="rounded-lg border border-border/50 bg-accent/20 p-3">
              <div className="flex items-center justify-between">
                <div className="text-[12px] font-medium text-foreground">未来触发时间预览</div>
                {schedulePreviewLoading && (
                  <span className="w-3.5 h-3.5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
                )}
              </div>
              {'error' in (schedulePreview || {}) ? (
                <div className="mt-2 text-[11px] text-muted-foreground">
                  {(schedulePreview as { error: string }).error}
                </div>
              ) : (schedulePreview as SchedulePreview | null)?.next_runs?.length ? (
                <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                  {(schedulePreview as SchedulePreview).next_runs.map((t, i) => (
                    <span
                      key={i}
                      className="px-1.5 py-0.5 rounded border border-border/60 bg-background/40 font-mono"
                      title={t}
                    >
                      {formatPreviewTime(t, (schedulePreview as SchedulePreview).timezone)}
                    </span>
                  ))}
                  {(schedulePreview as SchedulePreview | null)?.timezone ? (
                    <span className="opacity-60">({(schedulePreview as SchedulePreview).timezone})</span>
                  ) : null}
                </div>
              ) : (
                <div className="mt-2 text-[11px] text-muted-foreground">—</div>
              )}
              <div className="mt-2 text-[11px] text-muted-foreground/70 font-mono">
                schedule: {configToCron(scheduleConfig)}
              </div>
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setScheduleDialogAgent(null)}>取消</Button>
              <Button onClick={saveSchedule}>保存</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={!!bindDialogAgent} onOpenChange={(open) => !open && setBindDialogAgent(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {bindDialogAgent ? `${bindDialogAgent.display_name} 股票绑定` : '股票绑定'}
            </DialogTitle>
            <DialogDescription>点击即可切换绑定/不绑定，不会覆盖该股票的其它 Agent 个性化配置</DialogDescription>
          </DialogHeader>
          <div className="space-y-3 mt-2">
            <div>
              <Label>筛选股票</Label>
              <Input
                value={bindKeyword}
                onChange={(e) => setBindKeyword(e.target.value)}
                placeholder="按代码或名称筛选"
              />
            </div>

            <div className="flex items-center justify-between gap-2 flex-wrap">
              <div className="flex items-center gap-1.5">
                <Button variant={bindFilter === 'all' ? 'default' : 'secondary'} size="sm" className="h-7 text-[11px]" onClick={() => setBindFilter('all')}>全部</Button>
                <Button variant={bindFilter === 'bound' ? 'default' : 'secondary'} size="sm" className="h-7 text-[11px]" onClick={() => setBindFilter('bound')}>已绑定</Button>
                <Button variant={bindFilter === 'unbound' ? 'default' : 'secondary'} size="sm" className="h-7 text-[11px]" onClick={() => setBindFilter('unbound')}>未绑定</Button>
              </div>
              <div className="flex items-center gap-1.5">
                <Button variant="secondary" size="sm" className="h-7 text-[11px]" disabled={!bindDialogAgent || bindSavingStockIds.size > 0} onClick={() => applyBulkBindingForAgent(true)}>批量绑定</Button>
                <Button variant="secondary" size="sm" className="h-7 text-[11px]" disabled={!bindDialogAgent || bindSavingStockIds.size > 0} onClick={() => applyBulkBindingForAgent(false)}>批量解绑</Button>
              </div>
            </div>

            <div className="max-h-[40vh] overflow-y-auto rounded border border-border/50 p-3">
              {filteredBindStocks.length === 0 ? (
                <div className="p-4 text-[12px] text-muted-foreground text-center">无可选股票</div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {filteredBindStocks.map((s) => {
                    const bound = bindDialogAgent ? hasAgentBound(s, bindDialogAgent.name) : false
                    const saving = bindSavingStockIds.has(s.id)
                    return (
                      <button
                        key={s.id}
                        type="button"
                        disabled={!bindDialogAgent || saving}
                        onClick={() => bindDialogAgent && toggleStockBindingForAgent(s, bindDialogAgent.name)}
                        className={`h-8 px-3 rounded-full text-[12px] border transition-colors disabled:opacity-60 ${
                          bound
                            ? 'bg-primary/12 border-primary/35 text-primary hover:bg-primary/18'
                            : 'bg-accent/30 border-border/60 text-muted-foreground hover:border-primary/30'
                        }`}
                        title={`${s.name} (${s.symbol})`}
                      >
                        {saving ? '处理中...' : `${s.name || s.symbol}`}
                      </button>
                    )
                  })}
                </div>
              )}
            </div>

            <div className="flex justify-end gap-2 pt-1">
              <Button variant="ghost" onClick={() => setBindDialogAgent(null)}>关闭</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
