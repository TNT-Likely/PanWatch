import { afterEach, describe, expect, it, vi } from 'vitest'
import { tradingAgentsApi } from '@panwatch/api/tradingagents'

describe('deep analysis report metadata', () => {
  afterEach(() => vi.restoreAllMocks())
  it.each(['latest', 'history'])('keeps the analysis date separate from generated time for %s', async kind => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ code: 0, data: { content: 'Report', analysis_date: '2026-10-01', created_at: '2026-10-01T08:00:00+08:00', updated_at: '2026-10-01T15:30:00+08:00' } }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    const report = kind === 'latest' ? await tradingAgentsApi.getLatestForStock('601238') : await tradingAgentsApi.getAnalysisByDate('601238', '2026-10-01')
    expect(report?.analysis_date).toBe('2026-10-01')
    expect(report?.generated_at).toBe('2026-10-01T15:30:00+08:00')
  })
})
