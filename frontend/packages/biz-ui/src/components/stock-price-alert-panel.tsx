import { useConfirm } from '@panwatch/base-ui/components/ui/confirm-dialog'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Bell, Plus, Pencil, Trash2 } from 'lucide-react'
import { fetchAPI, stocksApi, type NotifyChannel } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import PriceAlertFormDialog, { type AlertConditionItem, type PriceAlertFormState, type PriceAlertSubmitPayload } from '@panwatch/biz-ui/components/price-alert-form-dialog'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { useTranslation } from 'react-i18next'

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
    op: 'and' | 'or'
    items: AlertConditionItem[]
  }
  market_hours_mode: 'always' | 'trading_only'
  cooldown_minutes: number
  max_triggers_per_day: number
  repeat_mode: 'once' | 'repeat'
  expire_at: string | null
  notify_channel_ids: number[]
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

function conditionText(item: AlertConditionItem, tr: (key: string) => string): string {
  const label: Record<string, string> = {
    price: tr('conditions.price'),
    change_pct: tr('conditions.change_pct'),
    turnover: tr('conditions.turnover'),
    volume: tr('conditions.volume'),
    volume_ratio: tr('conditions.volume_ratio'),
  }
  if (item.op === 'between' && Array.isArray(item.value)) {
    return `${label[item.type] || item.type} ∈ [${item.value[0]}, ${item.value[1]}]`
  }
  return `${label[item.type] || item.type} ${item.op} ${Array.isArray(item.value) ? item.value.join('~') : item.value}`
}

export default function StockPriceAlertPanel(props: {
  symbol: string
  market: string
  stockName?: string
  stockId?: number
  mode?: 'icon' | 'inline'
  initialTotal?: number
  initialEnabled?: number
  onChanged?: () => void
}) {
  const { toast } = useToast()
  const { t } = useTranslation('bizUi')
  const confirmAction = useConfirm()
  const tr = (key: string, options?: Record<string, unknown>) =>
    (t as unknown as (key: string, options?: Record<string, unknown>) => string)(`stockPriceAlert.${key}`, options)
  const symbol = String(props.symbol || '').trim()
  const market = String(props.market || 'CN').trim().toUpperCase()
  const mode = props.mode || 'icon'

  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [rules, setRules] = useState<AlertRule[]>([])
  const [stocks, setStocks] = useState<StockItem[]>([])
  const [channels, setChannels] = useState<NotifyChannel[]>([])
  const [formOpen, setFormOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState<PriceAlertFormState>(DEFAULT_FORM)

  const summary = useMemo(() => {
    const total = rules.length
    const enabled = rules.filter(r => r.enabled).length
    return { total, enabled }
  }, [rules])
  const shownSummary = useMemo(() => {
    if (summary.total > 0 || open) return summary
    return {
      total: Number(props.initialTotal || 0),
      enabled: Number(props.initialEnabled || 0),
    }
  }, [open, props.initialEnabled, props.initialTotal, summary])
  const formStocks = useMemo(() => {
    return stocks
  }, [stocks])

  const ensureStockId = useCallback(async (): Promise<number | null> => {
    if (props.stockId) return props.stockId
    let target = stocks.find(s => s.symbol === symbol && s.market === market)
    if (!target) {
      const all = await stocksApi.list()
      setStocks(all || [])
      target = (all || []).find(s => s.symbol === symbol && s.market === market)
    }
    if (!target) {
      const created = await stocksApi.create({ symbol, market, name: props.stockName || symbol })
      setStocks(prev => prev.some(s => s.id === created.id) ? prev : [created, ...prev])
      return created.id
    }
    return target.id
  }, [market, props.stockId, props.stockName, stocks, symbol])

  const load = useCallback(async () => {
    if (!symbol) return
    setLoading(true)
    try {
      const [ruleData, stockData, channelData] = await Promise.all([
        fetchAPI<AlertRule[]>('/price-alerts'),
        stocksApi.list(),
        fetchAPI<NotifyChannel[]>('/channels'),
      ])
      setStocks(stockData || [])
      setChannels(channelData || [])
      const filtered = (ruleData || []).filter(r =>
        String(r.stock_symbol || '').toUpperCase() === symbol.toUpperCase() &&
        String(r.market || '').toUpperCase() === market
      )
      setRules(filtered)
    } catch (e) {
      toast(tr('messages.loadFailed'), 'error')
    } finally {
      setLoading(false)
    }
  }, [market, symbol, toast])

  const loadSummaryOnly = useCallback(async () => {
    if (!symbol) return
    try {
      const ruleData = await fetchAPI<AlertRule[]>('/price-alerts')
      const filtered = (ruleData || []).filter(r =>
        String(r.stock_symbol || '').toUpperCase() === symbol.toUpperCase() &&
        String(r.market || '').toUpperCase() === market
      )
      setRules(filtered)
    } catch {
      // summary preload failure should not block interaction
    }
  }, [market, symbol])

  useEffect(() => {
    if (!open) return
    load()
  }, [open, load])

  useEffect(() => {
    if (open) return
    if (mode !== 'inline') return
    if ((props.initialTotal ?? null) !== null) return
    loadSummaryOnly()
  }, [loadSummaryOnly, mode, open, props.initialTotal])

  const openCreate = async () => {
    try {
      const stockId = await ensureStockId()
      if (!stockId) {
        toast(tr('messages.stockNotFound'), 'error')
        return
      }
      setEditingId(null)
      setForm({
        ...DEFAULT_FORM,
        stock_id: stockId,
        name: props.stockName ? tr('defaultName', { name: props.stockName }) : '',
      })
      setFormOpen(true)
    } catch (e) {
      toast(tr('messages.createFailed'), 'error')
    }
  }

  const openEdit = (r: AlertRule) => {
    setEditingId(r.id)
    setForm({
      stock_id: r.stock_id,
      name: r.name || '',
      op: r.condition_group?.op || 'and',
      items: (r.condition_group?.items || [{ type: 'price', op: '>=', value: 0 }]) as AlertConditionItem[],
      market_hours_mode: r.market_hours_mode || 'trading_only',
      cooldown_minutes: r.cooldown_minutes ?? 30,
      max_triggers_per_day: r.max_triggers_per_day ?? 3,
      repeat_mode: r.repeat_mode || 'repeat',
      expire_at: r.expire_at ? r.expire_at.slice(0, 16) : '',
      notify_channel_ids: r.notify_channel_ids || [],
    })
    setFormOpen(true)
  }

  const submitForm = async (payload: PriceAlertSubmitPayload) => {
    setSaving(true)
    try {
      if (editingId) {
        await fetchAPI(`/price-alerts/${editingId}`, { method: 'PUT', body: JSON.stringify(payload) })
      } else {
        await fetchAPI('/price-alerts', { method: 'POST', body: JSON.stringify(payload) })
      }
      setFormOpen(false)
      await load()
      props.onChanged?.()
      toast(tr('messages.saved'), 'success')
    } catch (e) {
      toast(tr('messages.saveFailed'), 'error')
    } finally {
      setSaving(false)
    }
  }

  const toggleRule = async (r: AlertRule) => {
    try {
      await fetchAPI(`/price-alerts/${r.id}/toggle`, { method: 'POST', body: JSON.stringify({ enabled: !r.enabled }) })
      await load()
      props.onChanged?.()
    } catch (e) {
      toast(tr('messages.toggleFailed'), 'error')
    }
  }

  const removeRule = async (r: AlertRule) => {
    if (!(await confirmAction(tr('messages.deleteConfirm', { name: r.name || tr('alert') }), { destructive: true }))) return
    try {
      await fetchAPI(`/price-alerts/${r.id}`, { method: 'DELETE' })
      await load()
      props.onChanged?.()
      toast(tr('messages.deleted'), 'success')
    } catch (e) {
      toast(tr('messages.deleteFailed'), 'error')
    }
  }

  const trigger = mode === 'inline' ? (
    <Button
      variant="secondary"
      size="sm"
      className="h-8 px-2.5"
      onClick={() => setOpen(true)}
      type="button"
      title={shownSummary.total > 0 ? `${tr('alert')} ${shownSummary.enabled}/${shownSummary.total}` : tr('priceAlert')}
    >
      <Bell className="w-3.5 h-3.5" />
      {tr('alert')} {shownSummary.total > 0 ? `${shownSummary.enabled}/${shownSummary.total}` : '0'}
    </Button>
  ) : (
    <button
      className="relative inline-flex items-center justify-center h-7 w-7 rounded-md hover:bg-accent/40 transition-colors"
      onClick={() => setOpen(true)}
      title={shownSummary.total > 0 ? `${tr('alert')} ${shownSummary.enabled}/${shownSummary.total}` : tr('priceAlert')}
      type="button"
    >
      <Bell className="w-3.5 h-3.5" />
      {shownSummary.total > 0 && (
        <span className="absolute -top-1 -right-1 min-w-[14px] h-[14px] px-1 rounded-full bg-primary text-primary-foreground text-[9px] leading-[14px] text-center">
          {shownSummary.enabled}
        </span>
      )}
    </button>
  )

  return (
    <>
      {trigger}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{tr('title', { name: props.stockName || symbol })}</DialogTitle>
            <DialogDescription>{tr('summary', { market, enabled: summary.enabled, total: summary.total })}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="flex justify-end">
              <Button size="sm" className="h-8" onClick={() => openCreate()}>
                <Plus className="w-3.5 h-3.5" />
                {tr('create')}
              </Button>
            </div>
            {loading ? (
              <div className="text-[12px] text-muted-foreground py-6 text-center">{tr('loading')}</div>
            ) : rules.length === 0 ? (
              <div className="text-[12px] text-muted-foreground py-6 text-center">{tr('empty')}</div>
            ) : (
              <div className="space-y-2 max-h-[48vh] overflow-y-auto scrollbar">
                {rules.map(r => (
                  <div key={r.id} className="rounded-lg border border-border/40 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-[13px] font-medium truncate">{r.name || tr('fallbackName', { name: props.stockName || symbol })}</span>
                          <span className={`text-[10px] px-1.5 py-0.5 rounded ${r.enabled ? 'bg-emerald-500/15 text-emerald-500' : 'bg-muted text-muted-foreground'}`}>
                            {r.enabled ? tr('enabled') : tr('paused')}
                          </span>
                        </div>
                        <div className="mt-1 text-[11px] text-muted-foreground">
                          {(r.condition_group?.items || []).map(item => conditionText(item, tr)).join(r.condition_group?.op === 'or' ? tr('or') : tr('and'))}
                        </div>
                      </div>
                      <div className="flex items-center gap-1">
                        <Button variant="secondary" size="sm" className="h-7 px-2" onClick={() => openEdit(r)}><Pencil className="w-3 h-3" /></Button>
                        <Button variant={r.enabled ? 'destructive' : 'default'} size="sm" className="h-7 px-2.5" onClick={() => toggleRule(r)}>{r.enabled ? tr('disable') : tr('enable')}</Button>
                        <Button variant="secondary" size="sm" className="h-7 px-2" onClick={() => removeRule(r)}><Trash2 className="w-3 h-3" /></Button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>

      <PriceAlertFormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        title={editingId ? tr('editTitle') : tr('createTitle')}
        description={tr('formDescription')}
        stocks={formStocks}
        channels={channels}
        initial={form}
        submitting={saving}
        submitLabel={tr('saveRule')}
        onSubmit={submitForm}
      />
    </>
  )
}
