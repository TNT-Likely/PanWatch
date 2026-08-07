import { useState, useEffect } from 'react'
import { Search, BookOpen, TrendingUp, TrendingDown, AlertCircle, CheckCircle2, Loader2, Play, X, Sparkles } from 'lucide-react'
import { strategiesApi, type StrategyItem, type ApplyResult } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'

const CATEGORY_LABELS: Record<string, string> = {
  value: '价值',
  momentum: '动量',
  reversal: '反转',
  framework: '多因子',
  quality: '质量',
  income: '收益',
}

const BADGE_COLORS: Record<string, string> = {
  价值: 'bg-blue-500/15 text-blue-500 border-blue-500/30',
  资金: 'bg-orange-500/15 text-orange-500 border-orange-500/30',
  突破: 'bg-red-500/15 text-red-500 border-red-500/30',
  反弹: 'bg-purple-500/15 text-purple-500 border-purple-500/30',
  动量: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
  防御: 'bg-slate-500/15 text-slate-500 border-slate-500/30',
  多因子: 'bg-amber-500/15 text-amber-500 border-amber-500/30',
  蓝筹: 'bg-cyan-500/15 text-cyan-500 border-cyan-500/30',
}

function scoreColor(s: number): string {
  if (s >= 75) return 'text-emerald-500'
  if (s >= 50) return 'text-amber-500'
  return 'text-red-500'
}

function scoreBg(s: number): string {
  if (s >= 75) return 'bg-emerald-500/10 border-emerald-500/30'
  if (s >= 50) return 'bg-amber-500/10 border-amber-500/30'
  return 'bg-red-500/10 border-red-500/30'
}

export default function StrategiesPage() {
  const [items, setItems] = useState<StrategyItem[]>([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [categoryFilter, setCategoryFilter] = useState<string>('')
  const [selected, setSelected] = useState<StrategyItem | null>(null)
  const [symbol, setSymbol] = useState('002361')
  const [applyResult, setApplyResult] = useState<ApplyResult | null>(null)
  const [applying, setApplying] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const res = await strategiesApi.list()
      setItems(res.items)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const filtered = items.filter(it => {
    if (categoryFilter && it.category !== categoryFilter) return false
    if (search) {
      const q = search.toLowerCase()
      return it.display_name.toLowerCase().includes(q) ||
        it.description.toLowerCase().includes(q) ||
        it.tags.some(t => t.toLowerCase().includes(q))
    }
    return true
  })

  const categories = Array.from(new Set(items.map(it => it.category)))

  const doApply = async () => {
    if (!selected) return
    setApplying(true)
    setApplyResult(null)
    try {
      const res = await strategiesApi.apply({
        strategy_id: selected.id,
        symbol: symbol.trim(),
        market: 'CN',
      })
      setApplyResult(res)
    } catch (e: any) {
      setApplyResult({
        strategy_id: selected.id,
        symbol,
        market: 'CN',
        passed: false,
        score: 0,
        score_breakdown: [],
        failed_filters: [],
        missing_fields: [],
        current_data: {},
        error: e?.message || String(e),
      } as any)
    } finally {
      setApplying(false)
    }
  }

  return (
    <div className="space-y-5 p-4 md:p-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold flex items-center gap-2">
            <BookOpen className="w-5 h-5 text-primary" />
            策略库
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            借鉴 <code className="text-xs">alphasift</code> 的 11 个 YAML 策略,翻译为 PanWatch 可用字段子集
          </p>
        </div>
        <span className="text-xs text-muted-foreground">
          共 {items.length} 个策略, {items.filter(i => i.available_now).length} 个实时可用
        </span>
      </div>

      {/* 来源声明 */}
      <div className="card-subtle p-3 text-xs text-muted-foreground">
        💡 策略数据源:<code className="text-[10px]">strategies/panwatch_strategies.yaml</code> ·
        实时字段(腾讯)可直接跑, 盘后字段(PE/PB/市值)需等东财恢复。
        <a className="text-primary hover:underline ml-2" href="https://github.com/ZhuLinsen/alphasift" target="_blank" rel="noreferrer">原始项目 ↗</a>
      </div>

      {/* 筛选 */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="relative flex-1 min-w-[180px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input placeholder="搜索策略 / 标签 / 描述" value={search} onChange={e => setSearch(e.target.value)} className="pl-9" />
        </div>
        <select
          value={categoryFilter}
          onChange={e => setCategoryFilter(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
        >
          <option value="">全部分类</option>
          {categories.map(c => (
            <option key={c} value={c}>{CATEGORY_LABELS[c] || c}</option>
          ))}
        </select>
        <div className="text-xs text-muted-foreground">{filtered.length} / {items.length}</div>
      </div>

      {/* 策略卡片 */}
      {loading ? (
        <div className="flex items-center justify-center py-12 text-muted-foreground">
          <Loader2 className="w-5 h-5 animate-spin mr-2" /> 加载中...
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map(it => {
            const badgeCls = BADGE_COLORS[it.ui_badge] || 'bg-accent/30 text-foreground border-border'
            return (
              <button
                key={it.id}
                onClick={() => { setSelected(it); setApplyResult(null) }}
                className="card-subtle p-4 text-left hover:border-primary/40 transition-colors"
              >
                <div className="flex items-start justify-between gap-2 mb-2">
                  <h3 className="font-semibold text-sm flex-1">{it.display_name}</h3>
                  <span className={`text-[10px] px-2 py-0.5 rounded-full border ${badgeCls}`}>
                    {it.ui_badge}
                  </span>
                </div>
                <p className="text-xs text-muted-foreground line-clamp-2 min-h-[2.5em]">
                  {it.description}
                </p>
                <div className="flex items-center gap-2 mt-3 flex-wrap">
                  {it.tags.slice(0, 3).map(t => (
                    <span key={t} className="text-[10px] px-1.5 py-0.5 rounded bg-accent/40 text-muted-foreground">
                      {t}
                    </span>
                  ))}
                </div>
                <div className="flex items-center justify-between mt-3 pt-3 border-t border-border/50">
                  <div className="text-[10px] text-muted-foreground">
                    {it.source}
                  </div>
                  {it.available_now ? (
                    <span className="text-[10px] text-emerald-500 flex items-center gap-1">
                      <CheckCircle2 className="w-3 h-3" /> 实时
                    </span>
                  ) : (
                    <span className="text-[10px] text-yellow-500 flex items-center gap-1">
                      <AlertCircle className="w-3 h-3" /> 需盘后
                    </span>
                  )}
                </div>
              </button>
            )
          })}
        </div>
      )}

      {/* 策略详情 + 应用 */}
      <Dialog open={!!selected} onOpenChange={o => !o && setSelected(null)}>
        <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
          {selected && (
            <>
              <DialogHeader>
                <DialogTitle className="flex items-center gap-2">
                  {selected.display_name}
                  <span className={`text-[10px] px-2 py-0.5 rounded-full border ${BADGE_COLORS[selected.ui_badge] || 'bg-accent/30'}`}>
                    {selected.ui_badge}
                  </span>
                  {selected.available_now ? (
                    <span className="text-[10px] text-emerald-500 flex items-center gap-1">
                      <CheckCircle2 className="w-3 h-3" /> 实时可用
                    </span>
                  ) : (
                    <span className="text-[10px] text-yellow-500 flex items-center gap-1">
                      <AlertCircle className="w-3 h-3" /> 需盘后数据
                    </span>
                  )}
                </DialogTitle>
                <DialogDescription>{selected.description}</DialogDescription>
              </DialogHeader>

              <div className="mt-4 space-y-4">
                {/* 硬过滤 */}
                <div>
                  <h4 className="text-xs font-semibold text-muted-foreground mb-2">硬过滤条件</h4>
                  <div className="flex flex-wrap gap-1.5">
                    {Object.entries(selected.filter || {}).map(([k, v]) => (
                      <span key={k} className="text-[10px] px-2 py-0.5 rounded bg-accent/40 font-mono">
                        {k}: {String(v)}
                      </span>
                    ))}
                  </div>
                </div>

                {/* 应用到单只股票 */}
                <div className="card-subtle p-3">
                  <h4 className="text-xs font-semibold mb-2 flex items-center gap-1">
                    <Play className="w-3 h-3" /> 应用到单只股票
                  </h4>
                  <div className="flex items-center gap-2">
                    <Input
                      value={symbol}
                      onChange={e => setSymbol(e.target.value)}
                      placeholder="6位股票代码"
                      maxLength={6}
                      className="w-32"
                    />
                    <Button onClick={doApply} disabled={applying || !symbol.trim()}>
                      {applying ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : <Play className="w-4 h-4 mr-1" />}
                      应用
                    </Button>
                  </div>
                </div>

                {/* 结果 */}
                {applyResult && (
                  <div className={`card-subtle p-4 ${applyResult.passed ? 'border-emerald-500/30' : 'border-amber-500/30'}`}>
                    {applyResult.error ? (
                      <div className="text-red-500 text-sm">❌ {applyResult.error}</div>
                    ) : (
                      <>
                        <div className="flex items-center justify-between mb-3">
                          <div className="flex items-center gap-3">
                            <div className={`text-3xl font-bold ${scoreColor(applyResult.score)}`}>
                              {applyResult.score.toFixed(1)}
                            </div>
                            <div>
                              <div className={`text-sm font-medium ${applyResult.passed ? 'text-emerald-500' : 'text-amber-500'}`}>
                                {applyResult.passed ? '✅ 通过' : '⚠️ 未通过'}
                              </div>
                              <div className="text-xs text-muted-foreground">{applyResult.symbol} @ {applyResult.market}</div>
                            </div>
                          </div>
                          <div className={`px-3 py-1.5 rounded-lg border text-sm font-semibold ${scoreBg(applyResult.score)} ${scoreColor(applyResult.score)}`}>
                            {applyResult.score >= 75 ? '强推荐' : applyResult.score >= 50 ? '中性' : '弱'}
                          </div>
                        </div>

                        {/* 失败过滤 */}
                        {applyResult.failed_filters.length > 0 && (
                          <div className="mb-3">
                            <div className="text-xs font-semibold text-red-500 mb-1">
                              失败过滤 ({applyResult.failed_filters.length}):
                            </div>
                            <div className="space-y-1">
                              {applyResult.failed_filters.map((f, i) => (
                                <div key={i} className="text-xs text-muted-foreground">
                                  <code className="text-red-500">{f.field}</code>=<strong>{String(f.actual)}</strong> 需要 {f.required} {String(f.threshold)}
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* 缺失字段 */}
                        {applyResult.missing_fields.length > 0 && (
                          <div className="mb-3 text-xs text-yellow-500">
                            ⚠️ 缺失字段: {applyResult.missing_fields.join(', ')}(盘后可拿)
                          </div>
                        )}

                        {/* 打分明细 */}
                        {applyResult.score_breakdown.length > 0 && (
                          <div>
                            <div className="text-xs font-semibold mb-1">因子打分:</div>
                            <table className="w-full text-xs">
                              <thead>
                                <tr className="text-muted-foreground">
                                  <th className="text-left py-1">因子</th>
                                  <th className="text-left py-1">原始值</th>
                                  <th className="text-right py-1">得分</th>
                                  <th className="text-right py-1">权重</th>
                                </tr>
                              </thead>
                              <tbody>
                                {applyResult.score_breakdown.map((b, i) => (
                                  <tr key={i} className="border-t border-border/30">
                                    <td className="py-1 font-mono">{b.factor}</td>
                                    <td className="py-1 text-muted-foreground">{String(b.raw)}</td>
                                    <td className={`py-1 text-right font-mono ${scoreColor(b.score)}`}>{b.score.toFixed(1)}</td>
                                    <td className="py-1 text-right text-muted-foreground">{(b.weight * 100).toFixed(0)}%</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}

                        {/* 实时数据 */}
                        {applyResult.current_data && Object.keys(applyResult.current_data).length > 0 && (
                          <details className="mt-3 text-xs text-muted-foreground">
                            <summary className="cursor-pointer">查看实时数据</summary>
                            <pre className="mt-2 p-2 rounded bg-accent/30 text-[10px] overflow-x-auto">
{JSON.stringify(applyResult.current_data, null, 2)}
                            </pre>
                          </details>
                        )}
                      </>
                    )}
                  </div>
                )}
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}