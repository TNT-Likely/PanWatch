import { useCallback, useEffect, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { insightApi, positionsApi, tradeDatetimeLocalToIso, type AddPositionEvalResult, type PositionAddResult, type PositionTrade } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

export interface AddPositionCalc {
  newQty: number
  newCost: number
  diluteAbs: number
  dilutePct: number
  totalInvested: number
  isAdd: boolean
}

export interface ReducePositionCalc {
  newQty: number
  costUnchanged: number
  realizedPnl: number
  realizedPct: number
  sellAmount: number
}

export interface PositionHoldingOption {
  id: number
  account_name: string
  quantity: number
  cost_price: number
}

/** 加仓后摊薄成本(正算)。无效输入返回 null。 */
export function calcAddPosition(
  curQty: number,
  curCost: number,
  addQty: number,
  addPrice: number,
): AddPositionCalc | null {
  if (!(addQty > 0) || !(addPrice > 0)) return null
  const newQty = curQty + addQty
  if (!(newQty > 0)) return null
  const newCost = (curQty * curCost + addQty * addPrice) / newQty
  const isAdd = curQty > 0 && curCost > 0
  const diluteAbs = isAdd ? curCost - newCost : 0
  const dilutePct = isAdd && curCost > 0 ? (diluteAbs / curCost) * 100 : 0
  return { newQty, newCost, diluteAbs, dilutePct, totalInvested: newQty * newCost, isAdd }
}

/** 减仓后剩余股数与实现盈亏。卖出股数不得超过持仓。 */
export function calcReducePosition(
  curQty: number,
  curCost: number,
  sellQty: number,
  sellPrice: number,
): ReducePositionCalc | null {
  if (!(sellQty > 0) || !(sellPrice > 0)) return null
  if (!(curQty > 0) || sellQty > curQty) return null
  const newQty = curQty - sellQty
  const realizedPnl = (sellPrice - curCost) * sellQty
  const realizedPct = curCost > 0 ? ((sellPrice - curCost) / curCost) * 100 : 0
  return {
    newQty,
    costUnchanged: curCost,
    realizedPnl,
    realizedPct,
    sellAmount: sellQty * sellPrice,
  }
}

/** 反推:把成本降到 target 需要按 addPrice 加多少股。仅当 addPrice < target < curCost 可行。 */
export function calcSharesForTargetCost(
  curQty: number,
  curCost: number,
  addPrice: number,
  target: number,
): number | null {
  if (!(curQty > 0) || !(curCost > 0)) return null
  if (!(addPrice > 0) || !(target > 0)) return null
  if (!(addPrice < target && target < curCost)) return null
  const q = (curQty * (curCost - target)) / (target - addPrice)
  return q > 0 ? q : null
}

function fmt(n: number | null | undefined, d = 2): string {
  if (n == null || !isFinite(n)) return '--'
  return n.toFixed(d)
}
/** 成本价：保留有效小数位，避免四舍五入后前后相同 */
function fmtCost(n: number | null | undefined): string {
  if (n == null || !isFinite(n)) return '--'
  return n.toFixed(4).replace(/\.?0+$/, '')
}
function fmtInt(n: number | null | undefined): string {
  if (n == null || !isFinite(n)) return '--'
  return Math.round(n).toLocaleString()
}

function fmtTradeTime(raw: string | null | undefined): string {
  if (!raw) return '--'
  const d = new Date(raw)
  if (Number.isNaN(d.getTime())) return raw.slice(0, 16)
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

const VERDICT_STYLE: Record<string, string> = {
  适合: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
  谨慎: 'bg-amber-500/15 text-amber-600 border-amber-500/30',
  不适合: 'bg-rose-500/15 text-rose-500 border-rose-500/30',
  未知: 'bg-muted text-muted-foreground border-border',
}

interface Props {
  symbol: string
  market: string
  currentQuantity: number
  currentCost: number
  currentPrice?: number | null
  /** 各账户下的持仓明细(用于选择加仓账户) */
  holdings?: PositionHoldingOption[]
  /** 加仓成功后回调(刷新持仓) */
  onApplied?: (result: PositionAddResult) => void
  /** 有持仓时默认展开 */
  defaultOpen?: boolean
  /** 递增时强制展开(供外部「加仓/减仓」按钮触发) */
  expandSignal?: number
  /** 与 expandSignal 配合，展开后切换到加仓或减仓 */
  expandMode?: 'add' | 'reduce'
}

export default function AddPositionCalculator({
  symbol,
  market,
  currentQuantity,
  currentCost,
  currentPrice,
  holdings = [],
  onApplied,
  defaultOpen = false,
  expandSignal = 0,
  expandMode = 'add',
}: Props) {
  const { toast } = useToast()
  const [open, setOpen] = useState(defaultOpen)
  const [tradeMode, setTradeMode] = useState<'add' | 'reduce'>('add')
  const [mode, setMode] = useState<'shares' | 'amount'>('shares')
  const [addRaw, setAddRaw] = useState('')
  const [priceRaw, setPriceRaw] = useState('')
  const [tradeTimeRaw, setTradeTimeRaw] = useState('')
  const [targetRaw, setTargetRaw] = useState('')
  const [aiLoading, setAiLoading] = useState(false)
  const [aiResult, setAiResult] = useState<AddPositionEvalResult | null>(null)
  const [applyLoading, setApplyLoading] = useState(false)
  const [trades, setTrades] = useState<PositionTrade[]>([])
  const [tradesLoading, setTradesLoading] = useState(false)

  const defaultPositionId = holdings.length === 1 ? String(holdings[0].id) : ''
  const [selectedPositionId, setSelectedPositionId] = useState(defaultPositionId)

  useEffect(() => {
    if (defaultOpen) setOpen(true)
  }, [defaultOpen])

  useEffect(() => {
    if (expandSignal > 0) {
      setOpen(true)
      setTradeMode(expandMode)
    }
  }, [expandSignal, expandMode])

  useEffect(() => {
    if (holdings.length === 1) setSelectedPositionId(String(holdings[0].id))
    else if (holdings.length === 0) setSelectedPositionId('')
    else if (!holdings.some(h => String(h.id) === selectedPositionId)) {
      setSelectedPositionId('')
    }
  }, [holdings, selectedPositionId])

  const selectedHolding = useMemo(
    () => holdings.find(h => String(h.id) === selectedPositionId) ?? null,
    [holdings, selectedPositionId],
  )

  const baseQty = selectedHolding?.quantity ?? currentQuantity
  const baseCost = selectedHolding?.cost_price ?? currentCost

  const isCN = market === 'CN'

  const addPrice = useMemo(() => {
    const p = parseFloat(priceRaw)
    if (isFinite(p) && p > 0) return p
    return currentPrice && currentPrice > 0 ? currentPrice : 0
  }, [priceRaw, currentPrice])

  const tradeQty = useMemo(() => {
    const v = parseFloat(addRaw)
    if (!isFinite(v) || v <= 0) return 0
    if (mode === 'shares') return Math.round(v)
    return addPrice > 0 ? Math.round(v / addPrice) : 0
  }, [addRaw, mode, addPrice])

  const addCalc = useMemo(
    () => (tradeMode === 'add' ? calcAddPosition(baseQty, baseCost, tradeQty, addPrice) : null),
    [tradeMode, baseQty, baseCost, tradeQty, addPrice],
  )

  const reduceCalc = useMemo(
    () => (tradeMode === 'reduce' ? calcReducePosition(baseQty, baseCost, tradeQty, addPrice) : null),
    [tradeMode, baseQty, baseCost, tradeQty, addPrice],
  )

  const reverseShares = useMemo(() => {
    const t = parseFloat(targetRaw)
    if (!isFinite(t) || t <= 0) return null
    return calcSharesForTargetCost(baseQty, baseCost, addPrice, t)
  }, [targetRaw, baseQty, baseCost, addPrice])

  const loadTrades = useCallback(async (positionId: number) => {
    setTradesLoading(true)
    try {
      const rows = await positionsApi.trades(positionId, 10)
      setTrades(rows || [])
    } catch {
      setTrades([])
    } finally {
      setTradesLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!open || !selectedPositionId) {
      setTrades([])
      return
    }
    void loadTrades(Number(selectedPositionId))
  }, [open, selectedPositionId, loadTrades])

  const runAi = async () => {
    if (!addCalc || tradeQty <= 0 || addPrice <= 0) {
      toast('请先填写有效的加仓股数/金额与价格', 'error')
      return
    }
    setAiLoading(true)
    setAiResult(null)
    try {
      const res = await insightApi.addPositionEval({
        symbol,
        market,
        current_quantity: baseQty,
        current_cost: baseCost,
        add_quantity: tradeQty,
        add_price: addPrice,
      })
      setAiResult(res)
    } catch (e: any) {
      toast(e?.message || 'AI 评估失败', 'error')
    } finally {
      setAiLoading(false)
    }
  }

  const applyTradeResult = useCallback(
    (result: PositionAddResult) => {
      if (result.trade) {
        setTrades((prev) => [result.trade, ...prev.filter((t) => t.id !== result.trade.id)])
      }
      onApplied?.(result)
    },
    [onApplied],
  )

  const confirmAdd = async () => {
    if (!selectedHolding) {
      toast('请选择要加仓的账户', 'error')
      return
    }
    if (!addCalc || tradeQty <= 0 || addPrice <= 0) {
      toast('请先填写有效的加仓股数/金额与价格', 'error')
      return
    }
    setApplyLoading(true)
    try {
      const traded_at = tradeDatetimeLocalToIso(tradeTimeRaw)
      const result = await positionsApi.add(selectedHolding.id, {
        price: addPrice,
        quantity: tradeQty,
        ...(traded_at ? { traded_at } : {}),
      })
      toast(`已记录加仓 ${tradeQty} 股 @ ${fmt(addPrice)}，新成本 ${fmtCost(result.position.cost_price)}`, 'success')
      setAddRaw('')
      setPriceRaw('')
      setTradeTimeRaw('')
      setTargetRaw('')
      setAiResult(null)
      applyTradeResult(result)
      await loadTrades(selectedHolding.id)
    } catch (e: any) {
      toast(e?.message || '加仓记录失败', 'error')
    } finally {
      setApplyLoading(false)
    }
  }

  const confirmReduce = async () => {
    if (!selectedHolding) {
      toast('请选择要减仓的账户', 'error')
      return
    }
    if (!reduceCalc || tradeQty <= 0 || addPrice <= 0) {
      toast('请先填写有效的卖出股数/金额与价格', 'error')
      return
    }
    setApplyLoading(true)
    try {
      const traded_at = tradeDatetimeLocalToIso(tradeTimeRaw)
      const result = await positionsApi.reduce(selectedHolding.id, {
        price: addPrice,
        quantity: tradeQty,
        ...(traded_at ? { traded_at } : {}),
      })
      const pnl = reduceCalc.realizedPnl
      toast(
        `已记录减仓 ${tradeQty} 股 @ ${fmt(addPrice)}，剩余 ${fmtInt(reduceCalc.newQty)} 股，本次盈亏 ${pnl >= 0 ? '+' : ''}${fmt(pnl)}`,
        'success',
      )
      setAddRaw('')
      setPriceRaw('')
      setTradeTimeRaw('')
      setTargetRaw('')
      setAiResult(null)
      applyTradeResult(result)
      await loadTrades(selectedHolding.id)
    } catch (e: any) {
      toast(e?.message || '减仓记录失败', 'error')
    } finally {
      setApplyLoading(false)
    }
  }

  const pricePlaceholder = currentPrice && currentPrice > 0
    ? String(currentPrice)
    : (tradeMode === 'reduce' ? '卖出价' : '买入价')
  const hasHolding = baseQty > 0 && baseCost > 0
  const lotWarn = isCN && tradeQty > 0 && tradeQty % 100 !== 0
  const canApplyAdd = !!selectedHolding && !!addCalc && tradeQty > 0 && addPrice > 0
  const canApplyReduce = !!selectedHolding && !!reduceCalc && tradeQty > 0 && addPrice > 0
  const isReduce = tradeMode === 'reduce'

  return (
    <div className="mt-3 border-t border-border/50 pt-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`flex w-full items-center justify-between rounded-lg border px-2.5 py-2 text-left transition-colors ${
          open
            ? 'border-primary/30 bg-primary/5'
            : hasHolding
              ? 'border-primary/20 bg-primary/5 hover:bg-primary/10'
              : 'border-border/50 bg-accent/10 hover:bg-accent/20'
        }`}
      >
        <span className={`text-[12px] font-medium ${hasHolding ? 'text-foreground' : 'text-muted-foreground'}`}>
          {hasHolding ? '持仓变动' : '建仓测算（当前空仓）'}
        </span>
        <span className="text-[11px] text-muted-foreground">{open ? '收起 ▾' : '展开 ▸'}</span>
      </button>

      {open && (
        <div className="mt-2 space-y-2 text-[12px]">
          {hasHolding && (
            <div className="flex gap-1">
              {(['add', 'reduce'] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => {
                    setTradeMode(m)
                    setAddRaw('')
                    setTradeTimeRaw('')
                    setTargetRaw('')
                    setAiResult(null)
                  }}
                  className={`rounded border px-2 py-0.5 text-[11px] ${
                    tradeMode === m
                      ? m === 'add'
                        ? 'border-primary bg-primary text-primary-foreground'
                        : 'border-rose-500/50 bg-rose-500/15 text-rose-600'
                      : 'border-border text-muted-foreground'
                  }`}
                >
                  {m === 'add' ? '加仓' : '减仓'}
                </button>
              ))}
            </div>
          )}

          {holdings.length > 1 && (
            <label className="block space-y-1">
              <div className="text-[10px] text-muted-foreground">{isReduce ? '减仓账户' : '加仓账户'}</div>
              <Select value={selectedPositionId} onValueChange={setSelectedPositionId}>
                <SelectTrigger className="h-8 text-[12px]">
                  <SelectValue placeholder="选择账户" />
                </SelectTrigger>
                <SelectContent>
                  {holdings.map(h => (
                    <SelectItem key={h.id} value={String(h.id)}>
                      {h.account_name} · {fmtInt(h.quantity)}股 @ {fmtCost(h.cost_price)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
          )}

          {holdings.length === 1 && (
            <div className="text-[10px] text-muted-foreground">
              账户：{holdings[0].account_name} · 当前 {fmtInt(holdings[0].quantity)} 股 @ {fmtCost(holdings[0].cost_price)}
            </div>
          )}

          <div className="flex gap-1">
            {(['shares', 'amount'] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={`rounded border px-2 py-0.5 text-[11px] ${
                  mode === m
                    ? 'border-primary bg-primary text-primary-foreground'
                    : 'border-border text-muted-foreground'
                }`}
              >
                {m === 'shares' ? '按股数' : '按金额'}
              </button>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-2">
            <label className="space-y-1">
              <div className="text-[10px] text-muted-foreground">
                {mode === 'shares'
                  ? (isReduce ? '卖出股数' : '买入股数')
                  : (isReduce ? '卖出金额(元)' : '买入金额(元)')}
              </div>
              <Input
                value={addRaw}
                onChange={(e) => setAddRaw(e.target.value)}
                inputMode="decimal"
                placeholder={mode === 'shares' ? (isReduce ? '如 100' : '如 200') : '如 10000'}
              />
            </label>
            <label className="space-y-1">
              <div className="text-[10px] text-muted-foreground">{isReduce ? '卖出价' : '买入价'}</div>
              <Input
                value={priceRaw}
                onChange={(e) => setPriceRaw(e.target.value)}
                inputMode="decimal"
                placeholder={pricePlaceholder}
              />
            </label>
          </div>

          <label className="space-y-1">
            <div className="text-[10px] text-muted-foreground">
              {isReduce ? '卖出时间' : '买入时间'}
              <span className="text-muted-foreground/70">（选填，默认当前）</span>
            </div>
            <Input
              type="datetime-local"
              value={tradeTimeRaw}
              onChange={(e) => setTradeTimeRaw(e.target.value)}
              className="font-mono text-[12px]"
            />
          </label>

          {mode === 'amount' && tradeQty > 0 && (
            <div className="text-[10px] text-muted-foreground">
              ≈ {fmtInt(tradeQty)} 股{isCN ? `（≈${fmtInt(tradeQty / 100)} 手）` : ''}
            </div>
          )}

          {isReduce ? (
            reduceCalc ? (
              <div className="space-y-1 rounded bg-accent/15 p-2">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">剩余股数</span>
                  <span className="font-mono">{fmtInt(reduceCalc.newQty)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">成本(不变)</span>
                  <span className="font-mono">{fmt(reduceCalc.costUnchanged)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">本次盈亏</span>
                  <span className={`font-mono ${reduceCalc.realizedPnl >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                    {reduceCalc.realizedPnl >= 0 ? '+' : ''}{fmt(reduceCalc.realizedPnl)}
                    （{reduceCalc.realizedPct >= 0 ? '+' : ''}{fmt(reduceCalc.realizedPct)}%）
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">卖出金额</span>
                  <span className="font-mono">{fmtInt(reduceCalc.sellAmount)}</span>
                </div>
                {lotWarn && (
                  <div className="text-[10px] text-amber-600">提示:A股通常 100 股/手,建议取整到 100 的倍数</div>
                )}
              </div>
            ) : tradeQty > baseQty ? (
              <div className="text-[11px] text-rose-500">卖出股数不能超过当前持仓 {fmtInt(baseQty)} 股</div>
            ) : (
              <div className="text-[11px] text-muted-foreground">填写卖出股数/金额与价格后自动计算</div>
            )
          ) : addCalc ? (
            <div className="space-y-1 rounded bg-accent/15 p-2">
              <div className="flex justify-between">
                <span className="text-muted-foreground">{addCalc.isAdd ? '加仓后成本' : '建仓成本'}</span>
                <span className="font-mono">{fmt(addCalc.newCost)}</span>
              </div>
              {addCalc.isAdd && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">摊薄</span>
                  <span className={`font-mono ${addCalc.diluteAbs >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>
                    {addCalc.diluteAbs >= 0 ? '↓' : '↑'}
                    {fmt(Math.abs(addCalc.diluteAbs))}（{fmt(Math.abs(addCalc.dilutePct))}%）
                  </span>
                </div>
              )}
              <div className="flex justify-between">
                <span className="text-muted-foreground">合计股数 / 投入</span>
                <span className="font-mono">
                  {fmtInt(addCalc.newQty)} / {fmtInt(addCalc.totalInvested)}
                </span>
              </div>
              {lotWarn && (
                <div className="text-[10px] text-amber-600">提示:A股通常 100 股/手,建议取整到 100 的倍数</div>
              )}
            </div>
          ) : (
            <div className="text-[11px] text-muted-foreground">填写买入股数/金额与价格后自动计算</div>
          )}

          {hasHolding && isReduce && (
            <Button
              size="sm"
              variant="outline"
              className="w-full border-rose-500/30 text-rose-600 hover:bg-rose-500/10"
              disabled={applyLoading || !canApplyReduce}
              onClick={confirmReduce}
            >
              {applyLoading ? '记录中…' : `确认减仓 · ${tradeQty > 0 ? `${fmtInt(tradeQty)}股 @ ${fmt(addPrice)}` : '填写卖出信息'}`}
            </Button>
          )}

          {hasHolding && !isReduce && (
            <Button
              size="sm"
              className="w-full"
              disabled={applyLoading || !canApplyAdd}
              onClick={confirmAdd}
            >
              {applyLoading ? '记录中…' : `确认加仓 · ${tradeQty > 0 ? `${fmtInt(tradeQty)}股 @ ${fmt(addPrice)}` : '填写买入信息'}`}
            </Button>
          )}

          {!hasHolding && holdings.length === 0 && (
            <div className="text-[10px] text-muted-foreground rounded bg-accent/10 px-2 py-1.5">
              当前未持仓。请先在「持仓」页添加持仓后再记录加仓。
            </div>
          )}

          {hasHolding && !isReduce && (
            <div className="grid grid-cols-2 items-end gap-2">
              <label className="space-y-1">
                <div className="text-[10px] text-muted-foreground">反推:目标成本</div>
                <Input
                  value={targetRaw}
                  onChange={(e) => setTargetRaw(e.target.value)}
                  inputMode="decimal"
                  placeholder={`< ${fmt(baseCost)}`}
                />
              </label>
              <div className="pb-1 text-[11px]">
                {targetRaw.trim() === '' ? (
                  <span className="text-muted-foreground">按买入价反推所需股数</span>
                ) : reverseShares != null ? (
                  <span>
                    需加 <span className="font-mono text-foreground">{fmtInt(reverseShares)}</span> 股
                    {isCN ? `（≈${fmtInt(Math.ceil(reverseShares / 100))} 手）` : ''}
                    <br />约 <span className="font-mono">{fmtInt(reverseShares * addPrice)}</span> 元
                  </span>
                ) : (
                  <span className="text-amber-600">需 买入价 &lt; 目标 &lt; 现成本 才能降到该成本</span>
                )}
              </div>
            </div>
          )}

          {selectedPositionId && (
            <div className="space-y-1 rounded border border-border/40 p-2">
              <div className="text-[10px] text-muted-foreground">最近交易记录</div>
              {tradesLoading ? (
                <div className="text-[10px] text-muted-foreground">加载中…</div>
              ) : trades.length === 0 ? (
                <div className="text-[10px] text-muted-foreground">暂无记录</div>
              ) : (
                <ul className="space-y-1">
                  {trades.map(t => (
                    <li key={t.id} className="flex items-center justify-between text-[10px]">
                      <span className="text-muted-foreground">{fmtTradeTime(t.traded_at)}</span>
                      <span className="font-mono">
                        {t.side === 'sell' ? '-' : '+'}{fmtInt(t.quantity)} @ {fmt(t.price)}
                      </span>
                      <span className="text-muted-foreground">
                        成本 {fmtCost(t.cost_before)} → {fmtCost(t.cost_after)}
                        {t.qty_before != null && t.qty_after != null && t.qty_before !== t.qty_after ? (
                          <span className="ml-1">· {fmtInt(t.qty_before)}→{fmtInt(t.qty_after)}股</span>
                        ) : null}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <div className="pt-1">
            {!isReduce && (
              <Button
                size="sm"
                variant="secondary"
                className="w-full"
                disabled={aiLoading || !addCalc}
                onClick={runAi}
              >
                {aiLoading ? 'AI 评估中…' : '让 AI 评估适不适合加仓'}
              </Button>
            )}
          </div>

          {aiResult && (
            <div className="space-y-1 rounded border border-border/60 p-2">
              <div className="flex items-center gap-2">
                <span
                  className={`rounded border px-2 py-0.5 text-[11px] ${
                    VERDICT_STYLE[aiResult.verdict] || VERDICT_STYLE['未知']
                  }`}
                >
                  {aiResult.verdict}
                </span>
                <span className="text-[10px] text-muted-foreground">AI 结论 · 仅供参考</span>
              </div>
              <div className="prose prose-sm dark:prose-invert max-w-none break-words text-[12px] leading-relaxed [&_p]:my-1 [&_ul]:my-1">
                <ReactMarkdown>{aiResult.content}</ReactMarkdown>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
