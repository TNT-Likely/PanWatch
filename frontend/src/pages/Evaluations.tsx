import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CheckCircle2, ClipboardCheck, Clock3, RefreshCw, Target } from 'lucide-react'
import {
  evaluationsApi,
  type AgentPredictionFilters,
  type AgentPredictionGroup,
  type AgentPredictionListResponse,
  type AgentPredictionOutcomeItem,
  type AgentPredictionSummary,
  type EvaluationHorizonUnit,
} from '@panwatch/api'
import { Badge } from '@panwatch/base-ui/components/ui/badge'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

type FilterState = {
  agentName: string
  market: string
  action: string
  status: string
  horizonUnit: EvaluationHorizonUnit
  days: number
  startDate: string
  endDate: string
}

const INITIAL_FILTERS: FilterState = {
  agentName: 'all', market: 'all', action: 'all', status: 'all',
  horizonUnit: 'trading_days', days: 90, startDate: '', endDate: '',
}

const ACTION_LABELS: Record<string, string> = {
  buy: '买入', add: '加仓', sell: '卖出', reduce: '减持', avoid: '回避', hold: '持有', watch: '观望',
}

function toApiFilters(filters: FilterState): AgentPredictionFilters {
  return {
    agentName: filters.agentName === 'all' ? undefined : filters.agentName,
    market: filters.market === 'all' ? undefined : filters.market,
    action: filters.action === 'all' ? undefined : filters.action,
    status: filters.status === 'all' ? undefined : filters.status,
    horizonUnit: filters.horizonUnit, days: filters.days,
    startDate: filters.startDate || undefined, endDate: filters.endDate || undefined, limit: 200,
  }
}

function formatPct(value: number | null | undefined) {
  if (value == null) return '--'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function pctClass(value: number | null | undefined) {
  if (value == null || value === 0) return 'text-muted-foreground'
  return value > 0 ? 'text-rose-500' : 'text-emerald-500'
}

function outcomeLabel(outcome?: AgentPredictionOutcomeItem) {
  if (!outcome) return '未记录'
  if (outcome.status === 'pending') return '待回填'
  if (outcome.status === 'no_base_price') return '无基准价'
  return outcome.hit === true ? '命中' : outcome.hit === false ? '未命中' : '无法判定'
}

function OutcomeCell({ outcome }: { outcome?: AgentPredictionOutcomeItem }) {
  if (!outcome || outcome.status === 'pending') return <span className="text-[12px] text-muted-foreground">{outcomeLabel(outcome)}</span>
  return <div className="text-right"><div className={`font-mono text-[12px] ${pctClass(outcome.return_pct)}`}>{formatPct(outcome.return_pct)}</div><div className={`text-[10px] ${outcome.hit ? 'text-emerald-600' : 'text-muted-foreground'}`}>{outcomeLabel(outcome)}</div></div>
}

function SummaryCard({ label, value, hint, tone = 'default' }: { label: string; value: string; hint?: string; tone?: 'default' | 'positive' | 'warning' }) {
  const valueClass = tone === 'positive' ? 'text-emerald-600' : tone === 'warning' ? 'text-amber-600' : 'text-foreground'
  return <div className="rounded-xl border border-border/60 bg-card/70 p-3.5"><div className="text-[11px] text-muted-foreground">{label}</div><div className={`mt-1 text-xl font-bold ${valueClass}`}>{value}</div>{hint && <div className="mt-1 text-[10px] text-muted-foreground">{hint}</div>}</div>
}

export default function EvaluationsPage() {
  const { toast } = useToast()
  const [searchParams] = useSearchParams()
  const [filters, setFilters] = useState<FilterState>(INITIAL_FILTERS)
  const [data, setData] = useState<AgentPredictionListResponse | null>(null)
  const [summary, setSummary] = useState<AgentPredictionSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [evaluating, setEvaluating] = useState(false)
  const [selected, setSelected] = useState<AgentPredictionGroup | null>(null)
  const targetGroupId = searchParams.get('prediction_group_id') || ''
  const apiFilters = useMemo(() => toApiFilters(filters), [filters])
  const rows = data?.items || []
  const options = data?.available_filters
  const policy = data?.policy || summary?.policy
  const oneDay = summary?.horizons['1']
  const fiveDay = summary?.horizons['5']

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [list, nextSummary] = await Promise.all([
        evaluationsApi.listAgentPredictions(apiFilters), evaluationsApi.getAgentPredictionSummary(apiFilters),
      ])
      setData(list)
      setSummary(nextSummary)
    } catch (error) {
      toast(error instanceof Error ? error.message : '加载验证数据失败', 'error')
    } finally {
      setLoading(false)
    }
  }, [apiFilters, toast])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!targetGroupId || !data) return
    const target = data.items.find(item => item.prediction_group_id === targetGroupId)
    if (target) setSelected(target)
  }, [data, targetGroupId])

  const updateFilter = <K extends keyof FilterState>(key: K, value: FilterState[K]) => setFilters(current => ({ ...current, [key]: value }))
  const handleEvaluate = async () => {
    setEvaluating(true)
    try {
      const result = await evaluationsApi.evaluateAgentPredictions()
      toast(`检查完成：新增回填 ${result.evaluated} 条，待到期 ${result.skipped_not_due} 条`, 'success')
      await load()
    } catch (error) {
      toast(error instanceof Error ? error.message : '检查建议失败', 'error')
    } finally {
      setEvaluating(false)
    }
  }

  return <div className="w-full space-y-4 md:space-y-6">
    <section className="card overflow-hidden">
      <div className="p-4 md:p-5 border-b border-border/60">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex gap-3"><div className="w-10 h-10 shrink-0 rounded-xl bg-gradient-to-br from-violet-500 to-indigo-500 flex items-center justify-center shadow-sm"><ClipboardCheck className="w-5 h-5 text-white" /></div><div><div className="flex items-center gap-2 flex-wrap"><h1 className="text-lg md:text-xl font-bold">验证中心</h1><Badge variant="secondary">Agent 建议复盘</Badge></div><p className="mt-1 text-[12px] md:text-[13px] text-muted-foreground">把已给出的建议与后续实际走势对照；TA 单股 1/5/20 日历史决策仍保留在分析详情。</p></div></div>
          <Button variant="outline" size="sm" onClick={() => void handleEvaluate()} disabled={evaluating}><RefreshCw className={`w-3.5 h-3.5 ${evaluating ? 'animate-spin' : ''}`} />{evaluating ? '正在检查' : '检查已到期建议'}</Button>
        </div>
      </div>
      <div className="p-4 md:p-5 space-y-4">
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3"><SummaryCard label="已记录建议" value={String(summary?.suggestion_count ?? '--')} hint="按一次建议去重" /><SummaryCard label="待回填" value={String(summary?.pending_count ?? '--')} hint="未满评估交易日" tone="warning" /><SummaryCard label="1 个交易日命中" value={oneDay?.hit_rate != null ? `${(oneDay.hit_rate * 100).toFixed(0)}%` : '--'} hint={`样本 ${oneDay?.completed_count ?? 0}`} tone="positive" /><SummaryCard label="5 个交易日命中" value={fiveDay?.hit_rate != null ? `${(fiveDay.hit_rate * 100).toFixed(0)}%` : '--'} hint={`样本 ${fiveDay?.completed_count ?? 0}`} tone="positive" /><SummaryCard label="5 日平均收益" value={formatPct(fiveDay?.avg_return_pct)} hint="仅已完成交易日口径" /></div>
        {summary?.insufficient_sample && <div className="flex items-center gap-2 rounded-lg bg-amber-500/10 px-3 py-2 text-[11px] text-amber-700 dark:text-amber-300"><Target className="w-3.5 h-3.5 shrink-0" />5 个交易日已完成样本不足 20 条，命中率仅作复盘参考，暂不代表稳定结论。</div>}
        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-2 pt-1">
          <Select value={filters.agentName} onValueChange={value => updateFilter('agentName', value)}><SelectTrigger className="h-8 text-[12px]"><SelectValue placeholder="全部 Agent" /></SelectTrigger><SelectContent><SelectItem value="all">全部 Agent</SelectItem>{options?.agent_names.map(value => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select>
          <Select value={filters.market} onValueChange={value => updateFilter('market', value)}><SelectTrigger className="h-8 text-[12px]"><SelectValue placeholder="全部市场" /></SelectTrigger><SelectContent><SelectItem value="all">全部市场</SelectItem>{options?.markets.map(value => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select>
          <Select value={filters.action} onValueChange={value => updateFilter('action', value)}><SelectTrigger className="h-8 text-[12px]"><SelectValue placeholder="全部动作" /></SelectTrigger><SelectContent><SelectItem value="all">全部动作</SelectItem>{options?.actions.map(value => <SelectItem key={value} value={value}>{ACTION_LABELS[value] || value}</SelectItem>)}</SelectContent></Select>
          <Select value={filters.status} onValueChange={value => updateFilter('status', value)}><SelectTrigger className="h-8 text-[12px]"><SelectValue placeholder="全部状态" /></SelectTrigger><SelectContent><SelectItem value="all">全部状态</SelectItem>{options?.statuses.map(value => <SelectItem key={value} value={value}>{value === 'evaluated' ? '已验证' : value === 'pending' ? '待回填' : value}</SelectItem>)}</SelectContent></Select>
          <Select value={filters.horizonUnit} onValueChange={value => updateFilter('horizonUnit', value as EvaluationHorizonUnit)}><SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="trading_days">交易日口径</SelectItem><SelectItem value="calendar_days_legacy">旧自然日口径</SelectItem><SelectItem value="all">全部口径</SelectItem></SelectContent></Select>
          <Select value={String(filters.days)} onValueChange={value => updateFilter('days', Number(value))}><SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="30">最近 30 天</SelectItem><SelectItem value="90">最近 90 天</SelectItem><SelectItem value="180">最近 180 天</SelectItem><SelectItem value="365">最近 365 天</SelectItem></SelectContent></Select>
          <div className="col-span-1 flex items-center gap-1.5"><Label className="sr-only" htmlFor="evaluation-start">开始日期</Label><Input id="evaluation-start" type="date" className="h-8 text-[11px]" value={filters.startDate} onChange={event => updateFilter('startDate', event.target.value)} /></div>
          <div className="col-span-1 flex items-center gap-1.5"><Label className="sr-only" htmlFor="evaluation-end">结束日期</Label><Input id="evaluation-end" type="date" className="h-8 text-[11px]" value={filters.endDate} onChange={event => updateFilter('endDate', event.target.value)} /></div>
        </div>
      </div>
    </section>
    <section className="card overflow-hidden"><div className="px-4 md:px-5 py-3 border-b border-border/60 flex items-center justify-between"><div className="text-[13px] font-semibold">建议明细</div><div className="text-[11px] text-muted-foreground">共 {data?.total ?? 0} 条建议</div></div>{loading ? <div className="py-14 text-center text-[13px] text-muted-foreground">正在加载建议复盘…</div> : rows.length === 0 ? <div className="py-14 text-center text-[13px] text-muted-foreground">当前筛选条件下暂无建议记录</div> : <div className="overflow-x-auto"><table className="w-full min-w-[860px] text-[12px]"><thead className="bg-accent/20 text-muted-foreground text-[11px]"><tr className="border-b border-border/50"><th className="py-2.5 px-4 text-left font-medium">建议日期</th><th className="py-2.5 px-2 text-left font-medium">标的</th><th className="py-2.5 px-2 text-left font-medium">来源</th><th className="py-2.5 px-2 text-left font-medium">动作</th><th className="py-2.5 px-2 text-right font-medium">置信度</th><th className="py-2.5 px-2 text-right font-medium">建议价</th><th className="py-2.5 px-3 text-right font-medium">1 个交易日</th><th className="py-2.5 px-4 text-right font-medium">5 个交易日</th></tr></thead><tbody>{rows.map(row => <tr key={row.prediction_group_id} onClick={() => setSelected(row)} className="border-b border-border/40 cursor-pointer hover:bg-accent/30 transition-colors"><td className="py-3 px-4 font-mono text-muted-foreground">{row.prediction_date}</td><td className="py-3 px-2 font-medium">{row.stock_symbol}<span className="ml-1 text-[10px] text-muted-foreground">{row.stock_market}</span></td><td className="py-3 px-2 text-muted-foreground">{row.agent_name}</td><td className="py-3 px-2"><Badge variant="secondary" className="px-1.5 py-0.5">{row.action_label || ACTION_LABELS[row.action] || row.action}</Badge>{row.is_legacy_group && <span className="ml-1.5 text-[10px] text-amber-600">旧口径</span>}</td><td className="py-3 px-2 text-right font-mono">{row.confidence == null ? '--' : row.confidence.toFixed(2)}</td><td className="py-3 px-2 text-right font-mono">{row.trigger_price == null ? '--' : row.trigger_price.toFixed(2)}</td><td className="py-3 px-3"><OutcomeCell outcome={row.outcomes['1']} /></td><td className="py-3 px-4"><OutcomeCell outcome={row.outcomes['5']} /></td></tr>)}</tbody></table></div>}</section>
    <Dialog open={!!selected} onOpenChange={open => !open && setSelected(null)}><DialogContent className="max-w-xl max-h-[80vh] overflow-y-auto"><DialogHeader><DialogTitle>{selected ? `${selected.stock_symbol} · ${selected.action_label || selected.action}` : '建议后验详情'}</DialogTitle><DialogDescription>{selected?.prediction_date} · {selected?.agent_name} · {selected?.stock_market}</DialogDescription></DialogHeader>{selected && <div className="space-y-4 text-[13px]"><div className="grid grid-cols-3 gap-3 rounded-xl bg-accent/30 p-3"><div><div className="text-[10px] text-muted-foreground">置信度</div><div className="mt-1 font-medium">{selected.confidence == null ? '--' : selected.confidence.toFixed(2)}</div></div><div><div className="text-[10px] text-muted-foreground">建议价格</div><div className="mt-1 font-mono">{selected.trigger_price == null ? '--' : selected.trigger_price.toFixed(2)}</div></div><div><div className="text-[10px] text-muted-foreground">评估口径</div><div className="mt-1 font-medium">{selected.is_legacy_group ? '旧自然日' : '交易日'}</div></div></div>{(selected.reason || selected.signal) && <div className="space-y-2"><div className="font-medium">当时依据</div>{selected.signal && <div className="rounded-lg border border-border/60 p-2.5 text-muted-foreground">信号：{selected.signal}</div>}{selected.reason && <div className="rounded-lg border border-border/60 p-2.5 leading-relaxed text-muted-foreground">{selected.reason}</div>}</div>}<div className="space-y-2"><div className="font-medium">后验结果</div>{['1', '5'].map(horizon => { const outcome = selected.outcomes[horizon]; return <div key={horizon} className="flex items-center justify-between rounded-lg border border-border/60 p-3"><div className="flex items-center gap-2"><Clock3 className="w-3.5 h-3.5 text-muted-foreground" /><span>{horizon} 个交易日</span></div><div className="text-right"><div className={`font-mono ${pctClass(outcome?.return_pct)}`}>{outcome?.status === 'pending' ? '待回填' : formatPct(outcome?.return_pct)}</div><div className="text-[10px] text-muted-foreground">{outcomeLabel(outcome)}</div></div></div> })}</div>{policy && <div className="rounded-lg bg-primary/5 p-3 text-[11px] text-muted-foreground"><div className="mb-1.5 flex items-center gap-1.5 font-medium text-foreground"><CheckCircle2 className="w-3.5 h-3.5 text-primary" />命中规则</div>{policy.actions[selected.action] || `观望类建议：绝对收益小于 ${policy.flat_threshold_pct}% 记为命中`}</div>}</div>}</DialogContent></Dialog>
  </div>
}
