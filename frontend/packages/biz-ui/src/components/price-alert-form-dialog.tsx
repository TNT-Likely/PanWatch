import { useEffect, useMemo, useState } from 'react'
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { Button } from '@panwatch/base-ui/components/ui/button'

export type RuleOp = 'and' | 'or'
export type ConditionType = 'price' | 'change_pct' | 'turnover' | 'volume' | 'volume_ratio'
export type ConditionOp = '>=' | '<=' | '>' | '<' | '==' | 'between'

export interface AlertConditionItem {
  type: ConditionType
  op: ConditionOp
  value: number | [number, number]
}

export interface PriceAlertFormState {
  stock_id: number
  name: string
  op: RuleOp
  items: AlertConditionItem[]
  market_hours_mode: 'always' | 'trading_only'
  cooldown_minutes: number
  max_triggers_per_day: number
  repeat_mode: 'once' | 'repeat'
  expire_at: string
  notify_channel_ids: number[]
}

export interface PriceAlertSubmitPayload {
  stock_id: number
  name: string
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
}

interface StockOption {
  id: number
  symbol: string
  name: string
  market: string
  enabled?: boolean
}

interface ChannelOption {
  id: number
  name: string
  enabled: boolean
  is_default?: boolean
}

const TYPE_LABEL: Record<ConditionType, string> = {
  price: '价格',
  change_pct: '涨跌幅%',
  turnover: '成交额',
  volume: '成交量',
  volume_ratio: '量比',
}

const buildDefaultForm = (stockId = 0): PriceAlertFormState => ({
  stock_id: stockId,
  name: '',
  op: 'and',
  items: [{ type: 'price', op: '>=', value: 0 }],
  market_hours_mode: 'trading_only',
  cooldown_minutes: 30,
  max_triggers_per_day: 3,
  repeat_mode: 'repeat',
  expire_at: '',
  notify_channel_ids: [],
})

export default function PriceAlertFormDialog(props: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  stocks: StockOption[]
  channels?: ChannelOption[]
  initial?: Partial<PriceAlertFormState>
  submitting?: boolean
  submitLabel?: string
  onSubmit: (payload: PriceAlertSubmitPayload) => Promise<void> | void
}) {
  const stockOptions = useMemo(() => props.stocks, [props.stocks])
  const [form, setForm] = useState<PriceAlertFormState>(buildDefaultForm())

  useEffect(() => {
    if (!props.open) return
    const fallbackStockId = stockOptions[0]?.id || 0
    const preferredStockId = Number(props.initial?.stock_id || 0)
    const hasPreferred = preferredStockId > 0 && stockOptions.some(s => s.id === preferredStockId)
    const merged: PriceAlertFormState = {
      ...buildDefaultForm(fallbackStockId),
      ...props.initial,
      stock_id: hasPreferred ? preferredStockId : (props.initial?.stock_id || fallbackStockId),
      items: props.initial?.items?.length ? props.initial.items : [{ type: 'price', op: '>=', value: 0 }],
      notify_channel_ids: props.initial?.notify_channel_ids || [],
    }
    setForm(merged)
    setCalendarOpen(false)
  }, [props.open, props.initial, stockOptions])

  const updateCond = (idx: number, patch: Partial<AlertConditionItem>) => {
    setForm(prev => {
      const next = [...prev.items]
      next[idx] = { ...next[idx], ...patch }
      return { ...prev, items: next }
    })
  }

  const addCond = () => {
    setForm(prev => ({ ...prev, items: [...prev.items, { type: 'price', op: '>=', value: 0 }] }))
  }

  const removeCond = (idx: number) => {
    setForm(prev => {
      const next = prev.items.filter((_, i) => i !== idx)
      return { ...prev, items: next.length ? next : [{ type: 'price', op: '>=', value: 0 }] }
    })
  }

  const submit = async () => {
    if (!form.stock_id) return
    if (!form.items.length) return
    await props.onSubmit({
      stock_id: form.stock_id,
      name: form.name.trim(),
      condition_group: { op: form.op, items: form.items },
      market_hours_mode: form.market_hours_mode,
      cooldown_minutes: Number(form.cooldown_minutes || 0),
      max_triggers_per_day: Number(form.max_triggers_per_day || 0),
      repeat_mode: form.repeat_mode,
      expire_at: form.expire_at ? new Date(form.expire_at).toISOString() : null,
      notify_channel_ids: form.notify_channel_ids || [],
    })
  }

  const enabledChannels = (props.channels || []).filter(c => c.enabled)
  const [calendarOpen, setCalendarOpen] = useState(false)
  const [calendarMonth, setCalendarMonth] = useState<Date>(() => new Date())
  const expireDatePart = useMemo(() => {
    if (!form.expire_at) return ''
    const s = String(form.expire_at)
    if (s.includes('T')) return s.slice(0, 10)
    if (s.includes(' ')) return s.slice(0, 10)
    return ''
  }, [form.expire_at])
  const expireTimePart = useMemo(() => {
    if (!form.expire_at) return ''
    const s = String(form.expire_at)
    const idx = s.includes('T') ? s.indexOf('T') : s.indexOf(' ')
    if (idx < 0) return ''
    return s.slice(idx + 1, idx + 6)
  }, [form.expire_at])
  const updateExpirePart = (datePart: string, timePart: string) => {
    const d = (datePart || '').trim()
    const t = (timePart || '').trim()
    if (!d) {
      setForm(prev => ({ ...prev, expire_at: '' }))
      return
    }
    const safeTime = t || '00:00'
    setForm(prev => ({ ...prev, expire_at: `${d}T${safeTime}` }))
  }
  const selectedDate = expireDatePart
  const monthLabel = useMemo(() => {
    const y = calendarMonth.getFullYear()
    const m = String(calendarMonth.getMonth() + 1).padStart(2, '0')
    return `${y}-${m}`
  }, [calendarMonth])
  const daysInMonth = useMemo(() => {
    const y = calendarMonth.getFullYear()
    const m = calendarMonth.getMonth()
    const first = new Date(y, m, 1)
    const firstWeekday = first.getDay()
    const lastDate = new Date(y, m + 1, 0).getDate()
    const cells: Array<{ day: number; date: string; inMonth: boolean }> = []
    for (let i = 0; i < firstWeekday; i++) cells.push({ day: 0, date: '', inMonth: false })
    for (let d = 1; d <= lastDate; d++) {
      const ds = `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`
      cells.push({ day: d, date: ds, inMonth: true })
    }
    while (cells.length % 7 !== 0) cells.push({ day: 0, date: '', inMonth: false })
    return cells
  }, [calendarMonth])
  const setSelectedDate = (date: string) => {
    updateExpirePart(date, expireTimePart || '00:00')
    setCalendarOpen(false)
  }
  const toggleChannel = (id: number) => {
    setForm(prev => {
      const exists = prev.notify_channel_ids.includes(id)
      return {
        ...prev,
        notify_channel_ids: exists
          ? prev.notify_channel_ids.filter(x => x !== id)
          : [...prev.notify_channel_ids, id],
      }
    })
  }

  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{props.title}</DialogTitle>
          <DialogDescription>{props.description}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <div className="text-[12px] text-muted-foreground mb-1">股票</div>
              <Select value={String(form.stock_id || '')} onValueChange={(v) => setForm(prev => ({ ...prev, stock_id: Number(v) }))}>
                <SelectTrigger><SelectValue placeholder="选择股票" /></SelectTrigger>
                <SelectContent>
                  {stockOptions.map(s => (
                    <SelectItem key={s.id} value={String(s.id)}>{s.name} ({s.symbol})</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <div className="text-[12px] text-muted-foreground mb-1">规则名称</div>
              <Input value={form.name} onChange={e => setForm(prev => ({ ...prev, name: e.target.value }))} placeholder="例如：突破120提醒" />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <div>
              <div className="text-[12px] text-muted-foreground mb-1">条件关系</div>
              <Select value={form.op} onValueChange={(v) => setForm(prev => ({ ...prev, op: v as RuleOp }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="and">AND（且）</SelectItem>
                  <SelectItem value="or">OR（或）</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <div className="text-[12px] text-muted-foreground mb-1">生效时段</div>
              <Select value={form.market_hours_mode} onValueChange={(v) => setForm(prev => ({ ...prev, market_hours_mode: v as any }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="trading_only">仅交易时段</SelectItem>
                  <SelectItem value="always">全天</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <div className="text-[12px] text-muted-foreground mb-1">冷却(分钟)</div>
              <Input type="number" value={String(form.cooldown_minutes)} onChange={e => setForm(prev => ({ ...prev, cooldown_minutes: Number(e.target.value || 0) }))} />
            </div>
            <div>
              <div className="text-[12px] text-muted-foreground mb-1">日上限</div>
              <Input type="number" value={String(form.max_triggers_per_day)} onChange={e => setForm(prev => ({ ...prev, max_triggers_per_day: Number(e.target.value || 0) }))} />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <div className="text-[12px] text-muted-foreground mb-1">触发模式</div>
              <Select value={form.repeat_mode} onValueChange={(v) => setForm(prev => ({ ...prev, repeat_mode: v as any }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="repeat">可重复触发</SelectItem>
                  <SelectItem value="once">仅触发一次</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <div className="text-[12px] text-muted-foreground mb-1">到期时间（可选）</div>
              <div className="grid grid-cols-2 gap-2">
                <div className="relative">
                  <Button
                    type="button"
                    variant="secondary"
                    className="w-full justify-start h-10 text-[12px]"
                    onClick={() => setCalendarOpen(v => !v)}
                  >
                    <CalendarDays className="w-3.5 h-3.5 mr-1.5" />
                    {selectedDate || '选择日期'}
                  </Button>
                  {calendarOpen && (
                    <div className="absolute z-50 mt-1 w-[260px] rounded-xl border border-border/60 bg-card shadow-xl p-2">
                      <div className="flex items-center justify-between mb-2">
                        <Button type="button" variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={() => setCalendarMonth(prev => new Date(prev.getFullYear(), prev.getMonth() - 1, 1))}>
                          <ChevronLeft className="w-3.5 h-3.5" />
                        </Button>
                        <div className="text-[12px] font-medium">{monthLabel}</div>
                        <Button type="button" variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={() => setCalendarMonth(prev => new Date(prev.getFullYear(), prev.getMonth() + 1, 1))}>
                          <ChevronRight className="w-3.5 h-3.5" />
                        </Button>
                      </div>
                      <div className="grid grid-cols-7 gap-1 text-center text-[10px] text-muted-foreground mb-1">
                        {['日', '一', '二', '三', '四', '五', '六'].map(w => <div key={w}>{w}</div>)}
                      </div>
                      <div className="grid grid-cols-7 gap-1">
                        {daysInMonth.map((c, i) => (
                          <button
                            key={`${c.date || 'empty'}-${i}`}
                            type="button"
                            disabled={!c.inMonth}
                            onClick={() => c.inMonth && setSelectedDate(c.date)}
                            className={`h-7 rounded text-[11px] ${
                              !c.inMonth
                                ? 'opacity-0 cursor-default'
                                : selectedDate === c.date
                                  ? 'bg-primary text-primary-foreground'
                                  : 'hover:bg-accent text-foreground'
                            }`}
                          >
                            {c.day || ''}
                          </button>
                        ))}
                      </div>
                      <div className="mt-2 flex justify-between">
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-7 text-[11px]"
                          onClick={() => {
                            updateExpirePart('', '')
                            setCalendarOpen(false)
                          }}
                        >
                          清空
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-7 text-[11px]"
                          onClick={() => {
                            const now = new Date()
                            const ds = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
                            setSelectedDate(ds)
                          }}
                        >
                          今天
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
                <Input
                  type="text"
                  inputMode="numeric"
                  placeholder="HH:mm"
                  value={expireTimePart}
                  onChange={e => updateExpirePart(expireDatePart, e.target.value)}
                />
              </div>
              <div className="mt-1 text-[10px] text-muted-foreground/70">留空表示永不过期</div>
            </div>
          </div>

          <div className="rounded-lg border border-border/40 p-3">
            <div className="text-[12px] text-muted-foreground mb-2">通知渠道（不选=系统默认）</div>
            {enabledChannels.length === 0 ? (
              <div className="text-[12px] text-muted-foreground/70">暂无可用渠道</div>
            ) : (
              <div className="flex items-center gap-2 flex-wrap">
                {enabledChannels.map(ch => {
                  const active = form.notify_channel_ids.includes(ch.id)
                  return (
                    <button
                      key={ch.id}
                      type="button"
                      onClick={() => toggleChannel(ch.id)}
                      className={`text-[11px] px-2.5 py-1 rounded-md border transition-colors ${
                        active
                          ? 'bg-primary/15 border-primary/30 text-primary'
                          : 'bg-accent/30 border-border/50 text-muted-foreground hover:border-primary/30'
                      }`}
                    >
                      {ch.name}
                      {ch.is_default ? ' · 默认' : ''}
                    </button>
                  )
                })}
              </div>
            )}
          </div>

          <div className="rounded-lg border border-border/40 p-3 space-y-2">
            <div className="flex items-center justify-between">
              <div className="text-[12px] text-muted-foreground">条件列表</div>
              <Button variant="secondary" size="sm" className="h-7 text-[11px]" onClick={addCond}>添加条件</Button>
            </div>
            {form.items.map((it, idx) => (
              <div key={idx} className="grid grid-cols-12 gap-2">
                <div className="col-span-4">
                  <Select value={it.type} onValueChange={(v) => updateCond(idx, { type: v as ConditionType })}>
                    <SelectTrigger className="h-8"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {Object.entries(TYPE_LABEL).map(([k, label]) => (
                        <SelectItem key={k} value={k}>{label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="col-span-3">
                  <Select value={it.op} onValueChange={(v) => updateCond(idx, { op: v as ConditionOp })}>
                    <SelectTrigger className="h-8"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value=">=">{'>='}</SelectItem>
                      <SelectItem value="<=">{'<='}</SelectItem>
                      <SelectItem value=">">{'>'}</SelectItem>
                      <SelectItem value="<">{'<'}</SelectItem>
                      <SelectItem value="==">{'=='}</SelectItem>
                      <SelectItem value="between">between</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="col-span-4">
                  {it.op === 'between' ? (
                    <div className="grid grid-cols-2 gap-1">
                      <Input
                        className="h-8"
                        type="number"
                        value={Array.isArray(it.value) ? String(it.value[0]) : '0'}
                        onChange={(e) => {
                          const arr: [number, number] = Array.isArray(it.value) ? [Number(it.value[0] || 0), Number(it.value[1] || 0)] : [0, 0]
                          arr[0] = Number(e.target.value || 0)
                          updateCond(idx, { value: arr })
                        }}
                      />
                      <Input
                        className="h-8"
                        type="number"
                        value={Array.isArray(it.value) ? String(it.value[1]) : '0'}
                        onChange={(e) => {
                          const arr: [number, number] = Array.isArray(it.value) ? [Number(it.value[0] || 0), Number(it.value[1] || 0)] : [0, 0]
                          arr[1] = Number(e.target.value || 0)
                          updateCond(idx, { value: arr })
                        }}
                      />
                    </div>
                  ) : (
                    <Input
                      className="h-8"
                      type="number"
                      value={Array.isArray(it.value) ? String(it.value[0]) : String(it.value)}
                      onChange={(e) => updateCond(idx, { value: Number(e.target.value || 0) })}
                    />
                  )}
                </div>
                <div className="col-span-1">
                  <Button variant="ghost" size="sm" className="h-8 w-8 p-0" onClick={() => removeCond(idx)}>×</Button>
                </div>
              </div>
            ))}
          </div>

          <div className="flex items-center justify-end gap-2">
            <Button variant="ghost" onClick={() => props.onOpenChange(false)}>取消</Button>
            <Button onClick={submit} disabled={props.submitting}>
              {props.submitting ? '保存中...' : (props.submitLabel || '保存规则')}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
