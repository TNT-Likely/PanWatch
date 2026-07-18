import { useState, useEffect } from 'react'
import { Pencil, Play, Database, Newspaper, LineChart, TrendingUp, DollarSign, Image, Layers, Zap, Check, X, Clock, Trash2, ChevronUp, ChevronDown, ChevronRight, Eye, EyeOff, RotateCcw, AlertTriangle, BarChart3, Trophy, Landmark, Users, Gift, ArrowLeftRight } from 'lucide-react'
import { fetchAPI, resetDataSourcesToSeed, type DataSource } from '@panwatch/api'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Switch } from '@panwatch/base-ui/components/ui/switch'
import { Badge } from '@panwatch/base-ui/components/ui/badge'
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
  test_passed: boolean
  source_name: string
  source_type: string
  type_label: string
  provider: string
  supports_batch: boolean
  test_symbols: string[]
  count: number
  duration_ms: number
  error?: string
  items?: unknown[] | { image?: string }  // array for most types, object for chart
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
  flash_news: { label: '快讯', icon: Zap, color: 'text-amber-500' },
  fundamentals: { label: '基本面', icon: BarChart3, color: 'text-indigo-500' },
  dragon_tiger: { label: '龙虎榜', icon: Trophy, color: 'text-red-500' },
  margin: { label: '融资融券', icon: Landmark, color: 'text-cyan-500' },
  shareholders: { label: '股东户数', icon: Users, color: 'text-teal-500' },
  dividend: { label: '分红', icon: Gift, color: 'text-pink-500' },
  northbound: { label: '北向资金', icon: ArrowLeftRight, color: 'text-sky-500' },
}

// 数据源分类分组:仅用于页面展示时的二级归组,不影响数据结构与后端
const DATASOURCE_CATEGORIES: { key: string; label: string; types: string[] }[] = [
  { key: 'quote_kline', label: '行情 & K线', types: ['quote', 'kline'] },
  { key: 'news', label: '资讯 & 快讯', types: ['news', 'flash_news', 'events'] },
  { key: 'fundamentals', label: '基本面 & 财务', types: ['fundamentals'] },
  { key: 'capital', label: '资金 & 市场面', types: ['capital_flow', 'dragon_tiger', 'margin', 'shareholders', 'northbound', 'dividend'] },
  { key: 'chart', label: '图表', types: ['chart'] },
]

// 兜底:未被以上分类覆盖的 type 归入"其他"(防止将来新增 type 时漏显示)
const CATEGORIZED_TYPES = new Set(DATASOURCE_CATEGORIES.flatMap(c => c.types))
const UNCATEGORIZED_TYPES = Object.keys(DATASOURCE_TYPES).filter(t => !CATEGORIZED_TYPES.has(t))
const ALL_DATASOURCE_CATEGORIES = UNCATEGORIZED_TYPES.length > 0
  ? [...DATASOURCE_CATEGORIES, { key: 'other', label: '其他', types: UNCATEGORIZED_TYPES }]
  : DATASOURCE_CATEGORIES

interface CredentialFieldDef { key: string; label: string; placeholder: string; secret?: boolean; help?: string }

// provider → 凭证字段(前端持有 UI 元数据,新增带凭证的 provider 时在此加一行)
const PROVIDER_CREDENTIAL_FIELDS: Record<string, CredentialFieldDef[]> = {
  tushare: [
    { key: 'token', label: 'Tushare Token', placeholder: '粘贴 token,留空则读环境变量 TUSHARE_TOKEN', secret: true, help: '登录 tushare.pro 个人主页获取' },
  ],
  xueqiu: [
    { key: 'cookies', label: '雪球 Cookies', placeholder: 'xq_a_token=...; xq_r_token=...', secret: true, help: '浏览器 DevTools → Network → 复制完整 cookie 字符串' },
  ],
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
  const [secretVisible, setSecretVisible] = useState(false)
  const [resetting, setResetting] = useState(false)
  // 分类折叠态:key 不存在或为 false 视为展开(默认全部展开)
  const [collapsedCategories, setCollapsedCategories] = useState<Record<string, boolean>>({})
  const toggleCategory = (key: string) => setCollapsedCategories(prev => ({ ...prev, [key]: !prev[key] }))

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

  const openDialog = (source?: DataSource, presetType?: string) => {
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
      setForm({ ...emptyForm, type: presetType || '' })
      setTestSymbolsInput('')
      setEditId(null)
    }
    setSecretVisible(false)
    setDialogOpen(true)
  }

  const saveSource = async () => {
    const testSymbols = testSymbolsInput.split(/[,，\s]+/).map(s => s.trim()).filter(Boolean)
    try {
      if (editId) {
        await fetchAPI(`/datasources/${editId}`, { method: 'PUT',
          body: JSON.stringify({ priority: form.priority, test_symbols: testSymbols, config: form.config || {} }) })
      } else {
        if (!form.name || !form.type || !form.provider) { toast('名称/类型/Provider 必填', 'error'); return }
        await fetchAPI('/datasources', { method: 'POST', body: JSON.stringify({
          name: form.name, type: form.type, provider: form.provider,
          config: form.config || {}, priority: form.priority,
          supports_batch: form.supports_batch, test_symbols: testSymbols, enabled: true }) })
      }
      setDialogOpen(false); load(); toast(editId ? '设置已保存' : '已新增数据源', 'success')
    } catch (e) { toast(e instanceof Error ? e.message : '保存失败', 'error') }
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

  // 组内按当前顺序(API 已按 type,priority,id 排序)与相邻源交换优先级
  const moveSource = async (source: DataSource, dir: -1 | 1) => {
    const group = groupedSources[source.type] || []
    const idx = group.findIndex(s => s.id === source.id)
    const swap = group[idx + dir]
    if (!swap) return
    try {
      await Promise.all([
        fetchAPI(`/datasources/${source.id}`, { method: 'PUT', body: JSON.stringify({ priority: swap.priority }) }),
        fetchAPI(`/datasources/${swap.id}`, { method: 'PUT', body: JSON.stringify({ priority: source.priority }) }),
      ])
      load()
    } catch { toast('调整顺序失败', 'error') }
  }

  const resetToSeed = async () => {
    if (!window.confirm('将删除无对应数据源的孤儿行、补齐缺失的默认源,并保留你的自定义配置与凭证。是否继续?')) return
    setResetting(true)
    try {
      const result = await resetDataSourcesToSeed()
      load()
      toast(`已清理 ${result.deleted.length} 个孤儿源,补齐 ${result.seeded_missing.length} 个默认源`, 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '恢复默认失败', 'error')
    } finally {
      setResetting(false)
    }
  }

  const deleteSource = async () => {
    if (!editId) return
    if (!window.confirm(`确定删除数据源「${form.name}」?`)) return
    try {
      await fetchAPI(`/datasources/${editId}`, { method: 'DELETE' })
      setDialogOpen(false); load(); toast('已删除', 'success')
    } catch (e) { toast(e instanceof Error ? e.message : '删除失败', 'error') }
  }

  // 单个 type 的 section 渲染(结构与此前平铺版本完全一致,仅抽成函数以便按分类复用)
  const renderTypeSection = (type: string) => {
    const meta = DATASOURCE_TYPES[type as keyof typeof DATASOURCE_TYPES]
    if (!meta) return null
    const { label, icon: Icon, color } = meta
    return (
      <section key={type} className="card p-4 md:p-6">
        <div className="flex items-center gap-2 mb-4">
          <Icon className={`w-4 h-4 ${color}`} />
          <h3 className="text-[13px] font-semibold text-foreground">{label}</h3>
          <span className="text-[11px] text-muted-foreground ml-auto">
            {groupedSources[type]?.length || 0} 个
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
                        {source.engine_attached ? (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">已接入新引擎</span>
                        ) : (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">旧路·待迁移</span>
                        )}
                        {source.is_orphan && (
                          <Badge variant="destructive" className="text-[10px] px-1.5 py-0.5">
                            <AlertTriangle className="w-2.5 h-2.5" />
                            无对应源·待清理
                          </Badge>
                        )}
                        {source.engine_attached && source.health && source.health.success_rate != null && (
                          <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                            <span className={`inline-block w-1.5 h-1.5 rounded-full ${
                              source.health.success_rate >= 0.95 ? 'bg-emerald-500'
                              : source.health.success_rate >= 0.8 ? 'bg-amber-500' : 'bg-red-500'}`} />
                            成功率 {Math.round(source.health.success_rate * 100)}%
                            {source.health.p50_latency_ms != null && ` · p50 ${source.health.p50_latency_ms}ms`}
                            {source.health.last_error ? ` · 最近错误` : ''}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => moveSource(source, -1)} title="上移(提高优先级)">
                      <ChevronUp className="w-3.5 h-3.5" />
                    </Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => moveSource(source, 1)} title="下移">
                      <ChevronDown className="w-3.5 h-3.5" />
                    </Button>
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
    )
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
      <div className="mb-4 md:mb-8 flex items-start justify-between gap-3">
        <div>
          <h1 className="text-[20px] md:text-[22px] font-bold text-foreground tracking-tight">数据源</h1>
          <p className="text-[12px] md:text-[13px] text-muted-foreground mt-0.5 md:mt-1">管理新闻、K线、资金流向和行情数据来源</p>
        </div>
        <Button variant="outline" size="sm" className="h-8 text-[12px] flex-shrink-0" onClick={resetToSeed} disabled={resetting}>
          {resetting ? (
            <span className="w-3.5 h-3.5 mr-1.5 border-2 border-current/30 border-t-current rounded-full animate-spin" />
          ) : (
            <RotateCcw className="w-3.5 h-3.5 mr-1.5" />
          )}
          恢复默认
        </Button>
      </div>

      <div className="space-y-6">
        {ALL_DATASOURCE_CATEGORIES.map(category => {
          const categoryCount = category.types.reduce((sum, t) => sum + (groupedSources[t]?.length || 0), 0)
          const isOpen = collapsedCategories[category.key] !== true
          return (
            <div key={category.key}>
              <button
                type="button"
                className="w-full flex items-center gap-2 mb-3 py-1 text-left group"
                onClick={() => toggleCategory(category.key)}
              >
                <ChevronRight className={`w-3.5 h-3.5 text-muted-foreground flex-shrink-0 transition-transform ${isOpen ? 'rotate-90' : ''}`} />
                <span className="text-[13px] font-semibold text-muted-foreground group-hover:text-foreground transition-colors">
                  {category.label}
                </span>
                <span className="text-[11px] text-muted-foreground/70">{categoryCount} 个源</span>
                <div className="flex-1 h-px bg-border ml-2" />
              </button>
              {isOpen && (
                <div className="space-y-6 mb-6">
                  {category.types.map(type => renderTypeSection(type))}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Edit Dialog - 编辑模式只允许修改配置项;新增模式含名称/类型/Provider */}
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

            {/* 凭证类配置:按 provider 动态渲染对应字段 */}
            {(PROVIDER_CREDENTIAL_FIELDS[form.provider] || []).map(field => (
              <div key={field.key}>
                <Label>{field.label}
                  {field.help && <span className="text-muted-foreground font-normal ml-1">({field.help})</span>}
                </Label>
                <div className="relative">
                  <Input
                    type={field.secret && !secretVisible ? 'password' : 'text'}
                    value={(form.config?.[field.key] as string) || ''}
                    onChange={e => setForm({ ...form, config: { ...form.config, [field.key]: e.target.value } })}
                    placeholder={field.placeholder}
                    className={field.secret ? 'pr-10 font-mono' : 'font-mono'}
                  />
                  {field.secret && (
                    <Button type="button" variant="ghost" size="icon"
                      className="absolute right-1 top-1/2 -translate-y-1/2 h-8 w-8"
                      onClick={() => setSecretVisible(!secretVisible)}>
                      {secretVisible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </Button>
                  )}
                </div>
              </div>
            ))}

            {/* 高级:完整 JSON 编辑(只读形式,展开后可编辑) */}
            {Object.keys(form.config || {}).length > 0 && (
              <details className="text-[12px]">
                <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                  高级:查看/编辑完整 config JSON
                </summary>
                <textarea
                  className="mt-2 w-full font-mono text-[11px] p-2 border border-border rounded bg-background min-h-[100px]"
                  value={JSON.stringify(form.config || {}, null, 2)}
                  onChange={e => {
                    try {
                      const parsed = JSON.parse(e.target.value)
                      setForm({ ...form, config: parsed })
                    } catch {
                      // 解析失败时不更新,允许用户继续输入
                    }
                  }}
                />
              </details>
            )}

            <div className="flex justify-between gap-2 pt-2">
              {editId ? (
                <Button variant="ghost" className="text-red-500 hover:text-red-600" onClick={deleteSource}>
                  <Trash2 className="w-4 h-4 mr-1" />删除
                </Button>
              ) : <span />}
              <div className="flex gap-2">
                <Button variant="ghost" onClick={() => setDialogOpen(false)}>取消</Button>
                <Button onClick={saveSource}>{editId ? '保存' : '新增'}</Button>
              </div>
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
              {testResult?.test_passed ? (
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
                <div className={`text-[13px] font-medium ${testResult?.test_passed ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500'}`}>
                  {testResult?.test_passed ? '测试成功' : '测试失败'}
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
                <div className="text-[12px] text-red-600 dark:text-red-400 break-words whitespace-pre-wrap">{testResult.error}</div>
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
            {testResult?.test_passed && testResult.source_type === 'chart' && (testResult.items as {image?: string})?.image && (
              <div>
                <div className="text-[12px] font-medium text-foreground mb-2">数据预览</div>
                <div className="rounded-lg overflow-hidden border">
                  <img src={(testResult.items as {image: string}).image} alt="K线图截图" className="w-full" />
                </div>
              </div>
            )}

            {/* Other data types - in scrollable container */}
            {testResult?.test_passed && testResult.items && testResult.source_type !== 'chart' && Array.isArray(testResult.items) && testResult.items.length > 0 && (
              <div>
                <div className="text-[12px] font-medium text-foreground mb-2">数据预览</div>
                <div className="space-y-1.5 max-h-60 overflow-y-auto">

                  {/* News type */}
                  {testResult.source_type === 'news' && testResult.items.map((item, i) => {
                    const newsItem = item as { title?: string; time?: string }
                    return (
                      <div key={i} className="flex items-start gap-2 p-2 rounded-lg bg-accent/30">
                        <span className="text-[12px] text-foreground flex-1">{newsItem.title}</span>
                        <span className="text-[11px] text-muted-foreground flex-shrink-0">{newsItem.time}</span>
                      </div>
                    )
                  })}

                  {/* Events type */}
                  {testResult.source_type === 'events' && testResult.items.map((item, i) => {
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
                  {testResult.source_type === 'quote' && testResult.items.map((item, i) => {
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
                  {testResult.source_type === 'kline' && testResult.items.map((item, i) => {
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

                  {/* Flash news type */}
                  {testResult.source_type === 'flash_news' && testResult.items.map((item, i) => {
                    const flashItem = item as { title?: string; time?: string; symbols?: string[] }
                    return (
                      <div key={i} className="flex items-start gap-2 p-2 rounded-lg bg-accent/30">
                        <span className="text-[12px] text-foreground flex-1">
                          {flashItem.title}
                          {flashItem.symbols && flashItem.symbols.length > 0 && (
                            <span className="ml-2 text-[11px] text-muted-foreground">{flashItem.symbols.join(', ')}</span>
                          )}
                        </span>
                        <span className="text-[11px] text-muted-foreground flex-shrink-0">{flashItem.time}</span>
                      </div>
                    )
                  })}

                  {/* Fundamentals type */}
                  {testResult.source_type === 'fundamentals' && testResult.items.map((item, i) => {
                    const fundItem = item as { symbol?: string; name?: string; pe_ttm?: number; pb?: number; roe?: number }
                    return (
                      <div key={i} className="flex items-center justify-between p-2 rounded-lg bg-accent/30">
                        <span className="text-[12px] font-medium text-foreground">{fundItem.name || fundItem.symbol}</span>
                        <div className="flex items-center gap-3">
                          <span className="text-[11px] text-muted-foreground">PE {fundItem.pe_ttm?.toFixed(2) ?? '-'}</span>
                          <span className="text-[11px] text-muted-foreground">PB {fundItem.pb?.toFixed(2) ?? '-'}</span>
                          <span className="text-[11px] text-muted-foreground">ROE {fundItem.roe?.toFixed(2) ?? '-'}%</span>
                        </div>
                      </div>
                    )
                  })}

                  {/* Capital flow type */}
                  {testResult.source_type === 'capital_flow' && testResult.items.map((item, i) => {
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

                  {/* Dragon tiger type */}
                  {testResult.source_type === 'dragon_tiger' && testResult.items.map((item, i) => {
                    const dtItem = item as { symbol?: string; name?: string; net_buy?: number }
                    return (
                      <div key={i} className="flex items-center justify-between p-2 rounded-lg bg-accent/30">
                        <span className="text-[12px] font-medium text-foreground">{dtItem.name || dtItem.symbol}</span>
                        <span className={`text-[12px] font-mono ${
                          (dtItem.net_buy ?? 0) > 0 ? 'text-red-500' : 'text-green-500'
                        }`}>
                          {(dtItem.net_buy ?? 0) > 0 ? '+' : ''}{((dtItem.net_buy ?? 0) / 10000).toFixed(2)}万
                        </span>
                      </div>
                    )
                  })}

                  {/* Margin type */}
                  {testResult.source_type === 'margin' && testResult.items.map((item, i) => {
                    const marginItem = item as { symbol?: string; date?: string; total_balance?: number }
                    return (
                      <div key={i} className="flex items-center justify-between p-2 rounded-lg bg-accent/30">
                        <span className="text-[12px] font-medium text-foreground">{marginItem.symbol}</span>
                        <div className="flex items-center gap-3">
                          <span className="text-[12px] font-mono">{((marginItem.total_balance ?? 0) / 10000).toFixed(2)}万</span>
                          <span className="text-[11px] text-muted-foreground">{marginItem.date}</span>
                        </div>
                      </div>
                    )
                  })}

                  {/* Shareholders type */}
                  {testResult.source_type === 'shareholders' && testResult.items.map((item, i) => {
                    const shItem = item as { symbol?: string; report_date?: string; holder_num?: number }
                    return (
                      <div key={i} className="flex items-center justify-between p-2 rounded-lg bg-accent/30">
                        <span className="text-[12px] font-medium text-foreground">{shItem.symbol}</span>
                        <div className="flex items-center gap-3">
                          <span className="text-[12px] font-mono">{shItem.holder_num?.toLocaleString() ?? '-'}</span>
                          <span className="text-[11px] text-muted-foreground">{shItem.report_date}</span>
                        </div>
                      </div>
                    )
                  })}

                  {/* Dividend type */}
                  {testResult.source_type === 'dividend' && testResult.items.map((item, i) => {
                    const divItem = item as { symbol?: string; ex_date?: string; dividend_per_share?: number }
                    return (
                      <div key={i} className="flex items-center justify-between p-2 rounded-lg bg-accent/30">
                        <span className="text-[12px] font-medium text-foreground">{divItem.symbol}</span>
                        <div className="flex items-center gap-3">
                          <span className="text-[12px] font-mono">{divItem.dividend_per_share?.toFixed(4) ?? '-'} 元/股</span>
                          <span className="text-[11px] text-muted-foreground">{divItem.ex_date}</span>
                        </div>
                      </div>
                    )
                  })}

                  {/* Northbound type */}
                  {testResult.source_type === 'northbound' && testResult.items.map((item, i) => {
                    const nbItem = item as { date?: string; hgt_net?: number; total_net?: number }
                    return (
                      <div key={i} className="flex items-center justify-between p-2 rounded-lg bg-accent/30">
                        <span className="text-[12px] font-medium text-foreground">{nbItem.date}</span>
                        <div className="flex items-center gap-3">
                          <span className={`text-[12px] font-mono ${
                            (nbItem.total_net ?? 0) > 0 ? 'text-red-500' : 'text-green-500'
                          }`}>
                            {(nbItem.total_net ?? 0) > 0 ? '+' : ''}{((nbItem.total_net ?? 0) / 10000).toFixed(2)}万
                          </span>
                          <span className="text-[11px] text-muted-foreground">
                            沪股通 {((nbItem.hgt_net ?? 0) / 10000).toFixed(2)}万
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
