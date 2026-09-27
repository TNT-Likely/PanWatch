import { useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { insightApi, type AddPositionEvalResult } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { useTranslation } from 'react-i18next'

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
  适合: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
  谨慎: 'bg-amber-500/15 text-amber-600 border-amber-500/30',
  不适合: 'bg-rose-500/15 text-rose-500 border-rose-500/30',
  未知: 'bg-muted text-muted-foreground border-border',
}
const DEFAULT_VERDICT_STYLE = 'bg-muted text-muted-foreground border-border'

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
  const { t } = useTranslation('bizUi')
  const tr = (key: string, options?: Record<string, unknown>) =>
    (t as unknown as (key: string, options?: Record<string, unknown>) => string)(`addPosition.${key}`, options)
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
      toast(tr('invalidInput'), 'error')
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
      toast(e?.message || tr('evalFailed'), 'error')
    } finally {
      setAiLoading(false)
    }
  }

  const pricePlaceholder = currentPrice && currentPrice > 0 ? String(currentPrice) : tr('price')
  const hasHolding = currentQuantity > 0 && currentCost > 0
  const lotWarn = isCN && addQty > 0 && Math.round(addQty) % 100 !== 0

  return (
    <div className="mt-3 border-t border-border/50 pt-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between text-[11px] text-muted-foreground"
      >
        <span>{tr('title')}{hasHolding ? '' : tr('emptySuffix')}</span>
        <span>{open ? tr('collapse') : tr('expand')}</span>
      </button>

      {open && (
        <div className="mt-2 space-y-2 text-[12px]">
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
                {m === 'shares' ? tr('byShares') : tr('byAmount')}
              </button>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-2">
            <label className="space-y-1">
              <div className="text-[10px] text-muted-foreground">
                {mode === 'shares' ? tr('shares') : tr('amount')}
              </div>
              <Input
                value={addRaw}
                onChange={(e) => setAddRaw(e.target.value)}
                inputMode="decimal"
                placeholder={mode === 'shares' ? tr('sharesExample') : tr('amountExample')}
              />
            </label>
            <label className="space-y-1">
              <div className="text-[10px] text-muted-foreground">{tr('price')}</div>
              <Input
                value={priceRaw}
                onChange={(e) => setPriceRaw(e.target.value)}
                inputMode="decimal"
                placeholder={pricePlaceholder}
              />
            </label>
          </div>

          {mode === 'amount' && addQty > 0 && (
            <div className="text-[10px] text-muted-foreground">
              {tr('estimatedShares', { shares: fmtInt(addQty), lots: isCN ? tr('estimatedLots', { lots: fmtInt(addQty / 100) }) : '' })}
            </div>
          )}

          {calc ? (
            <div className="space-y-1 rounded bg-accent/15 p-2">
              <div className="flex justify-between">
                <span className="text-muted-foreground">{calc.isAdd ? tr('afterCost') : tr('initialCost')}</span>
                <span className="font-mono">{fmt(calc.newCost)}</span>
              </div>
              {calc.isAdd && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">{tr('dilution')}</span>
                  <span className={`font-mono ${calc.diluteAbs >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>
                    {calc.diluteAbs >= 0 ? '↓' : '↑'}
                    {fmt(Math.abs(calc.diluteAbs))}（{fmt(Math.abs(calc.dilutePct))}%）
                  </span>
                </div>
              )}
              <div className="flex justify-between">
                <span className="text-muted-foreground">{tr('total')}</span>
                <span className="font-mono">
                  {fmtInt(calc.newQty)} / {fmtInt(calc.totalInvested)}
                </span>
              </div>
              {lotWarn && (
                <div className="text-[10px] text-amber-600">{tr('lotWarning')}</div>
              )}
            </div>
          ) : (
            <div className="text-[11px] text-muted-foreground">{tr('inputHint')}</div>
          )}

          {hasHolding && (
            <div className="grid grid-cols-2 items-end gap-2">
              <label className="space-y-1">
                <div className="text-[10px] text-muted-foreground">{tr('targetCost')}</div>
                <Input
                  value={targetRaw}
                  onChange={(e) => setTargetRaw(e.target.value)}
                  inputMode="decimal"
                  placeholder={`< ${fmt(currentCost)}`}
                />
              </label>
              <div className="pb-1 text-[11px]">
                {targetRaw.trim() === '' ? (
                  <span className="text-muted-foreground">{tr('targetHint')}</span>
                ) : reverseShares != null ? (
                  <span>
                    {tr('required', { shares: fmtInt(reverseShares), lots: isCN ? tr('estimatedLots', { lots: fmtInt(Math.ceil(reverseShares / 100)) }) : '' })}
                    <br />{tr('approximately', { amount: fmtInt(reverseShares * addPrice) })}
                  </span>
                ) : (
                  <span className="text-amber-600">{tr('targetInvalid')}</span>
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
              {aiLoading ? tr('evaluating') : tr('evaluate')}
            </Button>
          </div>

          {aiResult && (
            <div className="space-y-1 rounded border border-border/60 p-2">
              <div className="flex items-center gap-2">
                <span
                  className={`rounded border px-2 py-0.5 text-[11px] ${
                    VERDICT_STYLE[aiResult.verdict] || DEFAULT_VERDICT_STYLE
                  }`}
                >
                  {aiResult.verdict === '适合' ? tr('verdicts.suitable') : aiResult.verdict === '谨慎' ? tr('verdicts.cautious') : aiResult.verdict === '不适合' ? tr('verdicts.unsuitable') : tr('verdicts.unknown')}
                </span>
                <span className="text-[10px] text-muted-foreground">{tr('aiResult')}</span>
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
