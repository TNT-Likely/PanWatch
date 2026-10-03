import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { dashboardApi, homeApi, portfolioApi, recommendationsApi } from '@panwatch/api'
import DashboardPage from '@/pages/Dashboard'

vi.mock('@panwatch/api', () => ({
  dashboardApi: {
    indices: vi.fn(), intradaySnapshot: vi.fn(), overview: vi.fn(), portfolioSummary: vi.fn(),
    marketStatus: vi.fn(), brief: vi.fn(), curate: vi.fn(),
  },
  portfolioApi: { diagnostics: vi.fn(), benchmark: vi.fn(), attribution: vi.fn() },
  homeApi: { alertHitsToday: vi.fn(), todos: vi.fn() },
  recommendationsApi: { listStrategySignals: vi.fn() },
}))
vi.mock('@/components/DiscoveryPanel', () => ({ default: () => null }))
vi.mock('@panwatch/biz-ui/components/onboarding', () => ({ Onboarding: () => null }))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

const diagnostics = {
  position_count: 2, total_market_value: 10000, total_unrealized_pnl: 1000,
  hhi: 0.5, max_weight: 0.5, by_market: { CN: 10000 }, by_strategy: {}, alerts: [],
}
const summary = { accounts: [], total: {
  total_market_value: 10000, total_cost: 9000, total_pnl: 1000, total_pnl_pct: 11,
  total_daily_pnl: 321, available_funds: 2000, total_assets: 12000,
} }
const opportunity = { stock_symbol: '600519', stock_name: '机会标的', stock_market: 'CN', signal: '机会已就绪' }
const overview = { kpis: { watchlist_count: 2 }, action_center: { opportunities: [opportunity] } }
const benchmark = { excess_return: 1.25, portfolio_return: 2, benchmark_return: 0.75,
  curve: [{ date: '2026-10-01', portfolio: 100, benchmark: 100 }, { date: '2026-10-02', portfolio: 102, benchmark: 100.75 }] }

beforeEach(() => {
  vi.resetAllMocks()
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  localStorage.setItem('panwatch_onboarding_completed', 'true')
  for (const api of [dashboardApi, homeApi, portfolioApi, recommendationsApi]) {
    for (const method of Object.values(api)) vi.mocked(method).mockReturnValue(new Promise(() => {}))
  }
  vi.mocked(dashboardApi.curate).mockResolvedValue({ items: [] })
})
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals() })

describe('dashboard progressive loading', () => {
  it('renders fast data and the benchmark before a slow quote snapshot or attribution, then curates once', async () => {
    const snapshot = deferred<{ stocks: [] }>()
    const alerts = deferred<[]>()
    const diag = deferred<typeof diagnostics>()
    const ps = deferred<typeof summary>()
    const ov = deferred<typeof overview>()
    const bn = deferred<typeof benchmark>()
    vi.mocked(portfolioApi.diagnostics).mockReturnValue(diag.promise)
    vi.mocked(dashboardApi.portfolioSummary).mockReturnValue(ps.promise)
    vi.mocked(dashboardApi.overview).mockReturnValue(ov.promise)
    vi.mocked(portfolioApi.benchmark).mockReturnValue(bn.promise)
    vi.mocked(dashboardApi.intradaySnapshot).mockReturnValue(snapshot.promise)
    vi.mocked(homeApi.alertHitsToday).mockReturnValue(alerts.promise)
    vi.mocked(homeApi.todos).mockResolvedValue({ todos: [{ type: 'no_alert', name: '待办标的', symbol: '000001', market: 'CN' }] })
    vi.mocked(dashboardApi.marketStatus).mockResolvedValue([{ code: 'HK', is_trading: false }])
    vi.mocked(dashboardApi.brief).mockImplementation(type => type === 'premarket'
      ? Promise.resolve({ type, title: '快速简报', content: '已保存的报告', updated_at: '2026-10-03' })
      : new Promise(() => {}))
    render(<MemoryRouter><DashboardPage /></MemoryRouter>)
    expect(await screen.findByText(/待办标的/)).toBeTruthy()
    expect(screen.getByText('快速简报')).toBeTruthy()
    expect(screen.getByText('港股')).toBeTruthy()
    expect(portfolioApi.benchmark).toHaveBeenCalledTimes(1)
    expect(dashboardApi.intradaySnapshot).toHaveBeenCalledWith()

    // Resolve the other sections while snapshot/attribution/second brief remain pending.
    // Each result must become visible without waiting for the whole group.
    await act(async () => { diag.resolve(diagnostics); ps.resolve(summary); bn.resolve(benchmark); ov.resolve(overview) })
    expect(screen.getByText('+¥321')).toBeTruthy()
    expect(screen.getAllByText(/\+1.25%/).length).toBeGreaterThan(0)
    expect(screen.getAllByText('机会标的').length).toBeGreaterThan(0)
    expect(dashboardApi.curate).not.toHaveBeenCalled()
    await act(async () => { snapshot.resolve({ stocks: [] }); alerts.resolve([]) })
    await waitFor(() => expect(dashboardApi.curate).toHaveBeenCalledTimes(1))
  })

  it('loads fallback opportunities immediately if overview fails without waiting for quotes', async () => {
    vi.mocked(dashboardApi.overview).mockRejectedValue(new Error('offline'))
    vi.mocked(recommendationsApi.listStrategySignals).mockResolvedValue({ items: [opportunity] })
    render(<MemoryRouter><DashboardPage /></MemoryRouter>)
    expect((await screen.findAllByText('机会标的')).length).toBeGreaterThan(0)
    expect(recommendationsApi.listStrategySignals).toHaveBeenCalledTimes(1)
    expect(dashboardApi.curate).not.toHaveBeenCalled()
  })

  it('ignores the old benchmark response after a newer refresh finishes', async () => {
    const oldBenchmark = deferred<typeof benchmark>()
    vi.mocked(portfolioApi.benchmark).mockReturnValueOnce(oldBenchmark.promise)
      .mockResolvedValue({ ...benchmark, excess_return: 9 })
    vi.mocked(portfolioApi.diagnostics).mockResolvedValue(diagnostics)
    vi.mocked(dashboardApi.portfolioSummary).mockResolvedValue(summary)
    vi.mocked(dashboardApi.overview).mockResolvedValue(overview)
    vi.mocked(dashboardApi.intradaySnapshot).mockResolvedValue({ stocks: [] })
    vi.mocked(homeApi.alertHitsToday).mockResolvedValue([])
    vi.mocked(homeApi.todos).mockResolvedValue({ todos: [] })
    vi.mocked(dashboardApi.marketStatus).mockResolvedValue([])
    const { container } = render(<MemoryRouter><DashboardPage /></MemoryRouter>)
    const refresh = container.querySelector('button')!
    await waitFor(() => expect(refresh.disabled).toBe(false))
    await waitFor(() => expect(dashboardApi.curate).toHaveBeenCalledTimes(1))
    await userEvent.click(refresh)
    await waitFor(() => expect(screen.getAllByText(/\+9.00%/).length).toBeGreaterThan(0))
    await act(async () => { oldBenchmark.resolve({ ...benchmark, excess_return: -9 }) })
    expect(screen.queryByText(/-9.00%/)).toBeNull()
    expect(screen.getAllByText(/\+9.00%/).length).toBeGreaterThan(0)
    await waitFor(() => expect(refresh.disabled).toBe(false))
    expect(dashboardApi.curate).toHaveBeenCalledTimes(1)
  })
})
