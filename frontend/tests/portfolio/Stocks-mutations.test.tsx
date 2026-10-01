import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchAPI } from '@panwatch/api'
import { klinesApi } from '@panwatch/api/klines'
import StocksPage from '@/pages/Stocks'
import { MarketColorProvider } from '@/hooks/use-market-colors'

vi.mock('@panwatch/api', async importOriginal => ({
  ...await importOriginal<typeof import('@panwatch/api')>(),
  fetchAPI: vi.fn(),
}))
vi.mock('@panwatch/api/klines', () => ({ klinesApi: { summaryBatch: vi.fn() } }))

afterEach(() => { cleanup(); vi.clearAllMocks(); localStorage.clear() })

describe('portfolio mutations while K-line summaries are slow', () => {
  it('shows a new account without waiting for or reloading K-lines and quotes', async () => {
    let saved = false
    const account = { id: 1, name: 'New account', available_funds: 0, positions: [] }
    vi.mocked(klinesApi.summaryBatch).mockImplementation(() => new Promise(() => {}))
    vi.mocked(fetchAPI).mockImplementation(async (path, options) => {
      if (path === '/stocks') return [{ id: 1, symbol: '600519', name: 'Stock', market: 'CN', agents: [] }]
      if (path === '/accounts' && options?.method === 'POST') { saved = true; return account }
      if (path.startsWith('/portfolio/summary')) return {
        accounts: saved ? [account] : [],
        total: { total_market_value: 0, total_cost: 0, total_pnl: 0,
          total_pnl_pct: 0, total_daily_pnl: 0, available_funds: 0, total_assets: 0 },
      }
      if (path === '/quotes/batch') return [{ symbol: '600519', market: 'CN', current_price: 10, change_pct: null, daily_move_status: 'closed' }]
      if (path === '/stocks/markets/status') return [{ code: 'CN', status: 'closed', status_text: '休市', is_trading: false, sessions: [], local_time: '12:00' }]
      if (path.startsWith('/suggestions')) return {}
      if (path === '/price-alerts') return []
      throw new Error(`Unexpected request: ${path}`)
    })
    const user = userEvent.setup()
    render(<MemoryRouter><MarketColorProvider><StocksPage /></MarketColorProvider></MemoryRouter>)
    await waitFor(() => expect(klinesApi.summaryBatch).toHaveBeenCalledTimes(1))
    const quotesBefore = vi.mocked(fetchAPI).mock.calls.filter(([path]) => path === '/quotes/batch').length
    await user.click(screen.getAllByRole('button', { name: '添加账户' })[0])
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByPlaceholderText('账户名称'), 'New account')
    await user.click(within(dialog).getByRole('button', { name: '创建' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    await screen.findByText('New account')
    expect(klinesApi.summaryBatch).toHaveBeenCalledTimes(1)
    expect(vi.mocked(fetchAPI).mock.calls.filter(([path]) => path === '/quotes/batch')).toHaveLength(quotesBefore)
  })

  it('updates holding quantity using the displayed price while summaries remain pending', async () => {
    let quantity = 100
    vi.mocked(klinesApi.summaryBatch).mockImplementation(() => new Promise(() => {}))
    vi.mocked(fetchAPI).mockImplementation(async (path, options) => {
      if (path === '/stocks') return [{ id: 1, symbol: '600519', name: 'Stock', market: 'CN', agents: [] }]
      if (path === '/positions/1' && options?.method === 'PUT') {
        quantity = JSON.parse(String(options.body)).quantity
        return { id: 1 }
      }
      if (path.startsWith('/portfolio/summary')) return {
        accounts: [{ id: 1, name: 'Account', available_funds: 0, positions: [{
          id: 1, stock_id: 1, symbol: '600519', name: 'Stock', market: 'CN',
          cost_price: 8, quantity, invested_amount: null, trading_style: '',
          current_price: null, change_pct: null, daily_move_status: 'closed',
        }] }],
        total: { total_market_value: 0, total_cost: 0, total_pnl: 0,
          total_pnl_pct: 0, total_daily_pnl: 0, available_funds: 0, total_assets: 0 },
      }
      if (path === '/quotes/batch') return [{ symbol: '600519', market: 'CN', current_price: 10, change_pct: null, daily_move_status: 'closed' }]
      if (path === '/stocks/markets/status') return []
      if (path.startsWith('/suggestions')) return {}
      if (path === '/price-alerts') return []
      throw new Error(`Unexpected request: ${path}`)
    })
    const user = userEvent.setup()
    render(<MemoryRouter><MarketColorProvider><StocksPage /></MarketColorProvider></MemoryRouter>)
    await waitFor(() => expect(klinesApi.summaryBatch).toHaveBeenCalledTimes(1))
    const quotesBefore = vi.mocked(fetchAPI).mock.calls.filter(([path]) => path === '/quotes/batch').length
    await user.click(screen.getAllByRole('button', { name: '编辑持仓' })[0])
    const dialog = screen.getByRole('dialog')
    const input = within(dialog).getByPlaceholderText('0')
    await user.clear(input)
    await user.type(input, '250')
    await user.click(within(dialog).getByRole('button', { name: '保存' }))
    await waitFor(() => expect(screen.getAllByText('250').length).toBeGreaterThan(0))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getAllByText('10.00').length).toBeGreaterThan(0)
    expect(klinesApi.summaryBatch).toHaveBeenCalledTimes(1)
    expect(vi.mocked(fetchAPI).mock.calls.filter(([path]) => path === '/quotes/batch')).toHaveLength(quotesBefore)
  })
})
