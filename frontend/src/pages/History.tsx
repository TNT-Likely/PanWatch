import { useState, useEffect } from 'react'
import { Clock, Trash2, FileText, ArrowLeft } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import { fetchAPI } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Badge } from '@panwatch/base-ui/components/ui/badge'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@panwatch/base-ui/components/ui/select'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

interface HistoryRecord {
  id: number
  agent_name: string
  stock_symbol: string
  analysis_date: string
  title: string
  content: string
  created_at: string
  updated_at: string
}

const AGENT_LABELS: Record<string, string> = {
  daily_report: '盘后日报',
  premarket_outlook: '盘前分析',
  intraday_monitor: '盘中监测',
  news_digest: '新闻速递',
  chart_analyst: '技术分析',
}

export default function HistoryPage() {
  const { toast } = useToast()
  const [records, setRecords] = useState<HistoryRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedAgent, setSelectedAgent] = useState<string>('all')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [mobileView, setMobileView] = useState<'list' | 'reader'>('list')
  const [detailRecord, setDetailRecord] = useState<HistoryRecord | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (selectedAgent && selectedAgent !== 'all') params.set('agent_name', selectedAgent)
      params.set('limit', '50')
      const data = await fetchAPI<HistoryRecord[]>(`/history?${params.toString()}`)
      setRecords(data || [])
    } catch (e) {
      toast(e instanceof Error ? e.message : '加载失败', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [selectedAgent])

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
    if (!confirm('确定删除这条记录吗？')) return
    try {
      await fetchAPI(`/history/${id}`, { method: 'DELETE' })
      toast('已删除', 'success')
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : '删除失败', 'error')
    }
  }

  // 格式化标题（带日期）
  const formatTitle = (record: HistoryRecord) => {
    const agentLabel = AGENT_LABELS[record.agent_name] || record.agent_name
    if (record.title) {
      return `${record.analysis_date} ${record.title}`
    }
    return `${record.analysis_date} ${agentLabel}`
  }

  const selectedRecord = selectedId ? records.find(r => r.id === selectedId) || null : null

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
            <h1 className="text-lg md:text-xl font-bold">分析历史</h1>
            <p className="text-[12px] md:text-[13px] text-muted-foreground">报告式阅读：目录 + 正文</p>
          </div>
          <div className="hidden md:flex px-2.5 py-1 rounded-full bg-background/70 border border-border/50 text-[11px] text-muted-foreground">
            共 <span className="font-mono text-foreground/90">{records.length}</span> 条
          </div>
        </div>

        <Select value={selectedAgent} onValueChange={setSelectedAgent}>
          <SelectTrigger className="w-full sm:w-[180px] h-9">
            <SelectValue placeholder="全部 Agent" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部 Agent</SelectItem>
            {Object.entries(AGENT_LABELS).map(([key, label]) => (
              <SelectItem key={key} value={key}>{label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {loading ? (
        <div className="card p-12 text-center">
          <div className="w-6 h-6 border-2 border-primary/30 border-t-primary rounded-full animate-spin mx-auto" />
        </div>
      ) : records.length === 0 ? (
        <div className="card p-12 text-center">
          <FileText className="w-12 h-12 text-muted-foreground/30 mx-auto mb-3" />
          <p className="text-muted-foreground">暂无分析记录</p>
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
                目录
              </button>
              <button
                onClick={() => setMobileView('reader')}
                className={`h-9 rounded-lg text-[12px] font-medium transition-colors ${mobileView === 'reader' ? 'bg-primary text-white' : 'bg-accent/30 text-muted-foreground hover:bg-accent/50'}`}
                disabled={!selectedRecord}
              >
                正文
              </button>
            </div>
          </div>

          {/* List */}
          <div className={`md:col-span-5 card overflow-hidden ${mobileView === 'reader' ? 'hidden md:block' : ''}`}>
            <div className="px-4 py-3 bg-accent/20 border-b border-border/50 text-[12px] text-muted-foreground">
              目录（点击查看）
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
                        {AGENT_LABELS[r.agent_name] || r.agent_name}
                      </Badge>
                      <span className={`text-[13px] font-medium truncate ${active ? 'text-foreground' : 'text-foreground/90'}`}>{r.title || '分析报告'}</span>
                    </div>
                    <div className="mt-1 flex items-center justify-between text-[11px] text-muted-foreground">
                      <span className="font-mono">{r.analysis_date}</span>
                      <span>{new Date(r.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</span>
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
                        目录
                      </Button>
                      <Badge variant="outline" className="text-[10px]">{AGENT_LABELS[selectedRecord.agent_name] || selectedRecord.agent_name}</Badge>
                      <span className="text-[11px] text-muted-foreground font-mono">{selectedRecord.created_at}</span>
                    </div>
                    <div className="mt-1 text-[15px] md:text-[16px] font-semibold text-foreground truncate">
                      {formatTitle(selectedRecord)}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <Button variant="outline" size="sm" onClick={() => setDetailRecord(selectedRecord)}>查看详情</Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-9 w-9 hover:text-destructive"
                      onClick={() => deleteRecord(selectedRecord.id)}
                      title="删除"
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
              <div className="text-[13px] text-muted-foreground">请选择一条记录</div>
            )}
          </div>
        </div>
      )}

      {/* Detail Dialog */}
      <Dialog open={!!detailRecord} onOpenChange={open => !open && setDetailRecord(null)}>
        <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{detailRecord ? formatTitle(detailRecord) : '分析详情'}</DialogTitle>
            <DialogDescription>
              {detailRecord && (
                <span className="flex items-center gap-2">
                  <Badge variant="outline">{AGENT_LABELS[detailRecord.agent_name] || detailRecord.agent_name}</Badge>
                </span>
              )}
            </DialogDescription>
          </DialogHeader>
          <div className="mt-4 p-4 bg-accent/20 rounded-lg prose prose-sm dark:prose-invert max-w-none">
            {detailRecord && <ReactMarkdown>{detailRecord.content}</ReactMarkdown>}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
