import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import DashboardPage, { moveColor, pctChipCls } from '@/pages/Dashboard'

const {
  dashboardApi,
  portfolioApi,
  recommendationsApi,
  homeApi,
} = vi.hoisted(() => ({
  dashboardApi: {
    indices: vi.fn(),
    intradayScan: vi.fn(),
    overview: vi.fn(),
    portfolioSummary: vi.fn(),
    marketStatus: vi.fn(),
    brief: vi.fn(),
    curate: vi.fn(),
    watchlist: vi.fn(),
  },
  portfolioApi: {
    diagnostics: vi.fn(),
    benchmark: vi.fn(),
    attribution: vi.fn(),
    aiReview: vi.fn(),
  },
  recommendationsApi: {
    listStrategySignals: vi.fn(),
  },
  homeApi: {
    alertHitsToday: vi.fn(),
    todos: vi.fn(),
  },
}))

vi.mock('@panwatch/api', () => ({
  dashboardApi,
  portfolioApi,
  recommendationsApi,
  homeApi,
}))

vi.mock('@panwatch/biz-ui/components/onboarding', () => ({
  Onboarding: () => null,
}))

vi.mock('@panwatch/biz-ui/components/stock-insight-modal', () => ({
  default: () => null,
}))

vi.mock('@/components/DiscoveryPanel', () => ({
  default: () => null,
}))

vi.mock('@/components/Sparkline', () => ({
  default: () => null,
}))

vi.mock('@/components/BenchChart', () => ({
  default: () => null,
}))

vi.mock('@/components/BenchmarkShareCard', () => ({
  default: () => null,
}))

vi.mock('@/components/DiagnosticsShareCard', () => ({
  default: () => null,
}))

vi.mock('@/components/DigestShareCard', () => ({
  default: () => null,
}))

function resetMocks() {
  dashboardApi.indices.mockResolvedValue([])
  dashboardApi.intradayScan.mockResolvedValue({ stocks: [] })
  dashboardApi.overview.mockResolvedValue({
    kpis: { watchlist_count: 0 },
    action_center: { opportunities: [] },
  })
  dashboardApi.portfolioSummary.mockResolvedValue(null)
  dashboardApi.marketStatus.mockResolvedValue([])
  dashboardApi.brief.mockResolvedValue({ empty: true })
  dashboardApi.curate.mockResolvedValue({ items: [] })
  dashboardApi.watchlist.mockResolvedValue([])
  portfolioApi.diagnostics.mockResolvedValue({
    position_count: 0,
    total_market_value: 0,
    total_unrealized_pnl: 0,
    max_weight: 0,
    by_market: {},
    alerts: [],
  })
  portfolioApi.benchmark.mockResolvedValue({ empty: true })
  portfolioApi.attribution.mockResolvedValue({ items: [] })
  recommendationsApi.listStrategySignals.mockResolvedValue({ items: [] })
  homeApi.alertHitsToday.mockResolvedValue([])
  homeApi.todos.mockResolvedValue([])
}

describe('首页精致化 · 涨跌 token 映射', () => {
  it('红涨绿跌走 stock token，空值/平盘为 muted', () => {
    expect(moveColor(1.2)).toBe('text-stock-up')
    expect(moveColor(-0.8)).toBe('text-stock-down')
    expect(moveColor(0)).toBe('text-muted-foreground')
    expect(moveColor(null)).toBe('text-muted-foreground')

    expect(pctChipCls(2)).toBe('chip-up')
    expect(pctChipCls(-2)).toBe('chip-down')
    expect(pctChipCls(0)).toBe('chip-muted')
    expect(pctChipCls(undefined)).toBe('chip-muted')
  })
})

describe('首页精致化 · Dashboard 壳与空态', () => {
  beforeEach(() => {
    resetMocks()
  })

  it('渲染 page-container、页标题与统一空态', async () => {
    const { container } = render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    )

    expect(container.querySelector('.page-container')).toBeTruthy()
    expect(screen.getByRole('heading', { name: '今日该看什么' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '今日要紧事' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '组合体检' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '机会精选' })).toBeTruthy()

    await waitFor(() => {
      expect(screen.getByText(/暂无持仓,添加持仓后这里展示今日盈亏与组合走势/)).toBeTruthy()
    })

    const emptyHints = container.querySelectorAll('.empty-hint')
    expect(emptyHints.length).toBeGreaterThan(0)
  })
})
