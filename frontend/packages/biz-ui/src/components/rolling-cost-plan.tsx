import { useMemo, useState } from 'react'
import { Input } from '@panwatch/base-ui/components/ui/input'
import {
  buildRollingCostPlan,
  buildRollingCostPlanBrief,
  type RollingCostKlineLevels,
} from '@/lib/rolling-cost-plan'

interface RollingCostPlanPanelProps {
  market: string
  currentQuantity: number
  currentCost: number
  currentPrice?: number | null
  kline?: RollingCostKlineLevels | null
}

function fmt(value: number | null | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return '--'
  return value.toFixed(digits)
}

function fmtInt(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '--'
  return Math.round(value).toLocaleString()
}

function pctToRatio(value: string): number {
  const parsed = parseFloat(value)
  if (!Number.isFinite(parsed)) return 0.5
  return parsed / 100
}

export function RollingCostPlanPanel({
  market,
  currentQuantity,
  currentCost,
  currentPrice,
  kline,
}: RollingCostPlanPanelProps) {
  const [open, setOpen] = useState(false)
  const [basePctRaw, setBasePctRaw] = useState('50')
  const [tranchesRaw, setTranchesRaw] = useState('3')
  const [amountRaw, setAmountRaw] = useState('')
  const [reboundRaw, setReboundRaw] = useState('5')

  const plan = useMemo(() => {
    const amount = parseFloat(amountRaw)
    const trancheAmount = Number.isFinite(amount) && amount > 0 ? amount : undefined
    const tranches = parseInt(tranchesRaw, 10)
    const reboundPct = parseFloat(reboundRaw)

    return buildRollingCostPlan({
      market,
      currentQuantity,
      currentCost,
      currentPrice,
      kline,
      baseRatio: pctToRatio(basePctRaw),
      tranches: Number.isFinite(tranches) ? tranches : 3,
      trancheAmount,
      reboundPct: Number.isFinite(reboundPct) ? reboundPct : 5,
    })
  }, [amountRaw, basePctRaw, currentCost, currentPrice, currentQuantity, kline, market, reboundRaw, tranchesRaw])

  const brief = buildRollingCostPlanBrief(plan)
  const hasHolding = currentQuantity > 0 && currentCost > 0

  return (
    <div className="mt-3 border-t border-border/50 pt-3">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between text-[11px] text-muted-foreground"
      >
        <span>滚动降成本计划{hasHolding ? '' : '（试探建仓）'}</span>
        <span>{open ? '收起 ▾' : '展开 ▸'}</span>
      </button>

      {!open && (
        <div className="mt-1 text-[10px] text-muted-foreground">
          {brief || (plan.warnings[0] ?? '按底仓 + 机动仓低吸高抛思路生成测算')}
        </div>
      )}

      {open && (
        <div className="mt-2 space-y-2 text-[12px]">
          <div className="rounded bg-accent/15 p-2 text-[11px] leading-relaxed text-muted-foreground">
            底仓负责不踏空，机动仓只在回撤档位低吸；反弹踢出时只卖低位筹码。若产业逻辑或基本面破坏，停止加仓并重新评估。
          </div>

          <div className="grid grid-cols-2 gap-2">
            <label className="space-y-1">
              <div className="text-[10px] text-muted-foreground">底仓比例(%)</div>
              <Input value={basePctRaw} onChange={(e) => setBasePctRaw(e.target.value)} inputMode="decimal" />
            </label>
            <label className="space-y-1">
              <div className="text-[10px] text-muted-foreground">低吸档数</div>
              <Input value={tranchesRaw} onChange={(e) => setTranchesRaw(e.target.value)} inputMode="numeric" />
            </label>
            <label className="space-y-1">
              <div className="text-[10px] text-muted-foreground">单档机动资金</div>
              <Input
                value={amountRaw}
                onChange={(e) => setAmountRaw(e.target.value)}
                inputMode="decimal"
                placeholder="默认按持仓估算"
              />
            </label>
            <label className="space-y-1">
              <div className="text-[10px] text-muted-foreground">踢出反弹幅度(%)</div>
              <Input value={reboundRaw} onChange={(e) => setReboundRaw(e.target.value)} inputMode="decimal" />
            </label>
          </div>

          <div className="grid grid-cols-3 gap-2">
            <div className="rounded bg-accent/15 px-2 py-1.5">
              <div className="text-[10px] text-muted-foreground">底仓数量</div>
              <div className="font-mono">{fmtInt(plan.baseQty)}</div>
            </div>
            <div className="rounded bg-accent/15 px-2 py-1.5">
              <div className="text-[10px] text-muted-foreground">机动数量</div>
              <div className="font-mono">{fmtInt(plan.rollingQty)}</div>
            </div>
            <div className="rounded bg-accent/15 px-2 py-1.5">
              <div className="text-[10px] text-muted-foreground">单档预算</div>
              <div className="font-mono">{fmtInt(plan.trancheAmount)}</div>
            </div>
          </div>

          {plan.tranches.length > 0 ? (
            <div className="overflow-x-auto rounded border border-border/40">
              <table className="w-full min-w-[560px] text-[11px]">
                <thead className="bg-accent/20 text-muted-foreground">
                  <tr>
                    <th className="px-2 py-1.5 text-left">档位</th>
                    <th className="px-2 py-1.5 text-right">低吸价</th>
                    <th className="px-2 py-1.5 text-right">股数</th>
                    <th className="px-2 py-1.5 text-right">加后成本</th>
                    <th className="px-2 py-1.5 text-right">摊薄</th>
                    <th className="px-2 py-1.5 text-right">踢出价</th>
                    <th className="px-2 py-1.5 text-right">踢后成本</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/30">
                  {plan.tranches.map((tranche) => (
                    <tr key={tranche.index}>
                      <td className="px-2 py-1.5">第 {tranche.index} 档</td>
                      <td className="px-2 py-1.5 text-right font-mono">{fmt(tranche.buyPrice)}</td>
                      <td className="px-2 py-1.5 text-right font-mono">{fmtInt(tranche.addQty)}</td>
                      <td className="px-2 py-1.5 text-right font-mono">{fmt(tranche.afterBuyCost)}</td>
                      <td className="px-2 py-1.5 text-right font-mono text-emerald-500">
                        {fmt(Math.max(0, tranche.costDilution))} / {fmt(Math.max(0, tranche.costDilutionPct))}%
                      </td>
                      <td className="px-2 py-1.5 text-right font-mono">{fmt(tranche.sellPrice)}</td>
                      <td className="px-2 py-1.5 text-right font-mono">{fmt(tranche.afterSellCost)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="rounded bg-accent/15 p-2 text-[11px] text-muted-foreground">
              暂无可执行档位。上涨不追，等回撤到可测算区间再看。
            </div>
          )}

          {plan.warnings.length > 0 && (
            <div className="space-y-1 rounded border border-amber-500/20 bg-amber-500/10 p-2">
              {plan.warnings.map((warning) => (
                <div key={warning} className="text-[10px] text-amber-700 dark:text-amber-400">
                  {warning}
                </div>
              ))}
            </div>
          )}

          <div className="text-[10px] leading-relaxed text-muted-foreground/80">
            踢后成本按“先低吸、再按踢出价卖出本档筹码”测算。停止条件：跌破最后一档后不继续摊，基本面或产业逻辑恶化不加，连续低吸无反弹则暂停，单票亏损触及账户风险上限需重新评估。
          </div>
        </div>
      )}
    </div>
  )
}
