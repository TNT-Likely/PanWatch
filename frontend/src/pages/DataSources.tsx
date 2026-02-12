import { useState, useEffect } from 'react'
import { Pencil, Play, Database, Newspaper, LineChart, TrendingUp, DollarSign, Image, Layers, Check, X, Clock } from 'lucide-react'
import { fetchAPI, type DataSource } from '@panwatch/api'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Switch } from '@panwatch/base-ui/components/ui/switch'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

interface TestLogItem {
  timestamp: string
  source_name: string
  source_type: string
  action: 'start' | 'success' | 'error'
  message: string
  duration_ms: number
  count: number
}

interface TestResult {
  success: boolean
  source_name: string
  source_type: string
  type_label: string
  provider: string
  supports_batch: boolean
  test_symbols: string[]
  count: number
  duration_ms: number
  error?: string
  data?: unknown[] | { image?: string }  // array for most types, object for chart
  logs: TestLogItem[]
}

interface DataSourceForm {
  name: string
  type: string
  provider: string
  config: Record<string, unknown>
  priority: number
  supports_batch: boolean
  test_symbols: string[]
}

const DATASOURCE_TYPES = {
  news: { label: '新闻资讯', icon: Newspaper, color: 'text-blue-500' },
  kline: { label: 'K线数据', icon: LineChart, color: 'text-orange-500' },
  capital_flow: { label: '资金流向', icon: DollarSign, color: 'text-yellow-500' },
  quote: { label: '实时行情', icon: TrendingUp, color: 'text-emerald-500' },
  events: { label: '事件日历', icon: Layers, color: 'text-violet-500' },
  chart: { label: 'K线截图', icon: Image, color: 'text-purple-500' },
}

const emptyForm: DataSourceForm = {
  name: '',
  type: '',
  provider: '',
  config: {},
  priority: 0,
  supports_batch: false,
  test_symbols: [],
}

export default function DataSourcesPage() {
  const [sources, setSources] = useState<DataSource[]>([])
  const [loading, setLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [form, setForm] = useState<DataSourceForm>(emptyForm)
  const [editId, setEditId] = useState<number | null>(null)
  const [testing, setTesting] = useState<number | null>(null)
  const [testResult, setTestResult] = useState<TestResult | null>(null)
  const [testResultOpen, setTestResultOpen] = useState(false)
  const [testSymbolsInput, setTestSymbolsInput] = useState('')

  const { toast } = useToast()

  const load = async () => {
    try {
      const data = await fetchAPI<DataSource[]>('/datasources')
      setSources(data)
    } catch (e) {
      console.error(e)
      toast('加载数据源失败', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const openDialog = (source?: DataSource) => {
    if (source) {
      setForm({
        name: source.name,
        type: source.type,
        provider: source.provider,
        config: source.config || {},
        priority: source.priority,
        supports_batch: source.supports_batch || false,
        test_symbols: source.test_symbols || [],
      })
      setTestSymbolsInput((source.test_symbols || []).join(', '))
      setEditId(source.id)
    } else {
      setForm(emptyForm)
      setTestSymbolsInput('')
      setEditId(null)
    }
    setDialogOpen(true)
  }

  const saveSource = async () => {
    if (!editId) return
    try {
      // Parse test symbols from comma-separated string
      const testSymbols = testSymbolsInput
        .split(/[,，\s]+/)
        .map(s => s.trim())
        .filter(s => s.length > 0)

      const payload = {
        priority: form.priority,
        test_symbols: testSymbols,
      }
      await fetchAPI(`/datasources/${editId}`, { method: 'PUT', body: JSON.stringify(payload) })
      setDialogOpen(false)
      load()
      toast('设置已保存', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '保存失败', 'error')
    }
  }

  const toggleEnabled = async (source: DataSource) => {
    try {
      await fetchAPI(`/datasources/${source.id}`, {
        method: 'PUT',
        body: JSON.stringify({ enabled: !source.enabled }),
      })
      load()
    } catch {
      toast('操作失败', 'error')
    }
  }

  const testSource = async (id: number) => {
    setTesting(id)
    try {
      const result = await fetchAPI<TestResult>(`/datasources/${id}/test`, { method: 'POST' })
      setTestResult(result)
      setTestResultOpen(true)
    } catch (e) {
      toast(e instanceof Error ? e.message : '测试失败', 'error')
    } finally {
      setTesting(null)
    }
  }

  // Group sources by type
  const groupedSources = sources.reduce((acc, source) => {
    const type = source.type
    if (!acc[type]) acc[type] = []
    acc[type].push(source)
    return acc
  }, {} as Record<string, DataSource[]>)

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
        <h1 className="text-[20px] md:text-[22px] font-bold text-foreground tracking-tight">数据源</h1>
        <p className="text-[12px] md:text-[13px] text-muted-foreground mt-0.5 md:mt-1">管理新闻、K线、资金流向和行情数据来源</p>
      </div>

      <div className="space-y-6">
        {Object.entries(DATASOURCE_TYPES).map(([type, { label, icon: Icon, color }]) => (
          <section key={type} className="card p-4 md:p-6">
            <div className="flex items-center gap-2 mb-4">
              <Icon className={`w-4 h-4 ${color}`} />
              <h3 className="text-[13px] font-semibold text-foreground">{label}</h3>
              <span className="text-[11px] text-muted-foreground ml-auto">
                {groupedSources[type]?.length || 0} 个数据源
              </span>
            </div>

            {(!groupedSources[type] || groupedSources[type].length === 0) ? (
              <p className="text-[13px] text-muted-foreground text-center py-6">暂无{label}数据源</p>
            ) : (
              <div className="space-y-2">
                {groupedSources[type].map(source => (
                    <div
                      key={source.id}
                      className="flex items-center justify-between p-3.5 rounded-xl bg-accent/30 hover:bg-accent/50 transition-colors"
                    >
                      <div className="flex items-center gap-3 min-w-0 flex-1">
                        <Database className="w-4 h-4 text-muted-foreground flex-shrink-0" />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="text-[13px] font-medium text-foreground">{source.name}</span>
                            {source.supports_batch && (
                              <span className="flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                                <Layers className="w-2.5 h-2.5" />
                                批量
                              </span>
                            )}
                          </div>
                          <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                            <span className="text-[11px] text-muted-foreground font-mono">{source.provider}</span>
                            <span className="text-[11px] text-muted-foreground">优先级: {source.priority}</span>
                            {source.test_symbols?.length > 0 && (
                              <span className="text-[10px] text-muted-foreground">
                                测试: {source.test_symbols.slice(0, 3).join(', ')}{source.test_symbols.length > 3 ? '...' : ''}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-1 flex-shrink-0">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => testSource(source.id)}
                          disabled={testing === source.id || !source.enabled}
                          title="测试连接"
                        >
                          {testing === source.id ? (
                            <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                          ) : (
                            <Play className="w-3.5 h-3.5" />
                          )}
                        </Button>
                        <Switch checked={source.enabled} onCheckedChange={() => toggleEnabled(source)} />
                        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openDialog(source)} title="设置">
                          <Pencil className="w-3.5 h-3.5" />
                        </Button>
                      </div>
                    </div>
                ))}
              </div>
            )}
          </section>
        ))}
      </div>

      {/* Edit Dialog - 只允许修改配置项 */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>数据源设置 - {form.name}</DialogTitle>
            <DialogDescription>{form.provider}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label>优先级 <span className="text-muted-foreground font-normal">(越小越高)</span></Label>
                <Input
                  type="number"
                  value={form.priority}
                  onChange={e => setForm({ ...form, priority: parseInt(e.target.value) || 0 })}
                  min={0}
                />
              </div>
            </div>
            <div>
              <Label>测试股票代码 <span className="text-muted-foreground font-normal">(逗号分隔)</span></Label>
              <Input
                value={testSymbolsInput}
                onChange={e => setTestSymbolsInput(e.target.value)}
                placeholder="如 601127, 600519"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setDialogOpen(false)}>取消</Button>
              <Button onClick={saveSource}>保存</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Test Result Dialog */}
      <Dialog open={testResultOpen} onOpenChange={setTestResultOpen}>
        <DialogContent
          className="max-w-2xl w-[92vw] max-h-[85vh] overflow-y-auto scrollbar"
          onInteractOutside={(e) => e.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {testResult?.success ? (
                <Check className="w-5 h-5 text-emerald-500" />
              ) : (
                <X className="w-5 h-5 text-red-500" />
              )}
              测试结果 - {testResult?.source_name}
            </DialogTitle>
            <DialogDescription>
              {testResult?.type_label} · {testResult?.provider}
              {testResult?.supports_batch && ' · 支持批量'}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 mt-2 pr-1">
            {/* Summary */}
            <div className="flex items-center gap-4 p-3 rounded-lg bg-accent/30">
              <div className="flex-1">
                <div className="text-[11px] text-muted-foreground">状态</div>
                <div className={`text-[13px] font-medium ${testResult?.success ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500'}`}>
                  {testResult?.success ? '测试成功' : '测试失败'}
                </div>
              </div>
              <div className="flex-1">
                <div className="text-[11px] text-muted-foreground">数据量</div>
                <div className="text-[13px] font-medium">{testResult?.count ?? 0} 条</div>
              </div>
              <div className="flex-1">
                <div className="text-[11px] text-muted-foreground">耗时</div>
                <div className="text-[13px] font-medium">{testResult?.duration_ms ?? 0} ms</div>
              </div>
            </div>

            {/* Error message */}
            {testResult?.error && (
              <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20">
                <div className="text-[11px] text-red-500 font-medium mb-1">错误信息</div>
                <div className="text-[12px] text-red-600 dark:text-red-400">{testResult.error}</div>
              </div>
            )}

            {/* Execution Logs */}
            {testResult?.logs && testResult.logs.length > 0 && (
              <div>
                <div className="text-[12px] font-medium text-foreground mb-2 flex items-center gap-1.5">
                  <Clock className="w-3.5 h-3.5" />
                  执行日志
                </div>
                <div className="space-y-1.5 max-h-40 overflow-y-auto">
                  {testResult.logs.map((log, i) => (
                    <div key={i} className="flex items-start gap-2 p-2 rounded-lg bg-accent/30 text-[11px]">
                      <span className="text-muted-foreground font-mono flex-shrink-0">{log.timestamp}</span>
                      <span className={`px-1 py-0.5 rounded text-[10px] flex-shrink-0 ${
                        log.action === 'start' ? 'bg-blue-500/10 text-blue-500' :
                        log.action === 'success' ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' :
                        'bg-red-500/10 text-red-500'
                      }`}>
                        {log.action === 'start' ? '开始' : log.action === 'success' ? '成功' : '失败'}
                      </span>
                      <span className="text-foreground flex-1">{log.message}</span>
                      {log.duration_ms > 0 && (
                        <span className="text-muted-foreground flex-shrink-0">{log.duration_ms}ms</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Data Preview */}
            {/* Chart type - show image outside scrollable area */}
            {testResult?.success && testResult.source_type === 'chart' && (testResult.data as {image?: string})?.image && (
              <div>
                <div className="text-[12px] font-medium text-foreground mb-2">数据预览</div>
                <div className="rounded-lg overflow-hidden border">
                  <img src={(testResult.data as {image: string}).image} alt="K线图截图" className="w-full" />
                </div>
              </div>
            )}

            {/* Other data types - in scrollable container */}
            {testResult?.success && testResult.data && testResult.source_type !== 'chart' && Array.isArray(testResult.data) && testResult.data.length > 0 && (
              <div>
                <div className="text-[12px] font-medium text-foreground mb-2">数据预览</div>
                <div className="space-y-1.5 max-h-60 overflow-y-auto">

                  {/* News type */}
                  {testResult.source_type === 'news' && testResult.data.map((item, i) => {
                    const newsItem = item as { title?: string; time?: string }
                    return (
                      <div key={i} className="flex items-start gap-2 p-2 rounded-lg bg-accent/30">
                        <span className="text-[12px] text-foreground flex-1">{newsItem.title}</span>
                        <span className="text-[11px] text-muted-foreground flex-shrink-0">{newsItem.time}</span>
                      </div>
                    )
                  })}

                  {/* Events type */}
                  {testResult.source_type === 'events' && testResult.data.map((item, i) => {
                    const ev = item as { title?: string; time?: string; event_type?: string }
                    return (
                      <div key={i} className="flex items-start gap-2 p-2 rounded-lg bg-accent/30">
                        <span className="text-[11px] font-mono text-muted-foreground/80 flex-shrink-0">{ev.event_type || 'notice'}</span>
                        <span className="text-[12px] text-foreground flex-1">{ev.title}</span>
                        <span className="text-[11px] text-muted-foreground flex-shrink-0">{ev.time}</span>
                      </div>
                    )
                  })}

                  {/* Quote type */}
                  {testResult.source_type === 'quote' && testResult.data.map((item, i) => {
                    const quoteItem = item as { symbol?: string; name?: string; price?: number; change_pct?: number }
                    return (
                      <div key={i} className="flex items-center justify-between p-2 rounded-lg bg-accent/30">
                        <span className="text-[12px] font-medium text-foreground">{quoteItem.name || quoteItem.symbol}</span>
                        <div className="flex items-center gap-3">
                          <span className="text-[12px] font-mono">{quoteItem.price?.toFixed(2)}</span>
                          <span className={`text-[11px] font-medium ${
                            (quoteItem.change_pct ?? 0) > 0 ? 'text-red-500' : (quoteItem.change_pct ?? 0) < 0 ? 'text-green-500' : 'text-muted-foreground'
                          }`}>
                            {(quoteItem.change_pct ?? 0) > 0 ? '+' : ''}{quoteItem.change_pct?.toFixed(2)}%
                          </span>
                        </div>
                      </div>
                    )
                  })}

                  {/* Kline type */}
                  {testResult.source_type === 'kline' && testResult.data.map((item, i) => {
                    const klineItem = item as { symbol?: string; last_close?: number; trend?: string }
                    return (
                      <div key={i} className="flex items-center justify-between p-2 rounded-lg bg-accent/30">
                        <span className="text-[12px] font-medium text-foreground">{klineItem.symbol}</span>
                        <div className="flex items-center gap-3">
                          <span className="text-[12px] font-mono">{klineItem.last_close?.toFixed(2)}</span>
                          <span className="text-[11px] text-muted-foreground">{klineItem.trend}</span>
                        </div>
                      </div>
                    )
                  })}

                  {/* Capital flow type */}
                  {testResult.source_type === 'capital_flow' && testResult.data.map((item, i) => {
                    const flowItem = item as { symbol?: string; name?: string; main_net?: number; main_pct?: number }
                    return (
                      <div key={i} className="flex items-center justify-between p-2 rounded-lg bg-accent/30">
                        <span className="text-[12px] font-medium text-foreground">{flowItem.name || flowItem.symbol}</span>
                        <div className="flex items-center gap-3">
                          <span className={`text-[12px] font-mono ${
                            (flowItem.main_net ?? 0) > 0 ? 'text-red-500' : 'text-green-500'
                          }`}>
                            {(flowItem.main_net ?? 0) > 0 ? '+' : ''}{((flowItem.main_net ?? 0) / 10000).toFixed(2)}万
                          </span>
                          <span className="text-[11px] text-muted-foreground">
                            {flowItem.main_pct?.toFixed(2)}%
                          </span>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            {/* Test symbols info */}
            {testResult?.test_symbols && testResult.test_symbols.length > 0 && (
              <div className="text-[11px] text-muted-foreground">
                测试股票: {testResult.test_symbols.join(', ')}
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
