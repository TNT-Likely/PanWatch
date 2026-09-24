import { useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { insightApi, type AddPositionEvalResult } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

export interface AddPositionCalc {
  newQty: number
  newCost: number
  diluteAbs: number
  dilutePct: number
  totalInvested: number
  isAdd: boolean
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
function fmtInt(n: number | null | undefined): string {
  if (n == null || !isFinite(n)) return '--'
  return Math.round(n).toLocaleString()
}

const VERDICT_STYLE: Record<string, string> = {
  适合: 'bg-success/15 text-success border-success/30',
  谨慎: 'bg-amber-500/15 text-amber-600 border-amber-500/30',
  不适合: 'bg-destructive/15 text-destructive border-destructive/30',
  未知: 'bg-muted text-muted-foreground border-border',
}

interface Props {
  symbol: string
  market: string
  currentQuantity: number
  currentCost: number
  currentPrice?: number | null
}

export default function AddPositionCalculator({
  symbol,
  market,
  currentQuantity,
  currentCost,
  currentPrice,
}: Props) {
  const { toast } = useToast()
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<'shares' | 'amount'>('shares')
  const [addRaw, setAddRaw] = useState('')
  const [priceRaw, setPriceRaw] = useState('')
  const [targetRaw, setTargetRaw] = useState('')
  const [aiLoading, setAiLoading] = useState(false)
  const [aiResult, setAiResult] = useState<AddPositionEvalResult | null>(null)

  const isCN = market === 'CN'

  const addPrice = useMemo(() => {
    const p = parseFloat(priceRaw)
    if (isFinite(p) && p > 0) return p
    return currentPrice && currentPrice > 0 ? currentPrice : 0
  }, [priceRaw, currentPrice])

  // 输入(股数/金额)→ 加仓股数
  const addQty = useMemo(() => {
    const v = parseFloat(addRaw)
    if (!isFinite(v) || v <= 0) return 0
    if (mode === 'shares') return v
    return addPrice > 0 ? v / addPrice : 0
  }, [addRaw, mode, addPrice])

  const calc = useMemo(
    () => calcAddPosition(currentQuantity, currentCost, addQty, addPrice),
    [currentQuantity, currentCost, addQty, addPrice],
  )

  const reverseShares = useMemo(() => {
    const t = parseFloat(targetRaw)
    if (!isFinite(t) || t <= 0) return null
    return calcSharesForTargetCost(currentQuantity, currentCost, addPrice, t)
  }, [targetRaw, currentQuantity, currentCost, addPrice])

  const runAi = async () => {
    if (!calc || addQty <= 0 || addPrice <= 0) {
      toast('请先填写有效的加仓股数/金额与价格', 'error')
      return
    }
    setAiLoading(true)
    setAiResult(null)
    try {
      const res = await insightApi.addPositionEval({
        symbol,
        market,
        current_quantity: currentQuantity,
        current_cost: currentCost,
        add_quantity: Math.round(addQty),
        add_price: addPrice,
      })
      setAiResult(res)
    } catch (e: any) {
      toast(e?.message || 'AI 评估失败', 'error')
    } finally {
      setAiLoading(false)
    }
  }

  const pricePlaceholder = currentPrice && currentPrice > 0 ? String(currentPrice) : '加仓价'
  const hasHolding = currentQuantity > 0 && currentCost > 0
  const lotWarn = isCN && addQty > 0 && Math.round(addQty) % 100 !== 0

  return (
    <div className="mt-3 border-t border-border/50 pt-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between text-caption text-muted-foreground"
      >
        <span>加仓测算{hasHolding ? '' : '（当前空仓 · 建仓测算）'}</span>
        <span>{open ? '收起 ▾' : '展开 ▸'}</span>
      </button>

      {open && (
        <div className="mt-2 space-y-2 text-body-sm">
          <div className="flex gap-1">
            {(['shares', 'amount'] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={`rounded border px-2 py-0.5 text-caption ${
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
              <div className="text-mini text-muted-foreground">
                {mode === 'shares' ? '加仓股数' : '加仓金额(元)'}
              </div>
              <Input
                value={addRaw}
                onChange={(e) => setAddRaw(e.target.value)}
                inputMode="decimal"
                placeholder={mode === 'shares' ? '如 200' : '如 10000'}
              />
            </label>
            <label className="space-y-1">
              <div className="text-mini text-muted-foreground">加仓价</div>
              <Input
                value={priceRaw}
                onChange={(e) => setPriceRaw(e.target.value)}
                inputMode="decimal"
                placeholder={pricePlaceholder}
              />
            </label>
          </div>

          {mode === 'amount' && addQty > 0 && (
            <div className="text-mini text-muted-foreground">
              ≈ {fmtInt(addQty)} 股{isCN ? `（≈${fmtInt(addQty / 100)} 手）` : ''}
            </div>
          )}

          {calc ? (
            <div className="space-y-1 rounded bg-accent/15 p-2">
              <div className="flex justify-between">
                <span className="text-muted-foreground">{calc.isAdd ? '加仓后成本' : '建仓成本'}</span>
                <span className="font-mono">{fmt(calc.newCost)}</span>
              </div>
              {calc.isAdd && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">摊薄</span>
                  <span className={`font-mono ${calc.diluteAbs >= 0 ? 'text-success' : 'text-destructive'}`}>
                    {calc.diluteAbs >= 0 ? '↓' : '↑'}
                    {fmt(Math.abs(calc.diluteAbs))}（{fmt(Math.abs(calc.dilutePct))}%）
                  </span>
                </div>
              )}
              <div className="flex justify-between">
                <span className="text-muted-foreground">合计股数 / 投入</span>
                <span className="font-mono">
                  {fmtInt(calc.newQty)} / {fmtInt(calc.totalInvested)}
                </span>
              </div>
              {lotWarn && (
                <div className="text-mini text-amber-600">提示:A股通常 100 股/手,建议取整到 100 的倍数</div>
              )}
            </div>
          ) : (
            <div className="text-caption text-muted-foreground">填写加仓股数/金额与价格后自动计算</div>
          )}

          {hasHolding && (
            <div className="grid grid-cols-2 items-end gap-2">
              <label className="space-y-1">
                <div className="text-mini text-muted-foreground">反推:目标成本</div>
                <Input
                  value={targetRaw}
                  onChange={(e) => setTargetRaw(e.target.value)}
                  inputMode="decimal"
                  placeholder={`< ${fmt(currentCost)}`}
                />
              </label>
              <div className="pb-1 text-caption">
                {targetRaw.trim() === '' ? (
                  <span className="text-muted-foreground">按加仓价反推所需股数</span>
                ) : reverseShares != null ? (
                  <span>
                    需加 <span className="font-mono text-foreground">{fmtInt(reverseShares)}</span> 股
                    {isCN ? `（≈${fmtInt(Math.ceil(reverseShares / 100))} 手）` : ''}
                    <br />约 <span className="font-mono">{fmtInt(reverseShares * addPrice)}</span> 元
                  </span>
                ) : (
                  <span className="text-amber-600">需 加仓价 &lt; 目标 &lt; 现成本 才能降到该成本</span>
                )}
              </div>
            </div>
          )}

          <div className="pt-1">
            <Button
              size="sm"
              variant="secondary"
              className="w-full"
              disabled={aiLoading || !calc}
              onClick={runAi}
            >
              {aiLoading ? 'AI 评估中…' : '让 AI 评估适不适合加仓'}
            </Button>
          </div>

          {aiResult && (
            <div className="space-y-1 rounded border border-border/60 p-2">
              <div className="flex items-center gap-2">
                <span
                  className={`rounded border px-2 py-0.5 text-caption ${
                    VERDICT_STYLE[aiResult.verdict] || VERDICT_STYLE['未知']
                  }`}
                >
                  {aiResult.verdict}
                </span>
                <span className="text-mini text-muted-foreground">AI 结论 · 仅供参考</span>
              </div>
              <div className="prose prose-sm dark:prose-invert max-w-none break-words text-body-sm leading-relaxed [&_p]:my-1 [&_ul]:my-1">
                <ReactMarkdown>{aiResult.content}</ReactMarkdown>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
