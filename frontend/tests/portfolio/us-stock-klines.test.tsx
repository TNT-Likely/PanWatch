import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { insightApi } from '@panwatch/api'
import StockInsightModal from '@panwatch/biz-ui/components/stock-insight-modal'
import { MarketColorProvider } from '@/hooks/use-market-colors'

vi.mock('@panwatch/api', async (original) => ({
  ...await original<typeof import('@panwatch/api')>(),
  insightApi: {
    quote: vi.fn().mockResolvedValue({ symbol: 'LI', name: '理想汽车', market: 'US', current_price: 10.69 }),
    klineSummary: vi.fn().mockReturnValue(new Promise(() => {})),
    klines: vi.fn().mockResolvedValue({ symbol: 'LI', market: 'US', klines: [
      { date: '2026-10-01', open: 11, close: 10.9, high: 11.1, low: 10.8, volume: 1000 },
      { date: '2026-10-02', open: 10.8, close: 10.69, high: 10.85, low: 10.66, volume: 2000 },
    ] }),
    portfolioSummary: vi.fn().mockResolvedValue({ accounts: [] }),
    suggestions: vi.fn().mockResolvedValue([]), news: vi.fn().mockResolvedValue([]), history: vi.fn().mockResolvedValue([]),
  },
  stocksApi: { list: vi.fn().mockResolvedValue([]) },
}))
vi.mock('@panwatch/biz-ui/components/stock-price-alert-panel', () => ({ default: () => null }))
vi.mock('@panwatch/biz-ui/components/add-position-calculator', () => ({ default: () => null }))
afterEach(() => { cleanup(); localStorage.clear() })

it('shows US candles in the detail modal while the technical summary is still pending', async () => {
  render(<MemoryRouter><MarketColorProvider><StockInsightModal open onOpenChange={vi.fn()} symbol="LI" market="US" stockName="理想汽车" /></MarketColorProvider></MemoryRouter>)
  expect(await screen.findByTitle('点击进入交互式K线')).toBeTruthy()
  expect(insightApi.klines).toHaveBeenCalledWith('LI', { market: 'US', days: 36, interval: '1d' })
  expect(screen.queryByText('暂无K线摘要')).toBeNull()
})
