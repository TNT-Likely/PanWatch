import { useCallback, useEffect, useMemo, useState } from 'react'
import { Bell, Plus, Pencil, Trash2 } from 'lucide-react'
import { fetchAPI, type NotifyChannel } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import PriceAlertFormDialog, { type AlertConditionItem, type PriceAlertFormState, type PriceAlertSubmitPayload } from '@/components/price-alert-form-dialog'
import { useToast } from '@/components/ui/toast'

interface StockItem {
  id: number
  symbol: string
  name: string
  market: string
  enabled: boolean
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

function conditionText(item: AlertConditionItem): string {
  const label: Record<string, string> = {
    price: '价格',
    change_pct: '涨跌幅%',
    turnover: '成交额',
    volume: '成交量',
    volume_ratio: '量比',
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
    const selectedId = Number(form.stock_id || 0)
    const current = stocks.find(s => (
      String(s.symbol || '').toUpperCase() === symbol.toUpperCase() &&
      String(s.market || '').toUpperCase() === market
    ))
    const keepIds = new Set<number>([
      selectedId,
      Number(props.stockId || 0),
      Number(current?.id || 0),
    ].filter(Boolean))
    return stocks.filter(s => s.enabled || keepIds.has(s.id))
  }, [form.stock_id, market, props.stockId, stocks, symbol])

  const ensureStockId = useCallback(async (): Promise<number | null> => {
    if (props.stockId) return props.stockId
    let target = stocks.find(s => s.symbol === symbol && s.market === market)
    if (!target) {
      const all = await fetchAPI<StockItem[]>('/stocks')
      setStocks(all || [])
      target = (all || []).find(s => s.symbol === symbol && s.market === market)
    }
    if (!target) {
      const created = await fetchAPI<StockItem>('/stocks', {
        method: 'POST',
        body: JSON.stringify({ symbol, market, name: props.stockName || symbol }),
      })
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
        fetchAPI<StockItem[]>('/stocks'),
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
      toast(e instanceof Error ? e.message : '加载提醒失败', 'error')
    } finally {
      setLoading(false)
    }
  }, [market, symbol, toast])

  useEffect(() => {
    if (!open) return
    load()
  }, [open, load])

  const openCreate = async () => {
    const stockId = await ensureStockId()
    if (!stockId) {
      toast('无法定位股票', 'error')
      return
    }
    setEditingId(null)
    setForm({
      ...DEFAULT_FORM,
      stock_id: stockId,
      name: props.stockName ? `${props.stockName} 价格提醒` : '',
    })
    setFormOpen(true)
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
      toast('提醒已保存', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '保存失败', 'error')
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
      toast(e instanceof Error ? e.message : '切换失败', 'error')
    }
  }

  const removeRule = async (r: AlertRule) => {
    if (!window.confirm(`确认删除规则「${r.name || '提醒'}」？`)) return
    try {
      await fetchAPI(`/price-alerts/${r.id}`, { method: 'DELETE' })
      await load()
      props.onChanged?.()
      toast('已删除', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '删除失败', 'error')
    }
  }

  const trigger = mode === 'inline' ? (
    <button
      className="inline-flex items-center gap-1.5 text-[11px] px-2 py-1 rounded border border-border/50 hover:border-primary/30 hover:text-primary transition-colors"
      onClick={() => setOpen(true)}
      type="button"
    >
      <Bell className="w-3.5 h-3.5" />
      提醒 {shownSummary.total > 0 ? `${shownSummary.enabled}/${shownSummary.total}` : '0'}
    </button>
  ) : (
    <button
      className="relative inline-flex items-center justify-center h-7 w-7 rounded-md hover:bg-accent/40 transition-colors"
      onClick={() => setOpen(true)}
      title={shownSummary.total > 0 ? `提醒 ${shownSummary.enabled}/${shownSummary.total}` : '价格提醒'}
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
            <DialogTitle>{props.stockName || symbol} 提醒</DialogTitle>
            <DialogDescription>{market} · 启用 {summary.enabled} / 共 {summary.total}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="flex justify-end">
              <Button size="sm" className="h-8" onClick={() => openCreate()}>
                <Plus className="w-3.5 h-3.5" />
                新建提醒
              </Button>
            </div>
            {loading ? (
              <div className="text-[12px] text-muted-foreground py-6 text-center">加载中...</div>
            ) : rules.length === 0 ? (
              <div className="text-[12px] text-muted-foreground py-6 text-center">该股票暂无提醒规则</div>
            ) : (
              <div className="space-y-2 max-h-[48vh] overflow-y-auto scrollbar">
                {rules.map(r => (
                  <div key={r.id} className="rounded-lg border border-border/40 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-[13px] font-medium truncate">{r.name || `${props.stockName || symbol} 提醒`}</span>
                          <span className={`text-[10px] px-1.5 py-0.5 rounded ${r.enabled ? 'bg-emerald-500/15 text-emerald-500' : 'bg-muted text-muted-foreground'}`}>
                            {r.enabled ? '启用' : '暂停'}
                          </span>
                        </div>
                        <div className="mt-1 text-[11px] text-muted-foreground">
                          {(r.condition_group?.items || []).map(conditionText).join(r.condition_group?.op === 'or' ? ' 或 ' : ' 且 ')}
                        </div>
                      </div>
                      <div className="flex items-center gap-1">
                        <Button variant="secondary" size="sm" className="h-7 px-2" onClick={() => openEdit(r)}><Pencil className="w-3 h-3" /></Button>
                        <Button variant={r.enabled ? 'destructive' : 'default'} size="sm" className="h-7 px-2.5" onClick={() => toggleRule(r)}>{r.enabled ? '停用' : '启用'}</Button>
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
        title={editingId ? '编辑提醒规则' : '新建提醒规则'}
        description="支持价格、涨跌幅、成交额、量比条件，支持 AND / OR 组合"
        stocks={formStocks}
        channels={channels}
        initial={form}
        submitting={saving}
        submitLabel="保存规则"
        onSubmit={submitForm}
      />
    </>
  )
}
