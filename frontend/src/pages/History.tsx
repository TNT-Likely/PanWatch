import { useConfirm } from '@panwatch/base-ui/components/ui/confirm-dialog'
import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Clock, Trash2, FileText, ArrowLeft } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import { fetchAPI } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Badge } from '@panwatch/base-ui/components/ui/badge'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@panwatch/base-ui/components/ui/select'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { useTranslation } from 'react-i18next'

interface HistoryRecord {
  id: number
  agent_name: string
  agent_kind?: 'workflow' | 'capability'
  stock_symbol: string
  analysis_date: string
  title: string
  content: string
  context_payload?: Record<string, unknown> | null
  prompt_context?: string | null
  prompt_stats?: Record<string, unknown> | null
  news_debug?: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

const WORKFLOW_AGENT_KEYS = ['daily_report', 'premarket_outlook', 'intraday_monitor', 'tradingagents']
const CAPABILITY_AGENT_KEYS = ['news_digest', 'chart_analyst']

export default function HistoryPage() {
  const { toast } = useToast()
  const { t } = useTranslation('configuration')
  const confirmAction = useConfirm()
  const historyT = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const tr = (key: string, options?: Record<string, unknown>) => historyT(`p4.history.${key}`, options)
  const agentLabel = (key: string) => {
    const label = historyT(`p4.history.agents.${key}`)
    return label === `p4.history.agents.${key}` ? key : label
  }
  const navigate = useNavigate()
  const [records, setRecords] = useState<HistoryRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedAgent, setSelectedAgent] = useState<string>('all')
  const [historyKind, setHistoryKind] = useState<'workflow' | 'capability' | 'all'>('workflow')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [mobileView, setMobileView] = useState<'list' | 'reader'>('list')
  const [detailRecord, setDetailRecord] = useState<HistoryRecord | null>(null)

  const displayTime = (record: HistoryRecord) => record.updated_at || record.created_at
  const formatDateTime = (iso?: string) => {
    if (!iso) return '--'
    const s = String(iso).trim()
    if (!s) return '--'
    // Keep original offset semantics; only normalize display format and strip fractional seconds.
    let normalized = s.replace(' ', 'T').replace(/Z$/, '+00:00')
    normalized = normalized.replace(/\.\d+(?=[+-]\d{2}:\d{2}$)/, '')
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$/.test(normalized)) {
      return normalized
    }
    const d = new Date(s)
    if (isNaN(d.getTime())) return s
    const pad = (n: number) => String(n).padStart(2, '0')
    const year = d.getFullYear()
    const month = pad(d.getMonth() + 1)
    const day = pad(d.getDate())
    const hour = pad(d.getHours())
    const minute = pad(d.getMinutes())
    const second = pad(d.getSeconds())
    const tz = -d.getTimezoneOffset()
    const sign = tz >= 0 ? '+' : '-'
    const tzHour = pad(Math.floor(Math.abs(tz) / 60))
    const tzMinute = pad(Math.abs(tz) % 60)
    return `${year}-${month}-${day}T${hour}:${minute}:${second}${sign}${tzHour}:${tzMinute}`
  }

  const formatTimeShort = (iso?: string) => {
    const full = formatDateTime(iso)
    const m = full.match(/T(\d{2}:\d{2}):\d{2}[+-]\d{2}:\d{2}$/)
    return m ? m[1] : '--:--'
  }

  const load = async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (selectedAgent && selectedAgent !== 'all') params.set('agent_name', selectedAgent)
      params.set('kind', historyKind)
      params.set('limit', '50')
      const data = await fetchAPI<HistoryRecord[]>(`/history?${params.toString()}`)
      setRecords(data || [])
    } catch (e) {
      toast(e instanceof Error ? e.message : tr('loadFailed'), 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [selectedAgent, historyKind])

  useEffect(() => {
    const available = historyKind === 'workflow'
      ? WORKFLOW_AGENT_KEYS
      : historyKind === 'capability'
        ? CAPABILITY_AGENT_KEYS
        : [...WORKFLOW_AGENT_KEYS, ...CAPABILITY_AGENT_KEYS]
    if (selectedAgent !== 'all' && !available.includes(selectedAgent)) {
      setSelectedAgent('all')
    }
  }, [historyKind, selectedAgent])

  useEffect(() => {
    if (!records.length) {
      setSelectedId(null)
      setMobileView('list')
      return
    }
    if (selectedId && records.some(r => r.id === selectedId)) return
    setSelectedId(records[0].id)
  }, [records, selectedId])

  const deleteRecord = async (id: number) => {
    if (!(await confirmAction(tr('deleteConfirm'), { destructive: true }))) return
    try {
      await fetchAPI(`/history/${id}`, { method: 'DELETE' })
      toast(tr('deleted'), 'success')
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : tr('deleteFailed'), 'error')
    }
  }

  // 格式化标题（带日期）
  const formatTitle = (record: HistoryRecord) => {
    const label = agentLabel(record.agent_name)
    if (record.title) {
      return `${record.analysis_date} ${record.title}`
    }
    return `${record.analysis_date} ${label}`
  }

  const selectedRecord = selectedId ? records.find(r => r.id === selectedId) || null : null
  const agentOptions = historyKind === 'workflow'
    ? WORKFLOW_AGENT_KEYS
    : historyKind === 'capability'
      ? CAPABILITY_AGENT_KEYS
      : [...WORKFLOW_AGENT_KEYS, ...CAPABILITY_AGENT_KEYS]

  const selectRecord = (id: number) => {
    setSelectedId(id)
    // On mobile, jump to reader view for a smoother experience
    setMobileView('reader')
    try {
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch {
      // ignore
    }
  }

  return (
    <div className="w-full space-y-4 md:space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center gap-2 md:gap-3">
          <div className="w-9 h-9 md:w-10 md:h-10 rounded-xl bg-gradient-to-br from-amber-500 to-amber-500/70 flex items-center justify-center shadow-sm">
            <Clock className="w-4 h-4 md:w-5 md:h-5 text-white" />
          </div>
          <div>
            <h1 className="text-lg md:text-xl font-bold">{tr('title')}</h1>
            <p className="text-[12px] md:text-[13px] text-muted-foreground">{tr('subtitle')}</p>
          </div>
          <div className="hidden md:flex px-2.5 py-1 rounded-full bg-background/70 border border-border/50 text-[11px] text-muted-foreground">
            {tr('recordCount', { count: records.length })}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Select value={historyKind} onValueChange={(v) => setHistoryKind(v as 'workflow' | 'capability' | 'all')}>
            <SelectTrigger className="w-full sm:w-[150px] h-9">
              <SelectValue placeholder={tr('range')} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="workflow">{tr('workflow')}</SelectItem>
              <SelectItem value="capability">{tr('capability')}</SelectItem>
              <SelectItem value="all">{tr('all')}</SelectItem>
            </SelectContent>
          </Select>
          <Select value={selectedAgent} onValueChange={setSelectedAgent}>
            <SelectTrigger className="w-full sm:w-[180px] h-9">
              <SelectValue placeholder={tr('allAgents')} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{tr('allAgents')}</SelectItem>
              {agentOptions.map((key) => (
                <SelectItem key={key} value={key}>{agentLabel(key)}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {loading ? (
        <div className="card p-12 text-center">
          <div className="w-6 h-6 border-2 border-primary/30 border-t-primary rounded-full animate-spin mx-auto" />
        </div>
      ) : records.length === 0 ? (
        <div className="card p-12 text-center">
          <FileText className="w-12 h-12 text-muted-foreground/30 mx-auto mb-3" />
          <p className="text-muted-foreground">{tr('empty')}</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-12 gap-4">
          {/* Mobile view switch */}
          <div className="md:hidden card p-2">
            <div className="grid grid-cols-2 gap-2">
              <button
                onClick={() => setMobileView('list')}
                className={`h-9 rounded-lg text-[12px] font-medium transition-colors ${mobileView === 'list' ? 'bg-primary text-white' : 'bg-accent/30 text-muted-foreground hover:bg-accent/50'}`}
              >
                {tr('toc')}
              </button>
              <button
                onClick={() => setMobileView('reader')}
                className={`h-9 rounded-lg text-[12px] font-medium transition-colors ${mobileView === 'reader' ? 'bg-primary text-white' : 'bg-accent/30 text-muted-foreground hover:bg-accent/50'}`}
                disabled={!selectedRecord}
              >
                {tr('body')}
              </button>
            </div>
          </div>

          {/* List */}
          <div className={`md:col-span-5 card overflow-hidden ${mobileView === 'reader' ? 'hidden md:block' : ''}`}>
            <div className="px-4 py-3 bg-accent/20 border-b border-border/50 text-[12px] text-muted-foreground">
              {tr('tocHint')}
            </div>
            <div className="max-h-[70vh] md:max-h-[70vh] overflow-y-auto scrollbar divide-y divide-border/50">
              {records.map(r => {
                const active = selectedId === r.id
                return (
                  <button
                    key={r.id}
                    onClick={() => selectRecord(r.id)}
                    className={`w-full text-left px-4 py-3 transition-colors ${active ? 'bg-primary/8' : 'hover:bg-accent/30'}`}
                  >
                    <div className="flex items-center gap-2">
                      <Badge variant="outline" className="text-[10px] flex-shrink-0">
                        {agentLabel(r.agent_name)}
                      </Badge>
                      <span className={`text-[13px] font-medium truncate ${active ? 'text-foreground' : 'text-foreground/90'}`}>{r.title || tr('report')}</span>
                    </div>
                    <div className="mt-1 flex items-center justify-between text-[11px] text-muted-foreground">
                      <span className="font-mono">{r.analysis_date}</span>
                      <span>{formatTimeShort(displayTime(r))}</span>
                    </div>
                  </button>
                )
              })}
            </div>
          </div>

          {/* Reader */}
          <div className={`md:col-span-7 card p-4 md:p-6 ${mobileView === 'list' ? 'hidden md:block' : ''}`}>
            {selectedRecord ? (
              <div>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="md:hidden h-8 px-2 -ml-2"
                        onClick={() => setMobileView('list')}
                      >
                        <ArrowLeft className="w-4 h-4" />
                        {tr('toc')}
                      </Button>
                      <Badge variant="outline" className="text-[10px]">{agentLabel(selectedRecord.agent_name)}</Badge>
                      <span className="text-[11px] text-muted-foreground font-mono">{formatDateTime(displayTime(selectedRecord))}</span>
                    </div>
                    <div className="mt-1 text-[15px] md:text-[16px] font-semibold text-foreground truncate">
                      {formatTitle(selectedRecord)}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        // TradingAgents 深度记录 → 独立详细阅读页;其余 agent 维持原详情弹窗
                        if (selectedRecord.agent_name === 'tradingagents' && selectedRecord.stock_symbol) {
                          navigate(`/analysis/${selectedRecord.stock_symbol}/${selectedRecord.analysis_date}`)
                        } else {
                          setDetailRecord(selectedRecord)
                        }
                      }}
                    >
                      {tr('viewDetails')}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-9 w-9 hover:text-destructive"
                      onClick={() => deleteRecord(selectedRecord.id)}
                      title={tr('delete')}
                    >
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  </div>
                </div>

                <div className="mt-4 p-4 bg-accent/20 rounded-xl prose prose-sm dark:prose-invert max-w-none max-h-[62vh] md:max-h-[62vh] overflow-y-auto scrollbar">
                  <ReactMarkdown>{selectedRecord.content}</ReactMarkdown>
                </div>
              </div>
            ) : (
              <div className="text-[13px] text-muted-foreground">{tr('selectRecord')}</div>
            )}
          </div>
        </div>
      )}

      {/* Detail Dialog */}
      <Dialog open={!!detailRecord} onOpenChange={open => !open && setDetailRecord(null)}>
        <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{detailRecord ? formatTitle(detailRecord) : tr('detail')}</DialogTitle>
            <DialogDescription>
              {detailRecord && (
                <span className="flex items-center gap-2">
                  <Badge variant="outline">{agentLabel(detailRecord.agent_name)}</Badge>
                </span>
              )}
            </DialogDescription>
          </DialogHeader>
          <div className="mt-4 p-4 bg-accent/20 rounded-lg prose prose-sm dark:prose-invert max-w-none">
            {detailRecord && <ReactMarkdown>{detailRecord.content}</ReactMarkdown>}
          </div>
          {detailRecord?.prompt_stats ? (
            <div className="mt-3 rounded-lg border border-border/50 p-3">
              <div className="text-[12px] font-medium mb-1">{tr('promptStats')}</div>
              <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words overflow-x-auto scrollbar">{JSON.stringify(detailRecord.prompt_stats, null, 2)}</pre>
            </div>
          ) : null}
          {detailRecord?.context_payload ? (
            <div className="mt-3 rounded-lg border border-border/50 p-3">
              <div className="text-[12px] font-medium mb-1">{tr('contextSnapshot')}</div>
              <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words overflow-x-auto max-h-[280px] overflow-y-auto scrollbar">{JSON.stringify(detailRecord.context_payload, null, 2)}</pre>
            </div>
          ) : null}
          {detailRecord?.news_debug ? (
            <div className="mt-3 rounded-lg border border-border/50 p-3">
              <div className="text-[12px] font-medium mb-1">{tr('newsDetails')}</div>
              <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words overflow-x-auto scrollbar">{JSON.stringify(detailRecord.news_debug, null, 2)}</pre>
            </div>
          ) : null}
          {detailRecord?.prompt_context ? (
            <div className="mt-3 rounded-lg border border-border/50 p-3">
              <div className="text-[12px] font-medium mb-1">{tr('promptOriginal')}</div>
              <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words overflow-x-auto max-h-[280px] overflow-y-auto scrollbar">{detailRecord.prompt_context}</pre>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  )
}
