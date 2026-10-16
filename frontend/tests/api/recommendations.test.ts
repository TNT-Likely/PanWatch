import { beforeEach, describe, expect, it, vi } from 'vitest'

const { fetchAPI } = vi.hoisted(() => ({ fetchAPI: vi.fn() }))
vi.mock('../../packages/api/src/client', () => ({ fetchAPI }))

import { recommendationsApi } from '../../packages/api/src/recommendations'

describe('entry candidate reads and refresh commands', () => {
  beforeEach(() => {
    fetchAPI.mockReset()
    fetchAPI.mockResolvedValue({})
  })

  it('lists saved candidates without a refresh action in the GET query', async () => {
    await recommendationsApi.listEntryCandidates({ market: 'US', limit: 20, timeoutMs: 90000 })
    expect(fetchAPI).toHaveBeenCalledWith(
      '/recommendations/entry-candidates?market=US&limit=20',
      { timeoutMs: 90000 },
    )
  })

  it('cannot forward an obsolete refresh flag from a JavaScript caller into a GET', async () => {
    const legacyParams = { market: 'CN', refresh: true }
    await recommendationsApi.listEntryCandidates(legacyParams)
    expect(fetchAPI).toHaveBeenCalledWith('/recommendations/entry-candidates?market=CN', { timeoutMs: undefined })
  })

  it('uses the existing POST command for explicit refresh, then separately reads results', async () => {
    await recommendationsApi.refreshEntryCandidates(40, 25)
    await recommendationsApi.listEntryCandidates({ market: 'US' })
    expect(fetchAPI.mock.calls).toEqual([
      ['/recommendations/entry-candidates/refresh?max_inputs=40&market_scan_limit=25', { method: 'POST' }],
      ['/recommendations/entry-candidates?market=US', { timeoutMs: undefined }],
    ])
  })
})
