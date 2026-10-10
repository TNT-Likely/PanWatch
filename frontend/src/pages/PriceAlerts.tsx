import { useConfirm } from '@panwatch/base-ui/components/ui/confirm-dialog'
import { useEffect, useMemo, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { Plus, RefreshCw, Play, Trash2, BarChart3, BellRing } from 'lucide-react'
import { fetchAPI, stocksApi, type NotifyChannel } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { useTranslation } from 'react-i18next'
import PriceAlertFormDialog, { type AlertConditionItem, type PriceAlertFormState, type PriceAlertSubmitPayload } from '@panwatch/biz-ui/components/price-alert-form-dialog'
import { getCurrentLocale } from '@/i18n'

interface Delivery {
  id: number
  event_id: string
  channel_id: number | null
  status: string
  attempts: number
  max_attempts: number
  next_attempt_at: string | null
  delivered_at: string | null
  error_code: string
}
interface MonitoringHealth {
  total_rules: number
  truncated: boolean
  scan: { status: string; last_completed_at: string | null; next_scan_at: string | null }
  delivery_counts: Record<string, number>
  items: { rule_id: number; status: string; last_checked_at: string | null; last_success_at: string | null; next_scan_at: string | null; consecutive_failures: number; quota: { used: number; limit: number; unlimited: boolean }; deliveries: Record<string, number> }[]
}

type RuleOp = 'and' | 'or'

interface StockItem {
  id: number
  symbol: string
  name: string
  market: string
}

interface AlertRule {
  id: number
  stock_id: number
  stock_symbol: string
  stock_name: string
  market: string
  name: string
  enabled: boolean
  condition_group: {
    op: RuleOp
    items: AlertConditionItem[]
  }
  market_hours_mode: 'always' | 'trading_only'
  cooldown_minutes: number
  max_triggers_per_day: number
  repeat_mode: 'once' | 'repeat'
  expire_at: string | null
  notify_channel_ids: number[]
  last_trigger_at: string | null
  trigger_count_today: number
  trigger_date: string
}

interface AlertHit {
  id: number
  trigger_time: string
  trigger_snapshot: Record<string, any>
  notify_success: boolean
  notify_error: string
  deliveries: Delivery[]
}

const DEFAULT_FORM: PriceAlertFormState = {
  stock_id: 0,
  name: '',
  op: 'and',
  items: [{ type: 'price', op: '>=', value: 0 }],
  market_hours_mode: 'trading_only',
  cooldown_minutes: 30,
  max_triggers_per_day: 3,
  repeat_mode: 'repeat',
  expire_at: '',
  notify_channel_ids: [],
}

function fmt(iso?: string | null): string {
  if (!iso) return '--'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return '--'
  return d.toLocaleString(getCurrentLocale(), { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function conditionText(item: AlertConditionItem, translate: (key: string) => string): string {
  const TYPE_LABEL: Record<string, string> = {
    price: translate('conditions.price'), change_pct: translate('conditions.change_pct'), turnover: translate('conditions.turnover'), volume: translate('conditions.volume'), volume_ratio: translate('conditions.volume_ratio'),
  }
  if (item.op === 'between' && Array.isArray(item.value)) {
    return `${TYPE_LABEL[item.type] || item.type} ∈ [${item.value[0]}, ${item.value[1]}]`
  }
  return `${TYPE_LABEL[item.type] || item.type} ${item.op} ${Array.isArray(item.value) ? item.value.join('~') : item.value}`
}

export default function PriceAlertsPage() {
  const { t } = useTranslation('configuration')
  const confirmAction = useConfirm()
  const alertT = (key: string, options?: Record<string, unknown>) =>
    (t as unknown as (translationKey: string, interpolation?: Record<string, unknown>) => string)(
      `priceAlerts.${key}`,
      options,
    )
  const { toast } = useToast()
  const location = useLocation()
  const [loading, setLoading] = useState(true)
  const [rules, setRules] = useState<AlertRule[]>([])
  const [stocks, setStocks] = useState<StockItem[]>([])
  const [channels, setChannels] = useState<NotifyChannel[]>([])
  const [formOpen, setFormOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState<PriceAlertFormState>(DEFAULT_FORM)
  const [hitsOpen, setHitsOpen] = useState(false)
  const [hitRule, setHitRule] = useState<AlertRule | null>(null)
  const [hits, setHits] = useState<AlertHit[]>([])
  const [health, setHealth] = useState<MonitoringHealth | null>(null)
  const [retryingId, setRetryingId] = useState<number | null>(null)
  const [scanRunning, setScanRunning] = useState(false)
  const [prefillDone, setPrefillDone] = useState(false)

  const stockOptions = useMemo(() => stocks, [stocks])

  const load = async () => {
    setLoading(true)
    try {
      const [ruleData, stockData, channelData, healthData] = await Promise.all([
        fetchAPI<AlertRule[]>('/price-alerts'),
        stocksApi.list(),
        fetchAPI<NotifyChannel[]>('/channels'),
        fetchAPI<MonitoringHealth>('/price-alerts/health'),
      ])
      setHealth(healthData)
      setRules(ruleData || [])
      setStocks(stockData || [])
      setChannels(channelData || [])
    } catch (e) {
      toast(e instanceof Error ? e.message : alertT('messages.loadFailed'), 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  useEffect(() => {
    if (prefillDone) return
    if (loading) return
    const params = new URLSearchParams(location.search || '')
    if (!params.toString()) {
      setPrefillDone(true)
      return
    }
    const qStockId = Number(params.get('stock_id') || 0)
    const qSymbol = String(params.get('symbol') || '').trim().toUpperCase()
    const qMarket = String(params.get('market') || '').trim().toUpperCase() || 'CN'
    const qName = String(params.get('name') || '').trim()

    const openWithStock = async () => {
      let target = stocks.find(s => qStockId > 0 ? s.id === qStockId : (s.symbol === qSymbol && s.market === qMarket))
      if (!target && qSymbol) {
        try {
          target = await stocksApi.create({ symbol: qSymbol, market: qMarket, name: qName || qSymbol })
          if (target) setStocks(prev => prev.some(s => s.id === target!.id) ? prev : [target!, ...prev])
        } catch {
          // ignore and fallback to manual select
        }
      }
      setEditingId(null)
      setForm({
        ...DEFAULT_FORM,
        stock_id: target?.id || stockOptions[0]?.id || 0,
        name: target ? `${target.name} ${alertT('title')}` : (qName ? `${qName} ${alertT('title')}` : ''),
      })
      setFormOpen(true)
      setPrefillDone(true)
    }
    openWithStock()
  }, [loading, location.search, prefillDone, stockOptions, stocks])

  const retryDelivery = async (delivery: Delivery) => {
    // Keep one modal active while asking. Sibling modal layers can otherwise
    // send Escape to the history dialog instead of the confirmation.
    setHitsOpen(false)
    const confirmed = await confirmAction(alertT('health.retryConfirm'))
    setHitsOpen(true)
    if (!confirmed) return
    setRetryingId(delivery.id)
    try {
      await fetchAPI(`/price-alerts/deliveries/${delivery.id}/retry`, { method: 'POST' })
      if (hitRule) await openHits(hitRule)
      await load()
      toast(alertT('health.retryQueued'), 'success')
    } catch (error) {
      toast(error instanceof Error ? error.message : alertT('health.retryFailed'), 'error')
    } finally {
      setRetryingId(null)
    }
  }

  const openCreate = () => {
    setEditingId(null)
    setForm({
      ...DEFAULT_FORM,
      stock_id: stockOptions[0]?.id || 0,
    })
    setFormOpen(true)
  }

  const openEdit = (r: AlertRule) => {
    setEditingId(r.id)
    setForm({
      stock_id: r.stock_id,
      name: r.name || '',
      op: (r.condition_group?.op || 'and') as RuleOp,
      items: (r.condition_group?.items || [{ type: 'price', op: '>=', value: 0 }]) as AlertConditionItem[],
      market_hours_mode: (r.market_hours_mode || 'trading_only') as any,
      cooldown_minutes: r.cooldown_minutes ?? 30,
      max_triggers_per_day: r.max_triggers_per_day ?? 3,
      repeat_mode: (r.repeat_mode || 'repeat') as any,
      expire_at: r.expire_at || '',
      notify_channel_ids: r.notify_channel_ids || [],
    })
    setFormOpen(true)
  }

  const submitForm = async (payload: PriceAlertSubmitPayload) => {
    if (!payload.stock_id) {
      toast(alertT('messages.selectStock'), 'error')
      return
    }
    if (!payload.condition_group?.items?.length) {
      toast(alertT('messages.addCondition'), 'error')
      return
    }
    setSaving(true)
    try {
      if (editingId) {
        await fetchAPI(`/price-alerts/${editingId}`, { method: 'PUT', body: JSON.stringify(payload) })
      } else {
        await fetchAPI('/price-alerts', { method: 'POST', body: JSON.stringify(payload) })
      }
      setFormOpen(false)
      await load()
      toast(alertT('messages.saved'), 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : alertT('messages.saveFailed'), 'error')
    } finally {
      setSaving(false)
    }
  }

  const toggleRule = async (r: AlertRule) => {
    try {
      await fetchAPI(`/price-alerts/${r.id}/toggle`, { method: 'POST', body: JSON.stringify({ enabled: !r.enabled }) })
      await load()
    } catch (e) {
      toast(e instanceof Error ? e.message : alertT('messages.toggleFailed'), 'error')
    }
  }

  const removeRule = async (r: AlertRule) => {
    if (!(await confirmAction(alertT('messages.deleteConfirm', { name: r.name || r.stock_name }), { destructive: true }))) return
    try {
      await fetchAPI(`/price-alerts/${r.id}`, { method: 'DELETE' })
      await load()
      toast(alertT('messages.deleted'), 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : alertT('messages.deleteFailed'), 'error')
    }
  }

  const runScan = async () => {
    setScanRunning(true)
    try {
      const res = await fetchAPI<any>('/price-alerts/scan', { method: 'POST' })
      toast(alertT('messages.scanDone', { triggered: res?.triggered || 0, skipped: res?.skipped || 0 }), 'success')
      await load()
    } catch (e) {
      toast(e instanceof Error ? e.message : alertT('messages.scanFailed'), 'error')
    } finally {
      setScanRunning(false)
    }
  }

  const testRule = async (r: AlertRule) => {
    try {
      const res = await fetchAPI<any>(`/price-alerts/${r.id}/test`, { method: 'POST' })
      const st = (res?.items || [])[0]?.status || 'unknown'
      toast(alertT('messages.testDone', {
        status: alertT(`testStatuses.${st}`, { defaultValue: st }),
      }), 'info')
    } catch (e) {
      toast(e instanceof Error ? e.message : alertT('messages.testFailed'), 'error')
    }
  }

  const openHits = async (r: AlertRule) => {
    setHitRule(r)
    setHitsOpen(true)
    try {
      const data = await fetchAPI<AlertHit[]>(`/price-alerts/${r.id}/hits?limit=50`)
      setHits(data || [])
    } catch (e) {
      toast(e instanceof Error ? e.message : alertT('messages.hitsFailed'), 'error')
      setHits([])
    }
  }

  return (
    <div>
      <div className="mb-4 md:mb-8">
        <h1 className="text-[20px] md:text-[22px] font-bold text-foreground tracking-tight">{alertT('title')}</h1>
        <p className="text-[12px] md:text-[13px] text-muted-foreground mt-0.5 md:mt-1">{alertT('description')}</p>
      </div>

      <div className="card p-4 mb-4 flex items-center justify-between gap-2">
        <div className="text-[12px] text-muted-foreground">{alertT('ruleCount', { count: rules.length })}</div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" className="h-8" onClick={runScan} disabled={scanRunning}>
            {scanRunning ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
            {alertT('scan')}
          </Button>
          <Button size="sm" className="h-8" onClick={openCreate}>
            <Plus className="w-3.5 h-3.5" />
            {alertT('create')}
          </Button>
        </div>
      </div>

      <section className="card p-4 mb-4 space-y-2" aria-label={alertT('health.title')}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">{alertT('health.title')}</h2>
          <Button variant="secondary" size="sm" onClick={load} disabled={loading}>{alertT('health.refresh')}</Button>
        </div>
        {health ? <>
          <p className="text-xs text-muted-foreground">{alertT(`health.statuses.${health.scan.status}`)} · {alertT('health.lastScan', { time: fmt(health.scan.last_completed_at) })} · {alertT('health.nextScan', { time: fmt(health.scan.next_scan_at) })}</p>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs" aria-label={alertT('health.deliveryTitle')}>
            {['pending', 'sending', 'retry', 'failed', 'blocked', 'delivered'].map(status => <span key={status}>{alertT(`health.deliveryStates.${status}`)}: {health.delivery_counts[status] || 0}</span>)}
          </div>
          {health.truncated && <p className="text-xs text-muted-foreground">{alertT('health.truncated')}</p>}
        </> : <p className="text-xs text-muted-foreground">{alertT('health.unavailable')}</p>}
        <p className="text-xs text-muted-foreground">{alertT('health.boundary')}</p>
      </section>

      {loading ? (
        <div className="flex items-center justify-center py-20"><span className="w-5 h-5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" /></div>
      ) : rules.length === 0 ? (
        <div className="card p-8 text-center">
          <BellRing className="w-6 h-6 mx-auto text-muted-foreground" />
          <div className="mt-2 text-[14px] text-foreground">{alertT('emptyTitle')}</div>
          <div className="mt-1 text-[12px] text-muted-foreground">{alertT('emptyDescription')}</div>
        </div>
      ) : (
        <div className="space-y-3">
          {rules.map(r => (
            <div key={r.id} className="card p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-[14px] font-semibold">{r.name || `${r.stock_name} ${alertT('title')}`}</span>
                    <span className="text-[11px] px-2 py-0.5 rounded bg-accent/50 text-muted-foreground">{r.market}:{r.stock_symbol}</span>
                    <span className={`text-[11px] px-2 py-0.5 rounded ${r.enabled ? 'bg-emerald-500/15 text-emerald-500' : 'bg-muted text-muted-foreground'}`}>{r.enabled ? alertT('enabled') : alertT('paused')}</span>
                  </div>
                  <div className="mt-2 text-[12px] text-muted-foreground">
                    {(r.condition_group?.items || []).map(item => conditionText(item, key => alertT(key))).join(r.condition_group?.op === 'or' ? ` ${alertT('conditions.or')} ` : ` ${alertT('conditions.and')} `)}
                  </div>
                  <div className="mt-1 text-[11px] text-muted-foreground/80">
                    {alertT('cooldown', { minutes: r.cooldown_minutes })} · {alertT('dailyLimit', { count: r.max_triggers_per_day })} · {alertT('lastTrigger', { time: fmt(r.last_trigger_at) })}
                  </div>
                  {health?.items.filter(item => item.rule_id === r.id).map(item => <div key={item.rule_id} className="mt-2 text-xs space-y-1 break-words">
                    <p className={item.consecutive_failures > 0 || item.status === 'daily_limit' || item.status === 'monitoring_delayed' ? 'text-amber-600 dark:text-amber-400' : 'text-muted-foreground'}>{alertT(`health.statuses.${item.status}`)} · {alertT('health.failures', { count: item.consecutive_failures })}</p>
                    <p className="text-muted-foreground">{alertT('health.lastCheck', { time: fmt(item.last_checked_at) })} · {alertT('health.lastSuccess', { time: fmt(item.last_success_at) })}</p>
                    <p className="text-muted-foreground">{alertT('health.quota', { used: item.quota.used, limit: item.quota.unlimited ? alertT('health.unlimited') : item.quota.limit })}</p>
                    <p className="text-muted-foreground">{['pending', 'retry', 'failed', 'blocked'].map(status => `${alertT(`health.deliveryStates.${status}`)}: ${item.deliveries[status] || 0}`).join(' · ')}</p>
                  </div>)}
                </div>
                {/* Desktop: buttons on the right */}
                <div className="hidden md:flex items-center gap-1.5 shrink-0">
                  <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => testRule(r)}>{alertT('test')}</Button>
                  <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => openHits(r)} aria-label={alertT('hits.title')}><BarChart3 className="w-3.5 h-3.5" /></Button>
                  <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => openEdit(r)}>{alertT('edit')}</Button>
                  <Button variant={r.enabled ? 'destructive' : 'default'} size="sm" className="h-8 px-2.5" onClick={() => toggleRule(r)}>{r.enabled ? alertT('disable') : alertT('enable')}</Button>
                  <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => removeRule(r)}><Trash2 className="w-3.5 h-3.5" /></Button>
                </div>
              </div>
              {/* Mobile: buttons at bottom */}
              <div className="flex md:hidden items-center gap-1.5 mt-3 pt-3 border-t border-border/30">
                <Button variant="secondary" size="sm" className="h-7 px-2 text-[11px]" onClick={() => testRule(r)}>{alertT('test')}</Button>
                <Button variant="secondary" size="sm" className="h-7 px-2 text-[11px]" onClick={() => openHits(r)} aria-label={alertT('hits.title')}><BarChart3 className="w-3 h-3" /></Button>
                <Button variant="secondary" size="sm" className="h-7 px-2 text-[11px]" onClick={() => openEdit(r)}>{alertT('edit')}</Button>
                <div className="flex-1" />
                <Button variant={r.enabled ? 'destructive' : 'default'} size="sm" className="h-7 px-2 text-[11px]" onClick={() => toggleRule(r)}>{r.enabled ? alertT('disable') : alertT('enable')}</Button>
                <Button variant="secondary" size="sm" className="h-7 px-2 text-[11px]" onClick={() => removeRule(r)}><Trash2 className="w-3 h-3" /></Button>
              </div>
            </div>
          ))}
        </div>
      )}

      <PriceAlertFormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        title={editingId ? alertT('editor.editTitle') : alertT('editor.createTitle')}
        description={alertT('editor.description')}
        stocks={stockOptions}
        channels={channels}
        initial={form}
        submitting={saving}
        submitLabel={alertT('editor.submit')}
        onSubmit={submitForm}
      />

      <Dialog open={hitsOpen} onOpenChange={setHitsOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{alertT('hits.title')}</DialogTitle>
            <DialogDescription>{hitRule?.name || '--'}</DialogDescription>
          </DialogHeader>
          <div className="max-h-[60vh] overflow-y-auto scrollbar space-y-2">
            {hits.length === 0 ? (
              <div className="text-[12px] text-muted-foreground text-center py-6">{alertT('hits.empty')}</div>
            ) : hits.map(h => (
              <div key={h.id} className="rounded border border-border/40 p-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-[12px] text-muted-foreground">{fmt(h.trigger_time)}</div>
                  <div className="text-[11px] text-muted-foreground">
                    {h.deliveries?.length ? alertT('health.deliveryTitle') : (h.notify_success ? alertT('hits.notifySuccess') : alertT('health.legacyUnknown'))}
                  </div>
                </div>
                {(h.deliveries || []).map(delivery => <div key={delivery.id} className="mt-2 rounded bg-accent/20 p-2 text-xs space-y-1 break-words">
                  <p>{alertT('health.channel', { id: delivery.channel_id ?? '--' })} · {alertT(`health.deliveryStates.${delivery.status}`)} · {alertT('health.attempts', { count: delivery.attempts, max: delivery.max_attempts })}</p>
                  <p className="font-mono">{delivery.event_id}</p>
                  {delivery.error_code && <p className="text-amber-600 dark:text-amber-400">{alertT(`health.errors.${delivery.error_code}`)}</p>}
                  {delivery.next_attempt_at && <p>{alertT('health.nextRetry', { time: fmt(delivery.next_attempt_at) })}</p>}
                  {delivery.delivered_at && <p>{alertT('health.acceptedAt', { time: fmt(delivery.delivered_at) })}</p>}
                  {['retry', 'failed', 'blocked'].includes(delivery.status) && delivery.channel_id !== null && <Button variant="secondary" size="sm" disabled={retryingId !== null} onClick={() => retryDelivery(delivery)}>{alertT('health.retry')}</Button>}
                </div>)}
                <div className="mt-2 text-[11px] bg-accent/20 rounded p-2 font-mono overflow-x-auto scrollbar">
                  {JSON.stringify(h.trigger_snapshot || {}, null, 2)}
                </div>
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
