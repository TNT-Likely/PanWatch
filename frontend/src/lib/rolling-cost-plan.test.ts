import { describe, expect, it } from 'vitest'
import { buildRollingCostPlan } from './rolling-cost-plan'

describe('buildRollingCostPlan', () => {
  it('已有持仓亏损时生成三档低吸并逐档降低综合成本', () => {
    const plan = buildRollingCostPlan({
      market: 'CN',
      currentQuantity: 1000,
      currentCost: 10,
      currentPrice: 8,
      kline: {
        support: 7.8,
        support_s: 7.2,
        support_m: 6.6,
        resistance: 8.6,
      },
      tranches: 3,
      trancheAmount: 1600,
      baseRatio: 0.5,
      reboundPct: 5,
    })

    expect(plan.mode).toBe('rolling')
    expect(plan.baseQty).toBe(500)
    expect(plan.rollingQty).toBe(500)
    expect(plan.tranches).toHaveLength(3)
    expect(plan.tranches[0].buyPrice).toBe(7.8)
    expect(plan.tranches[0].addQty).toBe(200)
    expect(plan.tranches[0].afterBuyCost).toBeLessThan(10)
    expect(plan.tranches[1].afterBuyCost).toBeLessThan(plan.tranches[0].afterBuyCost)
    expect(plan.tranches[2].afterBuyCost).toBeLessThan(plan.tranches[1].afterBuyCost)
  })

  it('反弹踢出只卖机动仓且底仓数量保持不变', () => {
    const plan = buildRollingCostPlan({
      market: 'CN',
      currentQuantity: 1000,
      currentCost: 10,
      currentPrice: 8,
      kline: { support: 7.8, resistance: 8.4 },
      tranches: 1,
      trancheAmount: 1600,
      baseRatio: 0.5,
      reboundPct: 5,
    })

    expect(plan.tranches).toHaveLength(1)
    expect(plan.tranches[0].sellQty).toBe(plan.tranches[0].addQty)
    expect(plan.tranches[0].afterSellQty).toBe(1000)
    expect(plan.tranches[0].baseQtyAfterSell).toBe(500)
  })

  it('A 股加仓数量按 100 股取整', () => {
    const plan = buildRollingCostPlan({
      market: 'CN',
      currentQuantity: 1000,
      currentCost: 10,
      currentPrice: 8,
      kline: { support: 7.7 },
      tranches: 1,
      trancheAmount: 1300,
      baseRatio: 0.5,
    })

    expect(plan.tranches[0].addQty).toBe(100)
    expect(plan.warnings).toContain('A股按100股一手取整，实际投入可能低于单档预算。')
  })

  it('当前价格高于成本时不生成追高档位并提示上涨不理它', () => {
    const plan = buildRollingCostPlan({
      market: 'US',
      currentQuantity: 10,
      currentCost: 10,
      currentPrice: 12,
      kline: { support: 11.5, resistance: 13 },
      tranches: 3,
      trancheAmount: 1000,
      baseRatio: 0.5,
    })

    expect(plan.tranches).toHaveLength(0)
    expect(plan.warnings).toContain('当前价高于持仓成本，按策略不追高，底仓持有并等待新的回撤区间。')
  })

  it('缺少 K 线支撑时回退到百分比低吸档位', () => {
    const plan = buildRollingCostPlan({
      market: 'US',
      currentQuantity: 100,
      currentCost: 10,
      currentPrice: 8,
      kline: null,
      tranches: 3,
      trancheAmount: 100,
      baseRatio: 0.5,
    })

    expect(plan.tranches.map(item => Number(item.buyPrice.toFixed(2)))).toEqual([7.6, 7.2, 6.8])
    expect(plan.warnings).toContain('缺少有效K线支撑位，已按当前价下方5%/10%/15%生成低吸档。')
  })
})
