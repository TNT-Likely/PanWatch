import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { Search, Trash2, RefreshCw, ScrollText, ChevronDown } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { fetchAPI } from '@panwatch/api'
import { mapLoggerName, loggerOptions } from '@/lib/logger-map'
import { useLocalStorage } from '@/lib/utils'

interface LogEntry {
  id: number
  timestamp: string
  level: string
  logger_name: string
  message: string
  trace_id?: string
  run_id?: string
  agent_name?: string
  event?: string
  notify_status?: string
  notify_reason?: string
}

interface LogListResponse {
  items: LogEntry[]
  total: number
  has_more?: boolean
  next_before_id?: number | null
}

const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
const LEVEL_DOT: Record<string, string> = {
  DEBUG: 'bg-slate-400',
  INFO: 'bg-blue-500',
  WARNING: 'bg-amber-500',
  ERROR: 'bg-red-500',
  CRITICAL: 'bg-red-700',
}
const TIME_RANGES = [
  { label: '1h', value: 1 },
  { label: '6h', value: 6 },
  { label: '24h', value: 24 },
  { label: '全部', value: 0 },
]
const DOMAIN_OPTIONS: Array<{ label: string, value: 'business' | 'all' | 'infra' }> = [
  { label: '业务优先', value: 'business' },
  { label: '全部', value: 'all' },
  { label: '基础设施', value: 'infra' },
]
const FLOW_PRESETS: Array<{ key: string, label: string, loggers: string[] }> = [
  { key: '', label: '全部链路', loggers: [] },
  {
    key: 'premarket_outlook',
    label: '盘前分析',
    loggers: ['src.agents.premarket_outlook', 'src.agents.base', 'src.core.scheduler', 'src.core.notifier'],
  },
  {
    key: 'daily_report',
    label: '收盘复盘',
    loggers: ['src.agents.daily_report', 'src.agents.base', 'src.core.scheduler', 'src.core.notifier'],
  },
  {
    key: 'intraday_monitor',
    label: '盘中监测',
    loggers: ['src.agents.intraday_monitor', 'src.agents.base', 'src.core.scheduler', 'src.core.notifier'],
  },
]

function unique(arr: string[]) {
  return Array.from(new Set(arr.filter(Boolean)))
}

export default function LogsModal({ open, onOpenChange }: { open: boolean, onOpenChange: (v: boolean) => void }) {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [loadedOnce, setLoadedOnce] = useState(false)
  const [query, setQuery] = useState('')
  const [selectedLevels, setSelectedLevels] = useState<string[]>([])
  const [timeRange, setTimeRange] = useState(0)
  const [selectedLoggers, setSelectedLoggers] = useState<string[]>([])
  const [selectedFlow, setSelectedFlow] = useState('')
  const [domain, setDomain] = useLocalStorage<'business' | 'all' | 'infra'>('panwatch_logs_modal_domain', 'business')
  const [autoRefresh, setAutoRefresh] = useLocalStorage('panwatch_logs_modal_autoRefresh', false)
  const [showAllLoggerFilters, setShowAllLoggerFilters] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [beforeId, setBeforeId] = useState<number>(0)
  const refreshTimer = useRef<ReturnType<typeof setInterval>>()
  const searchTimer = useRef<ReturnType<typeof setTimeout>>()
  const limit = 200

  const loggerPreset = useMemo(
    () => FLOW_PRESETS.find(x => x.key === selectedFlow)?.loggers || [],
    [selectedFlow],
  )
  const effectiveLoggers = useMemo(
    () => unique([...selectedLoggers, ...loggerPreset]),
    [selectedLoggers, loggerPreset],
  )

  const load = useCallback(async (opts?: { append?: boolean, cursor?: number }) => {
    const append = !!opts?.append
    const cursor = Number(opts?.cursor || 0)
    if (append && !cursor) return
    if (append) setLoadingMore(true)
    else setLoading(true)
    try {
      const params = new URLSearchParams()
      if (selectedLevels.length > 0) params.set('level', selectedLevels.join(','))
      if (effectiveLoggers.length > 0) params.set('logger', effectiveLoggers.join(','))
      if (query) params.set('q', query)
      if (domain !== 'all') params.set('domain', domain)
      if (timeRange > 0) {
        const since = new Date(Date.now() - timeRange * 3600 * 1000).toISOString()
        params.set('since', since)
      }
      params.set('limit', String(limit))
      if (append) params.set('before_id', String(cursor))
      const data = await fetchAPI<LogListResponse>(`/logs?${params.toString()}`)

      if (append) {
        const incoming = data.items || []
        setLogs(prev => {
          const seen = new Set(prev.map(x => x.id))
          return [...prev, ...incoming.filter(x => !seen.has(x.id))]
        })
      } else {
        setLogs(data.items || [])
      }
      setTotal(data.total || 0)
      setHasMore(!!data.has_more)
      const next = data.next_before_id ?? ((data.items && data.items.length > 0) ? data.items[data.items.length - 1].id : 0)
      setBeforeId(next || 0)
      setLoadedOnce(true)
    } catch {
      // ignore
    } finally {
      if (append) setLoadingMore(false)
      else setLoading(false)
    }
  }, [selectedLevels, effectiveLoggers, query, timeRange, domain])

  const loadLatest = useCallback(() => {
    setBeforeId(0)
    void load({ append: false, cursor: 0 })
  }, [load])

  // 初次打开或筛选变更时刷新（关键词搜索走防抖）
  useEffect(() => {
    if (!open) return
    loadLatest()
    // query 由 handleSearchInput 防抖触发，避免每次键入都立即请求。
  }, [open, selectedLevels, selectedLoggers, selectedFlow, domain, timeRange])

  // 自动刷新（仅刷新最新页）
  useEffect(() => {
    if (open && autoRefresh) {
      refreshTimer.current = setInterval(() => loadLatest(), 3000)
    }
    return () => { if (refreshTimer.current) clearInterval(refreshTimer.current) }
  }, [open, autoRefresh, loadLatest])

  const handleSearchInput = (value: string) => {
    setQuery(value)
    clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => loadLatest(), 300)
  }

  useEffect(() => {
    return () => {
      if (searchTimer.current) clearTimeout(searchTimer.current)
    }
  }, [])

  const toggleLevel = (level: string) => {
    setSelectedLevels(prev => prev.includes(level) ? prev.filter(l => l !== level) : [...prev, level])
  }

  const toggleLogger = (key: string) => {
    setSelectedLoggers(prev => prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key])
  }

  const clearFilters = () => {
    setSelectedLevels([])
    setTimeRange(0)
    setSelectedLoggers([])
    setSelectedFlow('')
    setDomain('business')
    setQuery('')
  }

  const handleClear = async () => {
    if (!confirm('确定清空所有日志？')) return
    await fetchAPI('/logs', { method: 'DELETE' })
    setLogs([])
    setTotal(0)
    setHasMore(false)
    setBeforeId(0)
  }

  const formatTime = (iso: string) => {
    if (!iso) return ''
    const d = new Date(iso)
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
  }

  const filterSummary = useMemo(() => {
    const parts: string[] = []
    if (query) parts.push(`关键词:${query}`)
    if (selectedLevels.length) parts.push(`级别:${selectedLevels.join(',')}`)
    if (timeRange > 0) parts.push(`时间:${timeRange}h`)
    if (domain !== 'all') parts.push(`范围:${domain === 'business' ? '业务优先' : '基础设施'}`)
    if (selectedFlow) {
      const flow = FLOW_PRESETS.find(x => x.key === selectedFlow)
      if (flow) parts.push(`链路:${flow.label}`)
    }
    if (selectedLoggers.length) parts.push(`自选Logger:${selectedLoggers.length}`)
    return parts.length > 0 ? parts.join(' | ') : '当前无额外过滤'
  }, [query, selectedLevels, timeRange, domain, selectedFlow, selectedLoggers])

  const loggerFilterOptions = loggerOptions()

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[90vw] max-w-[90vw] h-[90vh] max-h-[90vh] flex flex-col overflow-hidden" onInteractOutside={(e) => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span>日志</span>
            <Button variant={autoRefresh ? 'default' : 'secondary'} size="sm" className="h-7" onClick={() => setAutoRefresh(v => !v)}>
              <RefreshCw className={`w-3.5 h-3.5 ${autoRefresh ? 'animate-spin' : ''}`} />
              自动刷新
            </Button>
            <Button variant="outline" size="sm" className="h-7" onClick={loadLatest}>
              刷新
            </Button>
            <Button variant="ghost" size="sm" className="h-7 hover:text-destructive hover:bg-destructive/8 ml-auto" onClick={handleClear}>
              <Trash2 className="w-3.5 h-3.5" /> 清空
            </Button>
          </DialogTitle>
        </DialogHeader>

        <div className="card p-3 md:p-4 mb-3 space-y-3">
          <div className="relative">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground/50" />
            <Input value={query} onChange={e => handleSearchInput(e.target.value)} placeholder="搜索日志内容 / trace_id / logger..." className="pl-10" />
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            {DOMAIN_OPTIONS.map(opt => (
              <button
                key={opt.value}
                onClick={() => setDomain(opt.value)}
                className={`px-2.5 py-1.5 rounded-lg text-[11px] font-medium transition-all ${domain === opt.value ? 'bg-primary text-white' : 'bg-accent text-muted-foreground hover:text-foreground'}`}
              >
                {opt.label}
              </button>
            ))}
            <span className="w-px h-5 bg-border mx-2" />
            {TIME_RANGES.map(range => (
              <button
                key={range.value}
                onClick={() => setTimeRange(range.value)}
                className={`px-2.5 py-1.5 rounded-lg text-[11px] font-medium transition-all ${timeRange === range.value ? 'bg-primary text-white' : 'bg-accent text-muted-foreground hover:text-foreground'}`}
              >
                {range.label}
              </button>
            ))}
            <span className="ml-auto text-[11px] text-muted-foreground font-medium">{total} 条记录</span>
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            {LEVELS.map(level => (
              <button
                key={level}
                onClick={() => toggleLevel(level)}
                className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-medium transition-all ${selectedLevels.includes(level) ? 'bg-primary text-white' : 'bg-accent text-muted-foreground hover:text-foreground'}`}
              >
                <span className={`w-1.5 h-1.5 rounded-full ${selectedLevels.includes(level) ? 'bg-white/70' : LEVEL_DOT[level]}`} />
                {level}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            {FLOW_PRESETS.map(flow => (
              <button
                key={flow.key || 'all'}
                onClick={() => setSelectedFlow(flow.key)}
                className={`px-2.5 py-1.5 rounded-lg text-[11px] font-medium transition-all ${selectedFlow === flow.key ? 'bg-primary text-white' : 'bg-accent text-muted-foreground hover:text-foreground'}`}
              >
                {flow.label}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowAllLoggerFilters(v => !v)}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-medium bg-accent text-muted-foreground hover:text-foreground"
            >
              Logger过滤
              <ChevronDown className={`w-3.5 h-3.5 transition-transform ${showAllLoggerFilters ? 'rotate-180' : ''}`} />
            </button>
            <div className="text-[11px] text-muted-foreground">默认链路会自动包含 `src.agents.base` 决策日志</div>
          </div>
          {showAllLoggerFilters && (
            <div className="flex flex-wrap items-center gap-1.5">
              {loggerFilterOptions.map(opt => (
                <button
                  key={opt.key}
                  onClick={() => toggleLogger(opt.key)}
                  className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-medium transition-all ${selectedLoggers.includes(opt.key) ? 'bg-primary text-white' : 'bg-accent text-muted-foreground hover:text-foreground'}`}
                  title={opt.key}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          )}

          <div className="flex items-center gap-2 text-[11px]">
            <div className="flex-1 rounded-md border border-border/50 px-2.5 py-1.5 text-muted-foreground bg-background/40">
              过滤器：{filterSummary}
            </div>
            <Button variant="ghost" size="sm" className="h-7" onClick={clearFilters}>清空过滤</Button>
          </div>
        </div>

        <div className="flex-1 min-h-0">
          {!loadedOnce && loading ? (
            <div className="flex items-center justify-center py-20">
              <span className="w-5 h-5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
            </div>
          ) : logs.length === 0 ? (
            <div className="card flex flex-col items-center justify-center py-20">
              <div className="w-14 h-14 rounded-xl bg-primary/10 flex items-center justify-center mb-4">
                <ScrollText className="w-6 h-6 text-primary" />
              </div>
              <p className="text-[15px] font-semibold text-foreground">暂无日志</p>
              <p className="text-[13px] text-muted-foreground mt-1.5">后台运行后日志会自动出现在这里</p>
            </div>
          ) : (
            <div className="card overflow-hidden h-full flex flex-col">
              <div className="overflow-x-auto overflow-y-auto flex-1 min-h-0 relative scrollbar">
                <table className="w-full text-[12px] font-mono">
                  <thead className="sticky top-0 bg-card z-10 border-b border-border/50">
                    <tr>
                      <th className="text-left px-4 py-3 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider w-32">时间</th>
                      <th className="text-left px-4 py-3 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider w-20">级别</th>
                      <th className="text-left px-4 py-3 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider w-36">Logger</th>
                      <th className="text-left px-4 py-3 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider w-44">链路</th>
                      <th className="text-left px-4 py-3 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">消息</th>
                    </tr>
                  </thead>
                  <tbody>
                    {logs.map((log, i) => (
                      <tr key={log.id} className={`hover:bg-accent/30 transition-colors ${i > 0 ? 'border-t border-border/20' : ''}`}>
                        <td className="px-4 py-2 text-muted-foreground whitespace-nowrap">{formatTime(log.timestamp)}</td>
                        <td className="px-4 py-2 whitespace-nowrap">
                          <span className="inline-flex items-center gap-1.5">
                            <span className={`w-1.5 h-1.5 rounded-full ${LEVEL_DOT[log.level] || 'bg-slate-400'}`} />
                            <span className="text-muted-foreground">{log.level}</span>
                          </span>
                        </td>
                        <td className="px-4 py-2 text-muted-foreground truncate max-w-[144px]" title={log.logger_name}>{mapLoggerName(log.logger_name)}</td>
                        <td className="px-4 py-2 text-[11px] text-muted-foreground">
                          <div className="truncate" title={log.trace_id || ''}>{log.trace_id || '-'}</div>
                          <div className="truncate">{log.event || '-'}</div>
                        </td>
                        <td className="px-4 py-2 whitespace-pre-wrap break-all text-foreground/80">{log.message}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {loading && loadedOnce && (
                  <div className="absolute top-2 right-4">
                    <span className="w-4 h-4 border-2 border-primary/30 border-t-primary rounded-full animate-spin inline-block" />
                  </div>
                )}
              </div>

              <div className="flex items-center justify-between px-5 py-3 border-t border-border/30">
                <span className="text-[12px] text-muted-foreground">已加载 {logs.length} / {total}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!hasMore || loadingMore}
                  onClick={() => load({ append: true, cursor: beforeId })}
                >
                  {loadingMore ? '加载中...' : hasMore ? '加载更多' : '没有更多了'}
                </Button>
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
