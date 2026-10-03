import { useConfirm } from '@panwatch/base-ui/components/ui/confirm-dialog'
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
import { useTranslation } from 'react-i18next'
import { formatNumber } from '@/i18n/format'
import { marketSignTextClass } from '@/lib/market-colors'

interface TestLogItem {
  timestamp: string
  source_name: string
  source_type: string
  action: 'start' | 'success' | 'error'
  message: string
  duration_ms: number
  count: number
}

export interface TestErrorItem {
  symbol: string
  market?: string
  error: string
}

export function TestErrorList({ errors }: { errors: TestErrorItem[] }) {
  const { t } = useTranslation('configuration')
  if (errors.length === 0) return null

  return (
    <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/20">
      <div className="text-[11px] text-amber-600 dark:text-amber-400 font-medium mb-1">{t('dataSources.result.missingDetails')}</div>
      <div className="space-y-1">
        {errors.map((item, i) => (
          <div key={`${item.symbol}-${i}`} className="text-[12px] text-amber-700 dark:text-amber-300">
            {item.symbol}{item.market ? ` (${item.market})` : ''}: {item.error}
          </div>
        ))}
      </div>
    </div>
  )
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
  errors?: TestErrorItem[]
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
  news: { icon: Newspaper, color: 'text-blue-500' },
  kline: { icon: LineChart, color: 'text-orange-500' },
  capital_flow: { icon: DollarSign, color: 'text-yellow-500' },
  quote: { icon: TrendingUp, color: 'text-emerald-500' },
  events: { icon: Layers, color: 'text-violet-500' },
  chart: { icon: Image, color: 'text-purple-500' },
  flash_news: { icon: Zap, color: 'text-amber-500' },
  fundamentals: { icon: BarChart3, color: 'text-indigo-500' },
  dragon_tiger: { icon: Trophy, color: 'text-red-500' },
  margin: { icon: Landmark, color: 'text-cyan-500' },
  shareholders: { icon: Users, color: 'text-teal-500' },
  dividend: { icon: Gift, color: 'text-pink-500' },
  northbound: { icon: ArrowLeftRight, color: 'text-sky-500' },
}

// 数据源分类分组:仅用于页面展示时的二级归组,不影响数据结构与后端
const DATASOURCE_CATEGORIES: { key: string; types: string[] }[] = [
  { key: 'quote_kline', types: ['quote', 'kline'] },
  { key: 'news', types: ['news', 'flash_news', 'events'] },
  { key: 'fundamentals', types: ['fundamentals'] },
  { key: 'capital', types: ['capital_flow', 'dragon_tiger', 'margin', 'shareholders', 'northbound', 'dividend'] },
  { key: 'chart', types: ['chart'] },
]

// 兜底:未被以上分类覆盖的 type 归入"其他"(防止将来新增 type 时漏显示)
const CATEGORIZED_TYPES = new Set(DATASOURCE_CATEGORIES.flatMap(c => c.types))
const UNCATEGORIZED_TYPES = Object.keys(DATASOURCE_TYPES).filter(t => !CATEGORIZED_TYPES.has(t))
const ALL_DATASOURCE_CATEGORIES = UNCATEGORIZED_TYPES.length > 0
  ? [...DATASOURCE_CATEGORIES, { key: 'other', types: UNCATEGORIZED_TYPES }]
  : DATASOURCE_CATEGORIES

interface CredentialFieldDef { key: string; labelKey: string; placeholderKey: string; secret?: boolean; helpKey?: string }

// provider → 凭证字段(前端持有 UI 元数据,新增带凭证的 provider 时在此加一行)
const PROVIDER_CREDENTIAL_FIELDS: Record<string, CredentialFieldDef[]> = {
  tushare: [
    { key: 'token', labelKey: 'dataSources.credentials.tushareLabel', placeholderKey: 'dataSources.credentials.tusharePlaceholder', secret: true, helpKey: 'dataSources.credentials.tushareHelp' },
  ],
  xueqiu: [
    { key: 'cookies', labelKey: 'dataSources.credentials.xueqiuLabel', placeholderKey: 'dataSources.credentials.xueqiuPlaceholder', secret: true, helpKey: 'dataSources.credentials.xueqiuHelp' },
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
  const { t } = useTranslation(['configuration', 'common'])
  const confirmAction = useConfirm()
  const configT = t as unknown as (key: string, options?: Record<string, unknown>) => string
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
      toast(t('configuration:dataSources.messages.loadFailed'), 'error')
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
        if (!form.name || !form.type || !form.provider) { toast(t('configuration:dataSources.messages.required'), 'error'); return }
        await fetchAPI('/datasources', { method: 'POST', body: JSON.stringify({
          name: form.name, type: form.type, provider: form.provider,
          config: form.config || {}, priority: form.priority,
          supports_batch: form.supports_batch, test_symbols: testSymbols, enabled: true }) })
      }
      setDialogOpen(false); load(); toast(editId ? t('configuration:dataSources.messages.saved') : t('configuration:dataSources.messages.created'), 'success')
    } catch (e) { toast(e instanceof Error ? e.message : t('configuration:dataSources.messages.saveFailed'), 'error') }
  }

  const toggleEnabled = async (source: DataSource) => {
    try {
      await fetchAPI(`/datasources/${source.id}`, {
        method: 'PUT',
        body: JSON.stringify({ enabled: !source.enabled }),
      })
      load()
    } catch {
      toast(t('configuration:dataSources.messages.operationFailed'), 'error')
    }
  }

  const testSource = async (id: number) => {
    setTesting(id)
    try {
      const result = await fetchAPI<TestResult>(`/datasources/${id}/test`, { method: 'POST' })
      setTestResult(result)
      setTestResultOpen(true)
    } catch (e) {
      toast(e instanceof Error ? e.message : t('configuration:dataSources.messages.testFailed'), 'error')
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
    } catch { toast(t('configuration:dataSources.messages.reorderFailed'), 'error') }
  }

  const resetToSeed = async () => {
    if (!(await confirmAction(t('configuration:dataSources.messages.resetConfirm'), { destructive: true }))) return
    setResetting(true)
    try {
      const result = await resetDataSourcesToSeed()
      load()
      toast(t('configuration:dataSources.messages.resetDone', { deleted: result.deleted.length, seeded: result.seeded_missing.length }), 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : t('configuration:dataSources.messages.resetFailed'), 'error')
    } finally {
      setResetting(false)
    }
  }

  const deleteSource = async () => {
    if (!editId) return
    if (!(await confirmAction(t('configuration:dataSources.messages.deleteConfirm', { name: form.name }), { destructive: true }))) return
    try {
      await fetchAPI(`/datasources/${editId}`, { method: 'DELETE' })
      setDialogOpen(false); load(); toast(t('configuration:dataSources.messages.deleted'), 'success')
    } catch (e) { toast(e instanceof Error ? e.message : t('configuration:dataSources.messages.deleteFailed'), 'error') }
  }

  // 单个 type 的 section 渲染(结构与此前平铺版本完全一致,仅抽成函数以便按分类复用)
  const renderTypeSection = (type: string) => {
    const meta = DATASOURCE_TYPES[type as keyof typeof DATASOURCE_TYPES]
    if (!meta) return null
    const { icon: Icon, color } = meta
    const label = configT(`dataSources.types.${type}`, { defaultValue: type })
    return (
      <section key={type} className="card p-4 md:p-6">
        <div className="flex items-center gap-2 mb-4">
          <Icon className={`w-4 h-4 ${color}`} />
          <h3 className="text-[13px] font-semibold text-foreground">{label}</h3>
          <span className="text-[11px] text-muted-foreground ml-auto">
            {t('configuration:dataSources.itemCount', { count: groupedSources[type]?.length || 0 })}
          </span>
        </div>

        {(!groupedSources[type] || groupedSources[type].length === 0) ? (
          <p className="text-[13px] text-muted-foreground text-center py-6">{t('configuration:dataSources.emptyType', { type: label })}</p>
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
                            {t('configuration:dataSources.batch')}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                        <span className="text-[11px] text-muted-foreground font-mono">{source.provider}</span>
                        <span className="text-[11px] text-muted-foreground">{t('configuration:dataSources.priority', { priority: source.priority })}</span>
                        {source.engine_attached ? (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">{t('configuration:dataSources.engineAttached')}</span>
                        ) : (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{t('configuration:dataSources.legacyEngine')}</span>
                        )}
                        {source.is_orphan && (
                          <Badge variant="destructive" className="text-[10px] px-1.5 py-0.5">
                            <AlertTriangle className="w-2.5 h-2.5" />
                            {t('configuration:dataSources.orphan')}
                          </Badge>
                        )}
                        {source.engine_attached && source.health && source.health.success_rate != null && (
                          <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                            <span className={`inline-block w-1.5 h-1.5 rounded-full ${
                              source.health.success_rate >= 0.95 ? 'bg-emerald-500'
                              : source.health.success_rate >= 0.8 ? 'bg-amber-500' : 'bg-red-500'}`} />
                            {t('configuration:dataSources.successRate', { rate: Math.round(source.health.success_rate * 100) })}
                            {source.health.p50_latency_ms != null && ` · p50 ${source.health.p50_latency_ms}ms`}
                            {source.health.last_error ? t('configuration:dataSources.recentError') : ''}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => moveSource(source, -1)} title={t('configuration:dataSources.moveUp')}>
                      <ChevronUp className="w-3.5 h-3.5" />
                    </Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => moveSource(source, 1)} title={t('configuration:dataSources.moveDown')}>
                      <ChevronDown className="w-3.5 h-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      onClick={() => testSource(source.id)}
                      disabled={testing === source.id || !source.enabled}
                      title={t('configuration:dataSources.testConnection')}
                    >
                      {testing === source.id ? (
                        <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                      ) : (
                        <Play className="w-3.5 h-3.5" />
                      )}
                    </Button>
                    <Switch checked={source.enabled} onCheckedChange={() => toggleEnabled(source)} />
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openDialog(source)} title={t('configuration:dataSources.settings')}>
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
          <h1 className="text-[20px] md:text-[22px] font-bold text-foreground tracking-tight">{t('configuration:dataSources.title')}</h1>
          <p className="text-[12px] md:text-[13px] text-muted-foreground mt-0.5 md:mt-1">{t('configuration:dataSources.subtitle')}</p>
        </div>
        <Button variant="outline" size="sm" className="h-8 text-[12px] flex-shrink-0" onClick={resetToSeed} disabled={resetting}>
          {resetting ? (
            <span className="w-3.5 h-3.5 mr-1.5 border-2 border-current/30 border-t-current rounded-full animate-spin" />
          ) : (
            <RotateCcw className="w-3.5 h-3.5 mr-1.5" />
          )}
          {t('configuration:dataSources.restoreDefaults')}
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
                  {configT(`dataSources.categories.${category.key}`, { defaultValue: category.key })}
                </span>
                <span className="text-[11px] text-muted-foreground/70">{t('configuration:dataSources.sourceCount', { count: categoryCount })}</span>
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
            <DialogTitle>{t('configuration:dataSources.form.title', { name: form.name })}</DialogTitle>
            <DialogDescription>{form.provider}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label>{t('configuration:dataSources.form.priority')} <span className="text-muted-foreground font-normal">{t('configuration:dataSources.form.priorityHint')}</span></Label>
                <Input
                  type="number"
                  value={form.priority}
                  onChange={e => setForm({ ...form, priority: parseInt(e.target.value) || 0 })}
                  min={0}
                />
              </div>
            </div>
            <div>
              <Label>{t('configuration:dataSources.form.testSymbols')} <span className="text-muted-foreground font-normal">{t('configuration:dataSources.form.commaSeparated')}</span></Label>
              <Input
                value={testSymbolsInput}
                onChange={e => setTestSymbolsInput(e.target.value)}
                placeholder={t('configuration:dataSources.form.symbolsPlaceholder')}
              />
            </div>

            {/* 凭证类配置:按 provider 动态渲染对应字段 */}
            {(PROVIDER_CREDENTIAL_FIELDS[form.provider] || []).map(field => (
              <div key={field.key}>
                <Label>{configT(field.labelKey)}
                  {field.helpKey && <span className="text-muted-foreground font-normal ml-1">({configT(field.helpKey)})</span>}
                </Label>
                <div className="relative">
                  <Input
                    type={field.secret && !secretVisible ? 'password' : 'text'}
                    value={(form.config?.[field.key] as string) || ''}
                    onChange={e => setForm({ ...form, config: { ...form.config, [field.key]: e.target.value } })}
                    placeholder={configT(field.placeholderKey)}
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
                  {t('configuration:dataSources.form.advanced')}
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
                  <Trash2 className="w-4 h-4 mr-1" />{t('common:actions.delete')}
                </Button>
              ) : <span />}
              <div className="flex gap-2">
                <Button variant="ghost" onClick={() => setDialogOpen(false)}>{t('common:actions.cancel')}</Button>
                <Button onClick={saveSource}>{editId ? t('common:actions.save') : t('common:actions.add')}</Button>
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
              {t('configuration:dataSources.result.title', { name: testResult?.source_name || '' })}
            </DialogTitle>
            <DialogDescription>
              {testResult?.type_label} · {testResult?.provider}
              {testResult?.supports_batch && t('configuration:dataSources.result.batchSupported')}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 mt-2 pr-1">
            {/* Summary */}
            <div className="flex items-center gap-4 p-3 rounded-lg bg-accent/30">
              <div className="flex-1">
                <div className="text-[11px] text-muted-foreground">{t('configuration:dataSources.result.status')}</div>
                <div className={`text-[13px] font-medium ${testResult?.test_passed ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500'}`}>
                  {testResult?.test_passed ? t('configuration:dataSources.result.success') : t('configuration:dataSources.result.failed')}
                </div>
              </div>
              <div className="flex-1">
                <div className="text-[11px] text-muted-foreground">{t('configuration:dataSources.result.count')}</div>
                <div className="text-[13px] font-medium">{t('configuration:dataSources.result.rows', { count: testResult?.count ?? 0 })}</div>
              </div>
              <div className="flex-1">
                <div className="text-[11px] text-muted-foreground">{t('configuration:dataSources.result.duration')}</div>
                <div className="text-[13px] font-medium">{testResult?.duration_ms ?? 0} ms</div>
              </div>
            </div>

            {/* Error message */}
            {testResult?.error && (
              <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20">
                <div className="text-[11px] text-red-500 font-medium mb-1">{t('configuration:dataSources.result.error')}</div>
                <div className="text-[12px] text-red-600 dark:text-red-400 break-words whitespace-pre-wrap">{testResult.error}</div>
              </div>
            )}

            {testResult?.errors && <TestErrorList errors={testResult.errors} />}

            {/* Execution Logs */}
            {testResult?.logs && testResult.logs.length > 0 && (
              <div>
                <div className="text-[12px] font-medium text-foreground mb-2 flex items-center gap-1.5">
                  <Clock className="w-3.5 h-3.5" />
                  {t('configuration:dataSources.result.logs')}
                </div>
                <div className="space-y-1.5 max-h-40 overflow-y-auto scrollbar">
                  {testResult.logs.map((log, i) => (
                    <div key={i} className="flex items-start gap-2 p-2 rounded-lg bg-accent/30 text-[11px]">
                      <span className="text-muted-foreground font-mono flex-shrink-0">{log.timestamp}</span>
                      <span className={`px-1 py-0.5 rounded text-[10px] flex-shrink-0 ${
                        log.action === 'start' ? 'bg-blue-500/10 text-blue-500' :
                        log.action === 'success' ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' :
                        'bg-red-500/10 text-red-500'
                      }`}>
                        {t(`configuration:dataSources.result.actions.${log.action}`)}
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
                <div className="text-[12px] font-medium text-foreground mb-2">{t('configuration:dataSources.result.preview')}</div>
                <div className="rounded-lg overflow-hidden border">
                  <img src={(testResult.items as {image: string}).image} alt={t('configuration:dataSources.result.chartAlt')} className="w-full" />
                </div>
              </div>
            )}

            {/* Other data types - in scrollable container */}
            {testResult?.test_passed && testResult.items && testResult.source_type !== 'chart' && Array.isArray(testResult.items) && testResult.items.length > 0 && (
              <div>
                <div className="text-[12px] font-medium text-foreground mb-2">{t('configuration:dataSources.result.preview')}</div>
                <div className="space-y-1.5 max-h-60 overflow-y-auto scrollbar">

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
                            marketSignTextClass(quoteItem.change_pct)
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
                            marketSignTextClass(flowItem.main_net)
                          }`}>
                            {(flowItem.main_net ?? 0) > 0 ? '+' : ''}{formatNumber(flowItem.main_net ?? 0, { notation: 'compact', maximumFractionDigits: 2 })}
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
                          marketSignTextClass(dtItem.net_buy)
                        }`}>
                          {(dtItem.net_buy ?? 0) > 0 ? '+' : ''}{formatNumber(dtItem.net_buy ?? 0, { notation: 'compact', maximumFractionDigits: 2 })}
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
                          <span className="text-[12px] font-mono">{formatNumber(marginItem.total_balance ?? 0, { notation: 'compact', maximumFractionDigits: 2 })}</span>
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
                          <span className="text-[12px] font-mono">{shItem.holder_num != null ? formatNumber(shItem.holder_num) : '-'}</span>
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
                          <span className="text-[12px] font-mono">{divItem.dividend_per_share?.toFixed(4) ?? '-'} {t('configuration:dataSources.result.cnyPerShare')}</span>
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
                            marketSignTextClass(nbItem.total_net)
                          }`}>
                            {(nbItem.total_net ?? 0) > 0 ? '+' : ''}{formatNumber(nbItem.total_net ?? 0, { notation: 'compact', maximumFractionDigits: 2 })}
                          </span>
                          <span className="text-[11px] text-muted-foreground">
                            {t('configuration:dataSources.result.shanghaiConnect', { value: formatNumber(nbItem.hgt_net ?? 0, { notation: 'compact', maximumFractionDigits: 2 }) })}
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
                {t('configuration:dataSources.result.testSymbols', { symbols: testResult.test_symbols.join(', ') })}
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
