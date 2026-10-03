import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { dashboardApi, discoveryApi } from '@panwatch/api'
import DiscoveryPanel from '@/components/DiscoveryPanel'

vi.mock('@panwatch/api', () => ({
  dashboardApi: { watchlist: vi.fn(), portfolioSummary: vi.fn() },
  discoveryApi: { listHotBoards: vi.fn(), listHotStocks: vi.fn() },
}))
beforeEach(() => {
  localStorage.clear()
  vi.resetAllMocks()
  vi.mocked(dashboardApi.watchlist).mockResolvedValue([])
  vi.mocked(discoveryApi.listHotBoards).mockResolvedValue([{ code: 'BK1', name: '半导体板块', change_pct: 2 }])
  vi.mocked(discoveryApi.listHotStocks).mockResolvedValue([{ symbol: '600519', market: 'CN', name: '持仓股票', change_pct: 2 }])
})
afterEach(() => { cleanup(); localStorage.clear() })

it('loads only the visible discovery tab and reuses the parent portfolio for personalization', async () => {
  const summary = { accounts: [{ positions: [{ symbol: '600519', market: 'CN', trading_style: 'long' }] }] }
  render(<MemoryRouter><DiscoveryPanel monitorStocks={[]} portfolioSummary={summary} onOpenStock={vi.fn()} /></MemoryRouter>)
  expect(await screen.findByText('半导体板块')).toBeTruthy()
  expect(discoveryApi.listHotBoards).toHaveBeenCalledTimes(1)
  expect(discoveryApi.listHotStocks).not.toHaveBeenCalled()
  expect(dashboardApi.watchlist).not.toHaveBeenCalled()
  expect(dashboardApi.portfolioSummary).not.toHaveBeenCalled()

  await userEvent.click(screen.getByRole('button', { name: '热门股票' }))
  expect(await screen.findByText('持仓股票')).toBeTruthy()
  await waitFor(() => expect(dashboardApi.watchlist).toHaveBeenCalledTimes(1))
  expect(discoveryApi.listHotStocks).toHaveBeenCalledTimes(2)
  expect(dashboardApi.portfolioSummary).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: '热门板块' }))
  expect(await screen.findByText('半导体板块')).toBeTruthy()
  expect(discoveryApi.listHotBoards).toHaveBeenCalledTimes(1)
})
