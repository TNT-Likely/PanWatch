import { describe, expect, expectTypeOf, it, vi } from 'vitest'
import { dashboardApi, type DashboardOverviewResponse } from '@panwatch/api/dashboard'
import { fetchAPI } from '@panwatch/api/client'

vi.mock('@panwatch/api/client', () => ({ fetchAPI: vi.fn().mockResolvedValue({}) }))

describe('Canadian dashboard API contract', () => {
  it('accepts both Canadian markets in overview requests and responses', async () => {
    for (const market of ['TSX', 'TSXV'] as const) {
      expectTypeOf<typeof market>().toExtend<DashboardOverviewResponse['market']>()
      await dashboardApi.overview({ market })
      expect(fetchAPI).toHaveBeenLastCalledWith(`/dashboard/overview?market=${market}`, { timeoutMs: 45000 })
    }
  })

  it('exposes incomplete valuations at every overview aggregation level', () => {
    expectTypeOf<DashboardOverviewResponse['kpis']['valuation_incomplete']>().toEqualTypeOf<boolean | undefined>()
    expectTypeOf<DashboardOverviewResponse['portfolio']['valuation_incomplete']>().toEqualTypeOf<boolean | undefined>()
    expectTypeOf<DashboardOverviewResponse['portfolio']['by_market'][number]['valuation_incomplete']>().toEqualTypeOf<boolean | undefined>()
  })
})
