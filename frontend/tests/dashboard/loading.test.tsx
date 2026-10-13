import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { dashboardApi, homeApi, portfolioApi } from '@panwatch/api'
import DashboardPage from '@/pages/Dashboard'
import { GuidedTourProvider } from '@/components/onboarding/GuidedTour'
import { onboardingApi, type SetupStatus } from '@panwatch/api/onboarding'

vi.mock('@panwatch/api', () => ({
  dashboardApi: {
    indices: vi.fn(), intradaySnapshot: vi.fn(), overview: vi.fn(), portfolioSummary: vi.fn(),
    marketStatus: vi.fn(), brief: vi.fn(), curate: vi.fn(),
  },
  portfolioApi: { diagnostics: vi.fn(), benchmark: vi.fn(), attribution: vi.fn() },
  homeApi: { alertHitsToday: vi.fn(), todos: vi.fn() },
}))
vi.mock('@/components/DiscoveryPanel', () => ({ default: () => null }))
vi.mock('@panwatch/api/onboarding', () => ({ onboardingApi: { status: vi.fn(), update: vi.fn() } }))

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
const opportunity = { symbol: 'SH600519', market: 'CN', rank: 1, rank_percentile: 100, raw_score: -0.02, rank_change: null, tradable: true, reference_close: 10, factor_inputs: {}, explanation_kind: 'processed_inputs' }
const overview = { generated_at: '2026-10-13', market: 'ALL' as const, selection: { items: [opportunity], run: null, status: 'ready', mode: 'research' }, insights: [] }
const benchmark = { excess_return: 1.25, portfolio_return: 2, benchmark_return: 0.75,
  curve: [{ date: '2026-10-01', portfolio: 100, benchmark: 100 }, { date: '2026-10-02', portfolio: 102, benchmark: 100.75 }] }

beforeEach(() => {
  vi.resetAllMocks()
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  localStorage.setItem('panwatch_onboarding_completed', 'true')
  for (const api of [dashboardApi, homeApi, portfolioApi]) {
    for (const method of Object.values(api)) vi.mocked(method).mockReturnValue(new Promise(() => {}))
  }
  vi.mocked(dashboardApi.curate).mockResolvedValue({ items: [] })
})
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals() })

describe('dashboard progressive loading', () => {
  it('renders the dashboard without flashing setup UI while completion status loads or after a reload', async () => {
    const saved: SetupStatus = { goal: 'ai', active_track: 'main', tracks: [], started: true, deferred: true,
      tour_step: 'done', selected_stock: null, models: [], channels: [], quote: null, analysis: null,
      steps: [], completed: true, completed_count: 4, required_count: 4 }
    const status = deferred<SetupStatus>()
    vi.mocked(onboardingApi.status).mockReturnValue(status.promise)
    const view = () => <MemoryRouter><GuidedTourProvider><DashboardPage /></GuidedTourProvider></MemoryRouter>
    const first = render(view())
    const expectNoGuide = () => {
      expect(screen.queryByRole('region', { name: '开始使用' })).toBeNull()
      expect(screen.queryByRole('link', { name: '查看开始指南' })).toBeNull()
      expect(screen.queryByRole('button', { name: '稍后处理' })).toBeNull()
    }
    expectNoGuide()
    await act(async () => status.resolve(saved))
    expectNoGuide()
    first.unmount()
    const reloadStatus = deferred<SetupStatus>()
    vi.mocked(onboardingApi.status).mockReturnValue(reloadStatus.promise)
    render(view())
    expectNoGuide()
    await act(async () => reloadStatus.resolve(saved))
    expectNoGuide()
  })
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
    expect(screen.getAllByText('SH600519').length).toBeGreaterThan(0)
    expect(dashboardApi.curate).not.toHaveBeenCalled()
    await act(async () => { snapshot.resolve({ stocks: [] }); alerts.resolve([]) })
    await waitFor(() => expect(dashboardApi.curate).toHaveBeenCalledTimes(1))
  })

  it('keeps selection empty when overview fails and never falls back to old scores', async () => {
    vi.mocked(dashboardApi.overview).mockRejectedValue(new Error('offline'))
    render(<MemoryRouter><DashboardPage /></MemoryRouter>)
    expect(await screen.findByText('暂无研究产物。请先导入数据、运行实验，再审批研究模型。')).toBeTruthy()
    expect(screen.queryByText('SH600519')).toBeNull()
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
