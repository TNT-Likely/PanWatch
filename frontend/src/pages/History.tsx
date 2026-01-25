import { useState, useEffect } from 'react'
import { Clock, Trash2, ChevronDown, ChevronRight, FileText } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import { fetchAPI } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import { useToast } from '@/components/ui/toast'

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
  const [expandedId, setExpandedId] = useState<number | null>(null)
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

  // 按日期分组
  const groupedByDate = records.reduce((acc, r) => {
    const date = r.analysis_date
    if (!acc[date]) acc[date] = []
    acc[date].push(r)
    return acc
  }, {} as Record<string, HistoryRecord[]>)

  const sortedDates = Object.keys(groupedByDate).sort((a, b) => b.localeCompare(a))

  return (
    <div className="max-w-4xl mx-auto space-y-4 md:space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center gap-2 md:gap-3">
          <div className="w-9 h-9 md:w-10 md:h-10 rounded-lg md:rounded-xl bg-amber-500 flex items-center justify-center">
            <Clock className="w-4 h-4 md:w-5 md:h-5 text-white" />
          </div>
          <div>
            <h1 className="text-lg md:text-xl font-bold">分析历史</h1>
            <p className="text-[12px] md:text-[13px] text-muted-foreground">查看 AI 分析记录</p>
          </div>
        </div>

        <Select value={selectedAgent} onValueChange={setSelectedAgent}>
          <SelectTrigger className="w-full sm:w-[160px] h-9">
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

      {/* Records */}
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
        <div className="space-y-4">
          {sortedDates.map(date => (
            <div key={date} className="card overflow-hidden">
              <div className="px-4 py-2.5 bg-accent/30 border-b border-border/50">
                <span className="text-[13px] font-medium">{date}</span>
                <span className="text-[12px] text-muted-foreground ml-2">
                  {groupedByDate[date].length} 条记录
                </span>
              </div>
              <div className="divide-y divide-border/50">
                {groupedByDate[date].map(record => {
                  const isExpanded = expandedId === record.id
                  return (
                    <div key={record.id} className="group">
                      <div
                        className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-accent/30 transition-colors"
                        onClick={() => setExpandedId(isExpanded ? null : record.id)}
                      >
                        {isExpanded ? (
                          <ChevronDown className="w-4 h-4 text-muted-foreground flex-shrink-0" />
                        ) : (
                          <ChevronRight className="w-4 h-4 text-muted-foreground flex-shrink-0" />
                        )}
                        <Badge variant="outline" className="text-[10px] flex-shrink-0">
                          {AGENT_LABELS[record.agent_name] || record.agent_name}
                        </Badge>
                        <span className="text-[13px] truncate flex-1">
                          {record.title || '分析报告'}
                        </span>
                        <span className="text-[11px] text-muted-foreground flex-shrink-0">
                          {new Date(record.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
                        </span>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="opacity-0 group-hover:opacity-100 transition-opacity h-7 w-7 p-0"
                          onClick={e => { e.stopPropagation(); deleteRecord(record.id) }}
                        >
                          <Trash2 className="w-3.5 h-3.5 text-muted-foreground" />
                        </Button>
                      </div>
                      {isExpanded && (
                        <div className="px-4 pb-4 pl-11">
                          <div className="p-4 bg-accent/20 rounded-lg prose prose-sm dark:prose-invert max-w-none max-h-[400px] overflow-y-auto">
                            <ReactMarkdown>{record.content}</ReactMarkdown>
                          </div>
                          <div className="mt-2 flex justify-end">
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => setDetailRecord(record)}
                            >
                              查看详情
                            </Button>
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
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
