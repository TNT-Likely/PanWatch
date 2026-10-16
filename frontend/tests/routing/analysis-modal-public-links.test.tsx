import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { insightApi, stocksApi, tradingAgentsApi, type DeepAnalysisResult } from '@panwatch/api'
import { DeepAnalysisModal } from '@panwatch/biz-ui/components/deep-analysis-modal'
import StockInsightModal from '@panwatch/biz-ui/components/stock-insight-modal'
import { MarketColorProvider } from '@/hooks/use-market-colors'

vi.mock('@panwatch/api', async original => ({
  ...await original<typeof import('@panwatch/api')>(),
  insightApi: {
    quote: vi.fn(), klineSummary: vi.fn(), klines: vi.fn(), portfolioSummary: vi.fn(),
    suggestions: vi.fn(), news: vi.fn(), history: vi.fn(),
  },
  stocksApi: { list: vi.fn() },
  tradingAgentsApi: { getLatestForStock: vi.fn(), getHistoryComparison: vi.fn() },
}))
vi.mock('@panwatch/biz-ui/components/stock-price-alert-panel', () => ({ default: () => null }))
vi.mock('@panwatch/biz-ui/components/add-position-calculator', () => ({ default: () => null }))

const result = {
  agent_name: 'tradingagents', title: 'Historical analysis', content: 'Report',
  analysis_date: '2026-10-01', timestamp: '2026-10-01T15:30:00+08:00',
  raw_data: { suggestion: { action: 'hold', confidence: 7 }, final_decision: 'Report' },
} as DeepAnalysisResult

const listings = [
  { market: 'CN', symbol: '601238', routeSymbol: '601238' },
  { market: 'HK', symbol: '00700', routeSymbol: '00700' },
  { market: 'US', symbol: 'AAPL', routeSymbol: 'AAPL' },
]

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.setItem('stock_insight_auto_refresh_enabled', 'false')
  vi.mocked(stocksApi.list).mockResolvedValue([])
  vi.mocked(insightApi.quote).mockResolvedValue(null)
  vi.mocked(insightApi.klineSummary).mockResolvedValue({ summary: null })
  vi.mocked(insightApi.klines).mockResolvedValue({ klines: [] })
  vi.mocked(insightApi.portfolioSummary).mockResolvedValue({ accounts: [] })
  vi.mocked(insightApi.suggestions).mockResolvedValue([])
  vi.mocked(insightApi.news).mockResolvedValue([])
  vi.mocked(insightApi.history).mockResolvedValue([])
  vi.mocked(tradingAgentsApi.getLatestForStock).mockResolvedValue(result)
  vi.mocked(tradingAgentsApi.getHistoryComparison).mockResolvedValue({
    items: [],
    stats: {
      total: 0, buy_count: 0, sell_count: 0, hold_count: 0,
      buy_hit_rate: null, sell_hit_rate: null, hold_hit_rate: null,
      overall_hit_rate: null, avg_return_20d_pct: null,
    },
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  localStorage.clear()
  document.querySelector('meta[name="panwatch-base-path"]')?.remove()
  window.history.replaceState(null, '', '/')
})

describe.each(['', '/panwatch', '/apps/panwatch'])('analysis modal links at public base "%s"', prefix => {
  beforeEach(() => {
    const meta = document.createElement('meta')
    meta.name = 'panwatch-base-path'
    meta.content = prefix
    document.head.append(meta)
    window.history.replaceState(null, '', `${prefix}/stocks`)
  })

  it.each(listings)('opens the deep analysis detail page for $market within the public base', async ({ symbol, routeSymbol }) => {
    const user = userEvent.setup()
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    render(
      <BrowserRouter basename={prefix || '/'}>
        <DeepAnalysisModal open onOpenChange={vi.fn()} stockId={1} stockName="Test stock"
          stockSymbol={symbol} initialResult={result} />
      </BrowserRouter>,
    )

    await user.click(await screen.findByRole('button', { name: '查看详细页' }))

    expect(open).toHaveBeenCalledExactlyOnceWith(`${prefix}/analysis/${routeSymbol}/2026-10-01`, '_blank')
    expect(window.location.pathname).toBe(`${prefix}/stocks`)
  })

  it.each(listings)('opens the stock insight detail page for $market within the public base', async ({ market, symbol, routeSymbol }) => {
    const user = userEvent.setup()
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    render(
      <BrowserRouter basename={prefix || '/'}>
        <MarketColorProvider>
          <StockInsightModal open onOpenChange={vi.fn()} symbol={symbol} market={market} stockName="Test stock" />
        </MarketColorProvider>
      </BrowserRouter>,
    )

    await user.click(screen.getByRole('button', { name: '深度' }))
    await user.click(await screen.findByRole('button', { name: '打开详情页 ↗' }))

    expect(open).toHaveBeenCalledExactlyOnceWith(`${prefix}/analysis/${routeSymbol}/2026-10-01`, '_blank')
    expect(window.location.pathname).toBe(`${prefix}/stocks`)
  })
})
