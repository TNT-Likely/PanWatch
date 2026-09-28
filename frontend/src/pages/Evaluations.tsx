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
import { useTranslation } from 'react-i18next'
import { localizeAgentName } from '@/i18n/agent-labels'
import { marketSignTextClass } from '@/lib/market-colors'

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

const ACTION_KEYS: Record<string, string> = { buy: 'buy', add: 'add', sell: 'sell', reduce: 'reduce', avoid: 'avoid', hold: 'hold', watch: 'watch' }

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
  return marketSignTextClass(value)
}

function outcomeLabel(outcome: AgentPredictionOutcomeItem | undefined, t: (key: string) => string) {
  if (!outcome) return t('outcomes.unrecorded')
  if (outcome.status === 'pending') return t('outcomes.pending')
  if (outcome.status === 'no_base_price') return t('outcomes.noBasePrice')
  return outcome.hit === true ? t('outcomes.hit') : outcome.hit === false ? t('outcomes.missed') : t('outcomes.unknown')
}

function OutcomeCell({ outcome, t }: { outcome?: AgentPredictionOutcomeItem; t: (key: string) => string }) {
  if (!outcome || outcome.status === 'pending') return <span className="text-[12px] text-muted-foreground">{outcomeLabel(outcome, t)}</span>
  const hitClass = outcome.hit ? 'text-emerald-600' : 'text-muted-foreground' // market-color-fixed: evaluation success status
  return (
    <div className="text-right">
      <div className={`font-mono text-[12px] ${pctClass(outcome.return_pct)}`}>{formatPct(outcome.return_pct)}</div>
      <div className={`text-[10px] ${hitClass}`}>{outcomeLabel(outcome, t)}</div>
    </div>
  )
}

function SummaryCard({ label, value, hint, tone = 'default' }: { label: string; value: string; hint?: string; tone?: 'default' | 'positive' | 'warning' }) {
  const valueClass = tone === 'positive' ? 'text-emerald-600' : tone === 'warning' ? 'text-amber-600' : 'text-foreground'
  return <div className="rounded-xl border border-border/60 bg-card/70 p-3.5"><div className="text-[11px] text-muted-foreground">{label}</div><div className={`mt-1 text-xl font-bold ${valueClass}`}>{value}</div>{hint && <div className="mt-1 text-[10px] text-muted-foreground">{hint}</div>}</div>
}

export default function EvaluationsPage() {
  const { t } = useTranslation('configuration')
  const { t: commonT } = useTranslation('common')
  const evaluationT = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const ev = (key: string, options?: Record<string, unknown>) => evaluationT(`p4.evaluations.${key}`, options)
  const actionLabel = useCallback((action: string, fallback = '') => (
    ACTION_KEYS[action] ? ev(`actions.${ACTION_KEYS[action]}`) : fallback || action
  ), [t])
  const agentLabel = useCallback((name: string) => localizeAgentName(name, name, evaluationT), [t])
  const marketLabel = useCallback((market: string) => commonT(`markets.${market}`, { defaultValue: market }), [commonT])
  const localizePolicy = useCallback(<T extends { flat_threshold_pct: number; actions: Record<string, string> },>(policy: T): T => ({
    ...policy,
    actions: Object.fromEntries(Object.keys(policy.actions).map((action) => {
      const key = ['buy', 'add'].includes(action)
        ? 'up'
        : ['sell', 'reduce', 'avoid'].includes(action)
          ? 'down'
          : 'flat'
      return [action, evaluationT(`p4.evaluationRules.${key}`, { threshold: policy.flat_threshold_pct })]
    })),
  }), [t])
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
      setData({
        ...list,
        items: list.items.map(item => ({
          ...item,
          agent_name: agentLabel(item.agent_name),
          stock_market: marketLabel(item.stock_market),
          action_label: actionLabel(item.action, item.action_label),
        })),
        policy: localizePolicy(list.policy),
      })
      setSummary({ ...nextSummary, policy: localizePolicy(nextSummary.policy) })
    } catch (error) {
      toast(error instanceof Error ? error.message : ev('messages.loadFailed'), 'error')
    } finally {
      setLoading(false)
    }
  }, [apiFilters, toast, actionLabel, agentLabel, localizePolicy, marketLabel])

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
      toast(ev('messages.evaluated', { evaluated: result.evaluated, skipped: result.skipped_not_due }), 'success')
      await load()
    } catch (error) {
      toast(error instanceof Error ? error.message : ev('messages.evaluateFailed'), 'error')
    } finally {
      setEvaluating(false)
    }
  }

  return <div className="w-full space-y-4 md:space-y-6">
    <section className="card overflow-hidden">
      <div className="p-4 md:p-5 border-b border-border/60">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex gap-3"><div className="w-10 h-10 shrink-0 rounded-xl bg-gradient-to-br from-violet-500 to-indigo-500 flex items-center justify-center shadow-sm"><ClipboardCheck className="w-5 h-5 text-white" /></div><div><div className="flex items-center gap-2 flex-wrap"><h1 className="text-lg md:text-xl font-bold">{ev('title')}</h1><Badge variant="secondary">{ev('badge')}</Badge></div><p className="mt-1 text-[12px] md:text-[13px] text-muted-foreground">{ev('description')}</p></div></div>
          <Button variant="outline" size="sm" onClick={() => void handleEvaluate()} disabled={evaluating}><RefreshCw className={`w-3.5 h-3.5 ${evaluating ? 'animate-spin' : ''}`} />{evaluating ? ev('checking') : ev('checkDue')}</Button>
        </div>
      </div>
      <div className="p-4 md:p-5 space-y-4">
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3"><SummaryCard label={ev('suggestionsRecorded')} value={String(summary?.suggestion_count ?? '--')} hint={ev('deduplicated')} /><SummaryCard label={ev('pending')} value={String(summary?.pending_count ?? '--')} hint={ev('pendingHint')} tone="warning" /><SummaryCard label={ev('dayHitRate', { days: 1 })} value={oneDay?.hit_rate != null ? `${(oneDay.hit_rate * 100).toFixed(0)}%` : '--'} hint={ev('samples', { count: oneDay?.completed_count ?? 0 })} tone="positive" /><SummaryCard label={ev('dayHitRate', { days: 5 })} value={fiveDay?.hit_rate != null ? `${(fiveDay.hit_rate * 100).toFixed(0)}%` : '--'} hint={ev('samples', { count: fiveDay?.completed_count ?? 0 })} tone="positive" /><SummaryCard label={ev('averageReturn')} value={formatPct(fiveDay?.avg_return_pct)} hint={ev('completedOnly')} /></div>
        {summary?.insufficient_sample && <div className="flex items-center gap-2 rounded-lg bg-amber-500/10 px-3 py-2 text-[11px] text-amber-700 dark:text-amber-300"><Target className="w-3.5 h-3.5 shrink-0" />{ev('insufficientSample')}</div>}
        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-2 pt-1">
          <Select value={filters.agentName} onValueChange={value => updateFilter('agentName', value)}><SelectTrigger className="h-8 text-[12px]"><SelectValue placeholder={ev('allAgents')} /></SelectTrigger><SelectContent><SelectItem value="all">{ev('allAgents')}</SelectItem>{options?.agent_names.map(value => <SelectItem key={value} value={value}>{agentLabel(value)}</SelectItem>)}</SelectContent></Select>
          <Select value={filters.market} onValueChange={value => updateFilter('market', value)}><SelectTrigger className="h-8 text-[12px]"><SelectValue placeholder={ev('allMarkets')} /></SelectTrigger><SelectContent><SelectItem value="all">{ev('allMarkets')}</SelectItem>{options?.markets.map(value => <SelectItem key={value} value={value}>{marketLabel(value)}</SelectItem>)}</SelectContent></Select>
          <Select value={filters.action} onValueChange={value => updateFilter('action', value)}><SelectTrigger className="h-8 text-[12px]"><SelectValue placeholder={ev('allActions')} /></SelectTrigger><SelectContent><SelectItem value="all">{ev('allActions')}</SelectItem>{options?.actions.map(value => <SelectItem key={value} value={value}>{ACTION_KEYS[value] ? ev(`actions.${ACTION_KEYS[value]}`) : value}</SelectItem>)}</SelectContent></Select>
          <Select value={filters.status} onValueChange={value => updateFilter('status', value)}><SelectTrigger className="h-8 text-[12px]"><SelectValue placeholder={ev('allStatuses')} /></SelectTrigger><SelectContent><SelectItem value="all">{ev('allStatuses')}</SelectItem>{options?.statuses.map(value => <SelectItem key={value} value={value}>{value === 'evaluated' ? ev('verified') : value === 'pending' ? ev('outcomes.pending') : value}</SelectItem>)}</SelectContent></Select>
          <Select value={filters.horizonUnit} onValueChange={value => updateFilter('horizonUnit', value as EvaluationHorizonUnit)}><SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="trading_days">{ev('tradingDayBasis')}</SelectItem><SelectItem value="calendar_days_legacy">{ev('legacyCalendarBasis')}</SelectItem><SelectItem value="all">{ev('allBasis')}</SelectItem></SelectContent></Select>
          <Select value={String(filters.days)} onValueChange={value => updateFilter('days', Number(value))}><SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger><SelectContent>{[30, 90, 180, 365].map(days => <SelectItem key={days} value={String(days)}>{ev('recentDays', { days })}</SelectItem>)}</SelectContent></Select>
          <div className="col-span-1 flex items-center gap-1.5"><Label className="sr-only" htmlFor="evaluation-start">{ev('startDate')}</Label><Input id="evaluation-start" type="date" className="h-8 text-[11px]" value={filters.startDate} onChange={event => updateFilter('startDate', event.target.value)} /></div>
          <div className="col-span-1 flex items-center gap-1.5"><Label className="sr-only" htmlFor="evaluation-end">{ev('endDate')}</Label><Input id="evaluation-end" type="date" className="h-8 text-[11px]" value={filters.endDate} onChange={event => updateFilter('endDate', event.target.value)} /></div>
        </div>
      </div>
    </section>
    <section className="card overflow-hidden"><div className="px-4 md:px-5 py-3 border-b border-border/60 flex items-center justify-between"><div className="text-[13px] font-semibold">{ev('details')}</div><div className="text-[11px] text-muted-foreground">{ev('suggestionCount', { count: data?.total ?? 0 })}</div></div>{loading ? <div className="py-14 text-center text-[13px] text-muted-foreground">{ev('loadingReview')}</div> : rows.length === 0 ? <div className="py-14 text-center text-[13px] text-muted-foreground">{ev('noRows')}</div> : <div className="overflow-x-auto"><table className="w-full min-w-[860px] text-[12px]"><thead className="bg-accent/20 text-muted-foreground text-[11px]"><tr className="border-b border-border/50"><th className="py-2.5 px-4 text-left font-medium">{ev('suggestionDate')}</th><th className="py-2.5 px-2 text-left font-medium">{ev('symbol')}</th><th className="py-2.5 px-2 text-left font-medium">{ev('source')}</th><th className="py-2.5 px-2 text-left font-medium">{ev('action')}</th><th className="py-2.5 px-2 text-right font-medium">{ev('confidence')}</th><th className="py-2.5 px-2 text-right font-medium">{ev('suggestedPrice')}</th><th className="py-2.5 px-3 text-right font-medium">{ev('tradingDays', { count: 1 })}</th><th className="py-2.5 px-4 text-right font-medium">{ev('tradingDays', { count: 5 })}</th></tr></thead><tbody>{rows.map(row => <tr key={row.prediction_group_id} onClick={() => setSelected(row)} className="border-b border-border/40 cursor-pointer hover:bg-accent/30 transition-colors"><td className="py-3 px-4 font-mono text-muted-foreground">{row.prediction_date}</td><td className="py-3 px-2 font-medium">{row.stock_symbol}<span className="ml-1 text-[10px] text-muted-foreground">{row.stock_market}</span></td><td className="py-3 px-2 text-muted-foreground">{row.agent_name}</td><td className="py-3 px-2"><Badge variant="secondary" className="px-1.5 py-0.5">{row.action_label || (ACTION_KEYS[row.action] ? ev(`actions.${ACTION_KEYS[row.action]}`) : row.action)}</Badge>{row.is_legacy_group && <span className="ml-1.5 text-[10px] text-amber-600">{ev('legacyBasis')}</span>}</td><td className="py-3 px-2 text-right font-mono">{row.confidence == null ? '--' : row.confidence.toFixed(2)}</td><td className="py-3 px-2 text-right font-mono">{row.trigger_price == null ? '--' : row.trigger_price.toFixed(2)}</td><td className="py-3 px-3"><OutcomeCell outcome={row.outcomes['1']} t={key => ev(key)} /></td><td className="py-3 px-4"><OutcomeCell outcome={row.outcomes['5']} t={key => ev(key)} /></td></tr>)}</tbody></table></div>}</section>
    <Dialog open={!!selected} onOpenChange={open => !open && setSelected(null)}><DialogContent className="max-w-xl max-h-[80vh] overflow-y-auto"><DialogHeader><DialogTitle>{selected ? `${selected.stock_symbol} · ${selected.action_label || (ACTION_KEYS[selected.action] ? ev(`actions.${ACTION_KEYS[selected.action]}`) : selected.action)}` : ev('detailTitle')}</DialogTitle><DialogDescription>{selected?.prediction_date} · {selected?.agent_name} · {selected?.stock_market}</DialogDescription></DialogHeader>{selected && <div className="space-y-4 text-[13px]"><div className="grid grid-cols-3 gap-3 rounded-xl bg-accent/30 p-3"><div><div className="text-[10px] text-muted-foreground">{ev('confidence')}</div><div className="mt-1 font-medium">{selected.confidence == null ? '--' : selected.confidence.toFixed(2)}</div></div><div><div className="text-[10px] text-muted-foreground">{ev('price')}</div><div className="mt-1 font-mono">{selected.trigger_price == null ? '--' : selected.trigger_price.toFixed(2)}</div></div><div><div className="text-[10px] text-muted-foreground">{ev('evaluationBasis')}</div><div className="mt-1 font-medium">{selected.is_legacy_group ? ev('oldCalendarDays') : ev('tradingDaysShort')}</div></div></div>{(selected.reason || selected.signal) && <div className="space-y-2"><div className="font-medium">{ev('rationale')}</div>{selected.signal && <div className="rounded-lg border border-border/60 p-2.5 text-muted-foreground">{ev('signal')}: {selected.signal}</div>}{selected.reason && <div className="rounded-lg border border-border/60 p-2.5 leading-relaxed text-muted-foreground">{selected.reason}</div>}</div>}<div className="space-y-2"><div className="font-medium">{ev('outcome')}</div>{['1', '5'].map(horizon => { const outcome = selected.outcomes[horizon]; return <div key={horizon} className="flex items-center justify-between rounded-lg border border-border/60 p-3"><div className="flex items-center gap-2"><Clock3 className="w-3.5 h-3.5 text-muted-foreground" /><span>{ev('tradingDays', { count: Number(horizon) })}</span></div><div className="text-right"><div className={`font-mono ${pctClass(outcome?.return_pct)}`}>{outcome?.status === 'pending' ? ev('outcomes.pending') : formatPct(outcome?.return_pct)}</div><div className="text-[10px] text-muted-foreground">{outcomeLabel(outcome, key => ev(key))}</div></div></div> })}</div>{policy && <div className="rounded-lg bg-primary/5 p-3 text-[11px] text-muted-foreground"><div className="mb-1.5 flex items-center gap-1.5 font-medium text-foreground"><CheckCircle2 className="w-3.5 h-3.5 text-primary" />{ev('matchedRules')}</div>{policy.actions[selected.action] || ev('flatSuggestionRule', { threshold: policy.flat_threshold_pct })}</div>}</div>}</DialogContent></Dialog>
  </div>
}
