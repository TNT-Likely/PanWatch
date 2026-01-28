import { useEffect, useRef, useState, useCallback } from 'react'
import { Search, Trash2, RefreshCw, ScrollText } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { fetchAPI } from '@/lib/utils'
import { mapLoggerName, loggerOptions } from '@/lib/logger-map'
import { useLocalStorage } from '@/lib/utils'

interface LogEntry {
  id: number
  timestamp: string
  level: string
  logger_name: string
  message: string
}

interface LogListResponse {
  items: LogEntry[]
  total: number
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

export default function LogsModal({ open, onOpenChange }: { open: boolean, onOpenChange: (v: boolean) => void }) {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [loadedOnce, setLoadedOnce] = useState(false)
  const [query, setQuery] = useState('')
  const [selectedLevels, setSelectedLevels] = useState<string[]>([])
  const [timeRange, setTimeRange] = useState(0)
  const [selectedLoggers, setSelectedLoggers] = useState<string[]>([])
  const [autoRefresh, setAutoRefresh] = useLocalStorage('panwatch_logs_modal_autoRefresh', false)
  const [offset, setOffset] = useState(0)
  const refreshTimer = useRef<ReturnType<typeof setInterval>>()
  const searchTimer = useRef<ReturnType<typeof setTimeout>>()
  const limit = 200

  const load = useCallback(async (currentOffset = 0) => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (selectedLevels.length > 0) params.set('level', selectedLevels.join(','))
      if (selectedLoggers.length > 0) params.set('logger', selectedLoggers.join(','))
      if (query) params.set('q', query)
      if (timeRange > 0) {
        const since = new Date(Date.now() - timeRange * 3600 * 1000).toISOString()
        params.set('since', since)
      }
      params.set('limit', String(limit))
      params.set('offset', String(currentOffset))
      const data = await fetchAPI<LogListResponse>(`/logs?${params.toString()}`)
      setLogs(data.items)
      setTotal(data.total)
      setLoadedOnce(true)
    } catch (e) {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [selectedLevels, selectedLoggers, query, timeRange])

  // 初次打开或过滤变更时刷新
  useEffect(() => {
    if (!open) return
    setOffset(0)
    load(0)
  }, [open, load])

  // 自动刷新
  useEffect(() => {
    if (open && autoRefresh) {
      refreshTimer.current = setInterval(() => load(offset), 3000)
    }
    return () => { if (refreshTimer.current) clearInterval(refreshTimer.current) }
  }, [open, autoRefresh, load, offset])

  const handleSearchInput = (value: string) => {
    setQuery(value)
    clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => setOffset(0), 300)
  }

  const toggleLevel = (level: string) => {
    setSelectedLevels(prev => prev.includes(level) ? prev.filter(l => l !== level) : [...prev, level])
    setOffset(0)
  }

  const toggleLogger = (key: string) => {
    setSelectedLoggers(prev => prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key])
    setOffset(0)
  }

  const handleClear = async () => {
    if (!confirm('确定清空所有日志？')) return
    await fetchAPI('/logs', { method: 'DELETE' })
    setLogs([]); setTotal(0)
  }

  const handlePageChange = (newOffset: number) => { setOffset(newOffset); load(newOffset) }

  const formatTime = (iso: string) => {
    if (!iso) return ''
    const d = new Date(iso)
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[90vw] max-w-[90vw] h-[90vh] max-h-[90vh]" onInteractOutside={(e) => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span>日志</span>
            <Button variant={autoRefresh ? 'default' : 'secondary'} size="sm" className="h-7" onClick={() => setAutoRefresh(v => !v)}>
              <RefreshCw className={`w-3.5 h-3.5 ${autoRefresh ? 'animate-spin' : ''}`} />
              自动刷新
            </Button>
            <Button variant="ghost" size="sm" className="h-7 hover:text-destructive hover:bg-destructive/8 ml-auto" onClick={handleClear}>
              <Trash2 className="w-3.5 h-3.5" /> 清空
            </Button>
          </DialogTitle>
        </DialogHeader>

        {/* Filters */}
        <div className="card p-3 md:p-4 mb-3 space-y-3">
          <div className="relative">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground/50" />
            <Input value={query} onChange={e => handleSearchInput(e.target.value)} placeholder="搜索日志内容..." className="pl-10" />
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {LEVELS.map(level => (
              <button
                key={level}
                onClick={() => toggleLevel(level)}
                className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-medium transition-all ${
                  selectedLevels.includes(level)
                    ? 'bg-primary text-white'
                    : 'bg-accent text-muted-foreground hover:text-foreground'
                }`}
              >
                <span className={`w-1.5 h-1.5 rounded-full ${selectedLevels.includes(level) ? 'bg-white/70' : LEVEL_DOT[level]}`} />
                {level}
              </button>
            ))}

            <span className="w-px h-5 bg-border mx-2" />

            {TIME_RANGES.map(range => (
              <button
                key={range.value}
                onClick={() => { setTimeRange(range.value); setOffset(0) }}
                className={`px-2.5 py-1.5 rounded-lg text-[11px] font-medium transition-all ${
                  timeRange === range.value
                    ? 'bg-primary text-white'
                    : 'bg-accent text-muted-foreground hover:text-foreground'
                }`}
              >
                {range.label}
              </button>
            ))}

            <span className="w-px h-5 bg-border mx-2" />

            {loggerOptions().map(opt => (
              <button
                key={opt.key}
                onClick={() => toggleLogger(opt.key)}
                className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-medium transition-all ${
                  selectedLoggers.includes(opt.key)
                    ? 'bg-primary text-white'
                    : 'bg-accent text-muted-foreground hover:text-foreground'
                }`}
                title={opt.key}
              >
                {opt.label}
              </button>
            ))}

            <span className="ml-auto text-[11px] text-muted-foreground font-medium">{total} 条记录</span>
          </div>
        </div>

        {/* Log list */}
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
          <div className="card overflow-hidden">
            <div className="overflow-x-auto max-h-[calc(90vh-220px)] overflow-y-auto relative">
              <table className="w-full text-[12px] font-mono">
                <thead className="sticky top-0 bg-card z-10 border-b border-border/50">
                  <tr>
                    <th className="text-left px-4 py-3 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider w-32">时间</th>
                    <th className="text-left px-4 py-3 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider w-20">级别</th>
                    <th className="text-left px-4 py-3 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider w-36">Logger</th>
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

            {Math.ceil(total / limit) > 1 && (
              <div className="flex items-center justify-between px-5 py-3 border-t border-border/30">
                <Button variant="ghost" size="sm" onClick={() => handlePageChange(Math.max(0, offset - limit))} disabled={offset === 0}>
                  上一页
                </Button>
                <span className="text-[12px] text-muted-foreground font-medium">{Math.floor(offset / limit) + 1} / {Math.ceil(total / limit)}</span>
                <Button variant="ghost" size="sm" onClick={() => handlePageChange(offset + limit)} disabled={(Math.floor(offset / limit) + 1) >= Math.ceil(total / limit)}>
                  下一页
                </Button>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
