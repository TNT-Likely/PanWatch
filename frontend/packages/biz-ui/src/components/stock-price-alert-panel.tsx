import { useRequestScope } from './use-request-scope'
import { useConfirm } from '@panwatch/base-ui/components/ui/confirm-dialog'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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

interface StockPriceAlertPanelProps {
  symbol: string
  market: string
  stockName?: string
  stockId?: number
  mode?: 'icon' | 'inline'
  initialTotal?: number
  initialEnabled?: number
  onChanged?: () => void
}

export default function StockPriceAlertPanel(props: StockPriceAlertPanelProps) {
  return <StockPriceAlertSession key={JSON.stringify([props.market, props.symbol, props.stockId])} {...props} />
}

function StockPriceAlertSession(props: StockPriceAlertPanelProps) {
  const beginRequest = useRequestScope()
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

  const dialogEpoch = useRef(0)
  const formEpoch = useRef(0)
  const beginDialogRequest = useCallback((lane: string, forForm = false) => {
    const isCurrent = beginRequest(lane)
    const dialog = dialogEpoch.current
    const form = formEpoch.current
    return () => isCurrent() && dialogEpoch.current === dialog && (!forForm || formEpoch.current === form)
  }, [beginRequest])

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) {
      dialogEpoch.current += 1
      formEpoch.current += 1
      setFormOpen(false)
      setLoading(false)
      setSaving(false)
    }
    setOpen(nextOpen)
  }
  const handleFormOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) {
      formEpoch.current += 1
      setSaving(false)
    }
    setFormOpen(nextOpen)
  }

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

  const ensureStockId = useCallback(async (isCurrent: () => boolean): Promise<number | null> => {
    if (props.stockId) return props.stockId
    let target = stocks.find(s => s.symbol === symbol && s.market === market)
    if (!target) {
      const all = await stocksApi.list()
      if (!isCurrent()) return null
      setStocks(all || [])
      target = (all || []).find(s => s.symbol === symbol && s.market === market)
    }
    if (!target) {
      const created = await stocksApi.create({ symbol, market, name: props.stockName || symbol })
      if (!isCurrent()) return null
      setStocks(prev => prev.some(s => s.id === created.id) ? prev : [created, ...prev])
      return created.id
    }
    return target.id
  }, [market, props.stockId, props.stockName, stocks, symbol])

  const load = useCallback(async () => {
    if (!symbol) return
    const isCurrent = beginDialogRequest('rules')
    if (!isCurrent()) return
    setLoading(true)
    try {
      const [ruleData, stockData, channelData] = await Promise.all([
        fetchAPI<AlertRule[]>('/price-alerts'),
        stocksApi.list(),
        fetchAPI<NotifyChannel[]>('/channels'),
      ])
      if (!isCurrent()) return
      setStocks(stockData || [])
      setChannels(channelData || [])
      const filtered = (ruleData || []).filter(r =>
        String(r.stock_symbol || '').toUpperCase() === symbol.toUpperCase() &&
        String(r.market || '').toUpperCase() === market
      )
      setRules(filtered)
    } catch (e) {
      if (isCurrent()) toast(tr('messages.loadFailed'), 'error')
    } finally {
      if (isCurrent()) setLoading(false)
    }
  }, [market, symbol, toast, beginDialogRequest])

  const loadSummaryOnly = useCallback(async () => {
    if (!symbol) return
    const isCurrent = beginRequest('rules')
    if (!isCurrent()) return
    try {
      const ruleData = await fetchAPI<AlertRule[]>('/price-alerts')
      if (!isCurrent()) return
      const filtered = (ruleData || []).filter(r =>
        String(r.stock_symbol || '').toUpperCase() === symbol.toUpperCase() &&
        String(r.market || '').toUpperCase() === market
      )
      setRules(filtered)
    } catch {
      // summary preload failure should not block interaction
    }
  }, [market, symbol, beginRequest])

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
    const isCurrent = beginDialogRequest('create')
    if (!isCurrent()) return
    try {
      const stockId = await ensureStockId(isCurrent)
      if (!isCurrent()) return
      if (!stockId) {
        toast(tr('messages.stockNotFound'), 'error')
        return
      }
      formEpoch.current += 1
      setSaving(false)
      setEditingId(null)
      setForm({
        ...DEFAULT_FORM,
        stock_id: stockId,
        name: props.stockName ? tr('defaultName', { name: props.stockName }) : '',
      })
      setFormOpen(true)
    } catch (e) {
      if (isCurrent()) toast(tr('messages.createFailed'), 'error')
    }
  }

  const openEdit = (r: AlertRule) => {
    beginRequest('create')
    formEpoch.current += 1
    setSaving(false)
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
      expire_at: r.expire_at || '',
      notify_channel_ids: r.notify_channel_ids || [],
    })
    setFormOpen(true)
  }

  const submitForm = async (payload: PriceAlertSubmitPayload) => {
    const isCurrent = beginDialogRequest('save', true)
    if (!isCurrent()) return
    setSaving(true)
    try {
      if (editingId) {
        await fetchAPI(`/price-alerts/${editingId}`, { method: 'PUT', body: JSON.stringify(payload) })
      } else {
        await fetchAPI('/price-alerts', { method: 'POST', body: JSON.stringify(payload) })
      }
      // The mutation succeeded even if its view closed; invalidate the parent
      // summary independently of this dialog's local state and feedback.
      props.onChanged?.()
      if (!isCurrent()) return
      setFormOpen(false)
      await load()
      if (!isCurrent()) return
      toast(tr('messages.saved'), 'success')
    } catch (e) {
      if (isCurrent()) toast(tr('messages.saveFailed'), 'error')
    } finally {
      if (isCurrent()) setSaving(false)
    }
  }

  const toggleRule = async (r: AlertRule) => {
    const isCurrent = beginDialogRequest(`rule:${r.id}`)
    if (!isCurrent()) return
    try {
      await fetchAPI(`/price-alerts/${r.id}/toggle`, { method: 'POST', body: JSON.stringify({ enabled: !r.enabled }) })
      props.onChanged?.()
      if (!isCurrent()) return
      await load()
      if (!isCurrent()) return
    } catch (e) {
      if (isCurrent()) toast(tr('messages.toggleFailed'), 'error')
    }
  }

  const removeRule = async (r: AlertRule) => {
    const isCurrent = beginDialogRequest(`rule:${r.id}`)
    if (!isCurrent()) return
    if (!(await confirmAction(tr('messages.deleteConfirm', { name: r.name || tr('alert') }), { destructive: true }))) return
    if (!isCurrent()) return
    try {
      await fetchAPI(`/price-alerts/${r.id}`, { method: 'DELETE' })
      props.onChanged?.()
      if (!isCurrent()) return
      await load()
      if (!isCurrent()) return
      toast(tr('messages.deleted'), 'success')
    } catch (e) {
      if (isCurrent()) toast(tr('messages.deleteFailed'), 'error')
    }
  }

  const trigger = mode === 'inline' ? (
    <Button
      variant="secondary"
      size="sm"
      className="h-8 px-2.5"
      onClick={() => handleOpenChange(true)}
      type="button"
      title={shownSummary.total > 0 ? `${tr('alert')} ${shownSummary.enabled}/${shownSummary.total}` : tr('priceAlert')}
    >
      <Bell className="w-3.5 h-3.5" />
      {tr('alert')} {shownSummary.total > 0 ? `${shownSummary.enabled}/${shownSummary.total}` : '0'}
    </Button>
  ) : (
    <button
      className="relative inline-flex items-center justify-center h-7 w-7 rounded-md hover:bg-accent/40 transition-colors"
      onClick={() => handleOpenChange(true)}
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
      <Dialog open={open} onOpenChange={handleOpenChange}>
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
                        <Button variant="secondary" size="sm" className="h-7 px-2" aria-label={tr('editTitle')} onClick={() => openEdit(r)}><Pencil className="w-3 h-3" /></Button>
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
        onOpenChange={handleFormOpenChange}
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
