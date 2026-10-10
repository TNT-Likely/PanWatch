import { afterEach, expect, it, vi } from 'vitest'
import { dashboardApi } from '@panwatch/api'
import { fetchAPI } from '@panwatch/api/client'

vi.mock('@panwatch/api/client', () => ({ fetchAPI: vi.fn() }))
afterEach(() => vi.resetAllMocks())
it('uses a bounded cold-refresh budget without changing the legacy endpoint', () => {
  dashboardApi.marketOverview()
  expect(fetchAPI).toHaveBeenCalledWith('/market/overview', { timeoutMs: 60000 })
  dashboardApi.indices()
  expect(fetchAPI).toHaveBeenLastCalledWith('/market/indices')
})
