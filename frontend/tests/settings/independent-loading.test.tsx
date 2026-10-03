import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { fetchAPI, patsApi, paperTradingApi } from '@panwatch/api'
import SettingsPage from '@/pages/Settings'
import AgentsPage from '@/pages/Agents'
import PaperTrading from '@/pages/PaperTrading'
import { MarketColorProvider } from '@/hooks/use-market-colors'

vi.mock('@panwatch/api', async original => ({ ...await original<typeof import('@panwatch/api')>(), fetchAPI: vi.fn() }))
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.clearAllMocks(); localStorage.clear() })
const pending = () => new Promise<never>(() => {})
const wrap = (child: React.ReactNode) => <MemoryRouter><MarketColorProvider>{child}</MarketColorProvider></MemoryRouter>

it('shows settings and channels while provider and health requests are still pending', async () => {
  vi.spyOn(patsApi, 'list').mockResolvedValue({ items: [] })
  vi.mocked(fetchAPI).mockImplementation(async path => {
    if (path === '/settings') return [{ key: 'http_proxy', value: '', description: '代理配置' }]
    if (path === '/channels') return [{ id: 1, name: '独立加载的通知渠道', type: 'bark', enabled: true, config: {} }]
    if (path === '/settings/avatar') return { value: '' }
    return pending()
  })
  render(wrap(<SettingsPage />))
  expect(await screen.findByText('独立加载的通知渠道')).toBeTruthy()
  expect(document.getElementById('sec-system')).toBeTruthy()
  expect(document.getElementById('sec-feedback')).toBeNull()
  expect(vi.mocked(fetchAPI).mock.calls.some(([path]) => path.includes('/feedback'))).toBe(false)
})

it('shows the agent list without waiting for models or schedule previews', async () => {
  vi.mocked(fetchAPI).mockImplementation(async path => {
    if (path === '/agents') return [{ id: 1, name: 'premarket_outlook', display_name: '盘前分析', enabled: true, schedule: '0 9 * * 1-5', config: {} }]
    if (path === '/stocks') return []
    return pending()
  })
  render(wrap(<AgentsPage />))
  expect(await screen.findByText('盘前分析')).toBeTruthy()
  await waitFor(() => expect(vi.mocked(fetchAPI).mock.calls.some(([path]) => path.includes('/schedule/preview'))).toBe(true))
})

it('paginates paper trades without reloading account, positions, or slow metrics', async () => {
  const account = vi.spyOn(paperTradingApi, 'getAccount').mockResolvedValue({ enabled: false, initial_capital: 100000, cash: 100000, total_equity: 100000, total_return_pct: 0, unrealized_pnl: 0, realized_pnl: 0, total_pnl: 0, current_capital: 100000, win_rate: 0, winning_trades: 0, total_trades: 0, max_drawdown_pct: 0 } as never)
  const positions = vi.spyOn(paperTradingApi, 'listPositions').mockResolvedValue([])
  const metrics = vi.spyOn(paperTradingApi, 'getMetrics').mockImplementation(pending)
  const trades = vi.spyOn(paperTradingApi, 'listTrades').mockResolvedValue({ items: [{ id: 1, stock_symbol: '601238', stock_name: '广汽集团', stock_market: 'CN', entry_price: 5, exit_price: 6, pnl: 100, pnl_pct: 20, exit_reason: 'manual', holding_days: 3 } as never], total: 40 })
  render(wrap(<PaperTrading />))
  await userEvent.click(await screen.findByRole('button', { name: /平仓.*40/ }))
  await userEvent.click(screen.getByRole('button', { name: '下一页' }))
  await waitFor(() => expect(trades).toHaveBeenCalledWith(20, 20, undefined))
  expect(account).toHaveBeenCalledTimes(1)
  expect(positions).toHaveBeenCalledTimes(1)
  expect(metrics).toHaveBeenCalledTimes(1)
})
