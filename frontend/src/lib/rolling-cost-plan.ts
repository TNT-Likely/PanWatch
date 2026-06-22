export interface RollingCostKlineLevels {
  support?: number | null
  support_s?: number | null
  support_m?: number | null
  support_l?: number | null
  resistance?: number | null
  resistance_s?: number | null
  resistance_m?: number | null
  resistance_l?: number | null
}

export interface BuildRollingCostPlanInput {
  market: string
  currentQuantity: number
  currentCost: number
  currentPrice: number | null | undefined
  kline?: RollingCostKlineLevels | null
  tranches?: number
  trancheAmount?: number
  baseRatio?: number
  reboundPct?: number
}

export interface RollingCostTranche {
  index: number
  buyPrice: number
  addQty: number
  addAmount: number
  beforeQty: number
  beforeCost: number
  afterBuyQty: number
  afterBuyCost: number
  costDilution: number
  costDilutionPct: number
  sellPrice: number
  sellQty: number
  afterSellQty: number
  afterSellCost: number
  baseQtyAfterSell: number
}

export interface RollingCostPlan {
  mode: 'entry' | 'rolling'
  currentQuantity: number
  currentCost: number
  currentPrice: number | null
  baseRatio: number
  baseQty: number
  rollingQty: number
  trancheAmount: number
  tranches: RollingCostTranche[]
  warnings: string[]
}

const FALLBACK_DRAWDOWNS = [0.05, 0.1, 0.15, 0.2]

function isPositiveNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
}

function roundPrice(value: number): number {
  return Math.round(value * 100) / 100
}

function roundQtyForMarket(quantity: number, market: string): number {
  if (!Number.isFinite(quantity) || quantity <= 0) return 0
  if (market === 'CN') return Math.floor(quantity / 100) * 100
  return Math.floor(quantity)
}

function buildFallbackLevels(currentPrice: number, count: number): number[] {
  return FALLBACK_DRAWDOWNS.slice(0, count).map(rate => roundPrice(currentPrice * (1 - rate)))
}

function formatFallbackDrawdowns(count: number): string {
  return FALLBACK_DRAWDOWNS.slice(0, count).map(rate => `${Math.round(rate * 100)}%`).join('/')
}

function buildSupportLevels(
  currentPrice: number,
  count: number,
  kline?: RollingCostKlineLevels | null,
): { levels: number[]; usedFallback: boolean } {
  const rawLevels = [
    kline?.support,
    kline?.support_s,
    kline?.support_m,
    kline?.support_l,
  ]
  const levels = Array.from(
    new Set(
      rawLevels
        .filter(isPositiveNumber)
        .map(roundPrice)
        .filter(level => level < currentPrice),
    ),
  ).sort((a, b) => b - a)

  if (levels.length >= count) return { levels: levels.slice(0, count), usedFallback: false }

  const fallback = buildFallbackLevels(currentPrice, count)
  const merged = Array.from(new Set([...levels, ...fallback]))
    .filter(level => level < currentPrice)
    .sort((a, b) => b - a)
    .slice(0, count)

  return { levels: merged, usedFallback: levels.length === 0 }
}

function resolveSellPrice(
  buyPrice: number,
  reboundPct: number,
  kline?: RollingCostKlineLevels | null,
): number {
  const target = buyPrice * (1 + reboundPct / 100)
  const resistance = [
    kline?.resistance,
    kline?.resistance_s,
    kline?.resistance_m,
    kline?.resistance_l,
  ]
    .filter(isPositiveNumber)
    .map(roundPrice)
    .filter(level => level > buyPrice)
    .sort((a, b) => a - b)[0]

  return roundPrice(resistance ? Math.min(target, resistance) : target)
}

function calcWeightedCost(quantity: number, cost: number, addQty: number, addPrice: number) {
  const afterQty = quantity + addQty
  const afterCost = afterQty > 0 ? ((quantity * cost) + (addQty * addPrice)) / afterQty : 0
  return { afterQty, afterCost }
}

export function buildRollingCostPlan(input: BuildRollingCostPlanInput): RollingCostPlan {
  const warnings: string[] = []
  const currentQuantity = Math.max(0, Number(input.currentQuantity) || 0)
  const currentCost = Math.max(0, Number(input.currentCost) || 0)
  const currentPrice = isPositiveNumber(input.currentPrice) ? input.currentPrice : null
  const requestedBaseRatio = input.baseRatio ?? 0.5
  const baseRatio = Math.min(0.9, Math.max(0.1, requestedBaseRatio))
  const requestedTranches = Math.round(input.tranches ?? 3)
  const trancheCount = Math.min(4, Math.max(1, requestedTranches))
  const reboundPct = Math.max(1, input.reboundPct ?? 5)
  const hasHolding = currentQuantity > 0 && currentCost > 0
  const mode: RollingCostPlan['mode'] = hasHolding ? 'rolling' : 'entry'
  const baseQty = hasHolding ? Math.round(currentQuantity * baseRatio) : 0
  const rollingQty = hasHolding ? Math.max(0, currentQuantity - baseQty) : 0

  if (baseRatio !== requestedBaseRatio) warnings.push('底仓比例已限制在10%-90%之间。')
  if (trancheCount !== requestedTranches) warnings.push('低吸档数已限制在1-4档之间。')

  if (!currentPrice) {
    warnings.push('缺少当前价，暂无法生成滚动低吸档。')
    return {
      mode,
      currentQuantity,
      currentCost,
      currentPrice,
      baseRatio,
      baseQty,
      rollingQty,
      trancheAmount: Math.max(0, input.trancheAmount ?? 0),
      tranches: [],
      warnings,
    }
  }

  if (hasHolding && currentPrice >= currentCost) {
    warnings.push('当前价高于持仓成本，按策略不追高，底仓持有并等待新的回撤区间。')
    return {
      mode,
      currentQuantity,
      currentCost,
      currentPrice,
      baseRatio,
      baseQty,
      rollingQty,
      trancheAmount: Math.max(0, input.trancheAmount ?? 0),
      tranches: [],
      warnings,
    }
  }

  const trancheAmount = Math.max(
    0,
    input.trancheAmount ?? (hasHolding ? (currentQuantity * currentPrice * (1 - baseRatio)) / trancheCount : currentPrice * 100),
  )
  if (!(trancheAmount > 0)) warnings.push('机动资金不足，无法生成有效低吸档。')

  const { levels, usedFallback } = buildSupportLevels(currentPrice, trancheCount, input.kline)
  if (usedFallback) warnings.push(`缺少有效K线支撑位，已按当前价下方${formatFallbackDrawdowns(trancheCount)}生成低吸档。`)

  let runningQty = currentQuantity
  let runningCost = hasHolding ? currentCost : 0
  const tranches: RollingCostTranche[] = []

  for (const [idx, buyPrice] of levels.entries()) {
    const addQty = roundQtyForMarket(trancheAmount / buyPrice, input.market)
    if (!(addQty > 0)) continue
    if (input.market === 'CN' && addQty * buyPrice < trancheAmount) {
      if (!warnings.includes('A股按100股一手取整，实际投入可能低于单档预算。')) {
        warnings.push('A股按100股一手取整，实际投入可能低于单档预算。')
      }
    }

    const beforeQty = runningQty
    const beforeCost = runningCost
    const { afterQty, afterCost } = calcWeightedCost(runningQty, runningCost, addQty, buyPrice)
    const sellPrice = resolveSellPrice(buyPrice, reboundPct, input.kline)
    const afterSellQty = Math.max(0, afterQty - addQty)
    const afterSellInvested = (afterQty * afterCost) - (addQty * sellPrice)
    const afterSellCost = afterSellQty > 0 ? Math.max(0, afterSellInvested / afterSellQty) : 0
    const costDilution = beforeCost - afterCost
    const costDilutionPct = beforeCost > 0 ? (costDilution / beforeCost) * 100 : 0

    tranches.push({
      index: idx + 1,
      buyPrice,
      addQty,
      addAmount: roundPrice(addQty * buyPrice),
      beforeQty,
      beforeCost,
      afterBuyQty: afterQty,
      afterBuyCost: afterCost,
      costDilution,
      costDilutionPct,
      sellPrice,
      sellQty: addQty,
      afterSellQty,
      afterSellCost,
      baseQtyAfterSell: baseQty,
    })

    runningQty = afterQty
    runningCost = afterCost
  }

  if (tranches.length === 0 && trancheAmount > 0) {
    warnings.push('单档预算不足以形成有效股数，建议提高机动资金或减少档位。')
  }

  return {
    mode,
    currentQuantity,
    currentCost,
    currentPrice,
    baseRatio,
    baseQty,
    rollingQty,
    trancheAmount,
    tranches,
    warnings,
  }
}

export function buildRollingCostPlanBrief(plan: RollingCostPlan): string | null {
  const next = plan.tranches[0]
  if (!next) return null
  return `滚动计划：下一档 ${next.buyPrice.toFixed(2)}，测算成本 ${next.afterBuyCost.toFixed(2)}，踢出 ${next.sellPrice.toFixed(2)}`
}
