import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { fetchAPI, stocksApi } from '@panwatch/api'
import StocksPage from '@/pages/Stocks'
import { MarketColorProvider } from '@/hooks/use-market-colors'

vi.mock('@panwatch/api', async importOriginal => ({
  ...await importOriginal<typeof import('@panwatch/api')>(),
  fetchAPI: vi.fn(),
  stocksApi: { create: vi.fn() },
}))
vi.mock('@panwatch/api/klines', () => ({ klinesApi: { summaryBatch: vi.fn().mockResolvedValue({}) } }))

afterEach(() => { cleanup(); vi.clearAllMocks(); localStorage.clear() })

const total = { total_market_value: 0, total_cost: 0, total_pnl: 0, total_pnl_pct: 0, total_daily_pnl: 0, available_funds: 0, total_assets: 0 }

it.each([
  ['510300', '沪深300ETF', 'CN', 'A股', 'etf'],
  ['SPY', 'SPDR ETF', 'US', '美股', 'etf'],
  ['02800', '盈富基金', 'HK', '港股', 'stock'],
])('adds %s to the watchlist with a persistent ETF classification', async (symbol, name, market, marketLabel, searchType) => {
  let saved = false
  const instrument = { id: 1, symbol, name, market, asset_type: 'etf' as const, agents: [] }
  vi.mocked(stocksApi.create).mockImplementation(async () => { saved = true; return instrument })
  vi.mocked(fetchAPI).mockImplementation(async path => {
    if (path === '/stocks') return saved ? [instrument] : []
    if (path.startsWith('/stocks/search')) return [{ symbol, name, market, asset_type: searchType }]
    if (path.startsWith('/portfolio/summary')) return { accounts: [], total }
    if (path === '/quotes/batch') return [{ symbol, market, current_price: 4.385, change_pct: null }]
    if (path.startsWith('/suggestions')) return {}
    return []
  })
  const user = userEvent.setup()
  render(<MemoryRouter><MarketColorProvider><StocksPage /></MarketColorProvider></MemoryRouter>)
  await user.click((await screen.findAllByRole('button', { name: '添加股票 / ETF' }))[0])
  const dialog = screen.getByRole('dialog')
  await user.click(within(dialog).getByRole('button', { name: marketLabel, exact: true }))
  await user.type(within(dialog).getByPlaceholderText(/代码或名称/), symbol)
  await user.click(await within(dialog).findByRole('button', { name: new RegExp(`${symbol}.*${name}`) }))
  const type = within(dialog).getByRole('combobox', { name: '标的类型' })
  if (searchType === 'stock') {
    await user.click(type)
    await user.click(await screen.findByRole('option', { name: 'ETF', exact: true }))
  }
  expect(type.textContent).toContain('ETF')
  await user.click(within(dialog).getByRole('button', { name: '确认添加' }))
  await waitFor(() => expect(stocksApi.create).toHaveBeenCalledWith({ symbol, name, market, asset_type: 'etf' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
})

it('creates an ETF holding directly from search and keeps the fund type in the stock request', async () => {
  let stockSaved = false
  const instrument = { id: 7, symbol: '159915', name: '创业板ETF', market: 'CN', asset_type: 'etf', agents: [] }
  vi.mocked(fetchAPI).mockImplementation(async (path, options) => {
    if (path === '/stocks' && options?.method === 'POST') { stockSaved = true; return instrument }
    if (path === '/stocks') return stockSaved ? [instrument] : []
    if (path.startsWith('/stocks/search')) return [instrument]
    if (path.startsWith('/portfolio/summary')) return { accounts: [{ id: 1, name: 'ETF account', available_funds: 0, positions: [] }], total }
    if (path === '/quotes/batch') return []
    if (path.startsWith('/suggestions')) return {}
    if (path === '/positions' && options?.method === 'POST') return { id: 9 }
    return []
  })
  const user = userEvent.setup()
  render(<MemoryRouter><MarketColorProvider><StocksPage /></MarketColorProvider></MemoryRouter>)
  await user.click((await screen.findAllByRole('button', { name: '添加持仓' }))[0])
  const dialog = screen.getByRole('dialog')
  await user.type(within(dialog).getByPlaceholderText(/代码或名称/), '159915')
  await user.click(await within(dialog).findByRole('button', { name: /159915.*创业板ETF/ }))
  await user.type(within(dialog).getByPlaceholderText('0.00'), '3.064')
  await user.type(within(dialog).getByPlaceholderText('0'), '100')
  await user.click(within(dialog).getByRole('button', { name: '添加', exact: true }))
  await waitFor(() => expect(vi.mocked(fetchAPI).mock.calls.some(([path, options]) => path === '/stocks' && options?.method === 'POST' && JSON.parse(String(options.body)).asset_type === 'etf')).toBe(true))
  await waitFor(() => expect(vi.mocked(fetchAPI).mock.calls.some(([path, options]) => path === '/positions' && JSON.parse(String(options?.body)).stock_id === 7)).toBe(true))
})

it('preserves the third decimal in mainland ETF holding quotes', async () => {
  vi.mocked(fetchAPI).mockImplementation(async path => {
    if (path === '/stocks') return [{ id: 1, symbol: '510300', name: '沪深300ETF', market: 'CN', asset_type: 'etf', agents: [] }]
    if (path.startsWith('/portfolio/summary')) return { accounts: [{ id: 1, name: 'ETF account', available_funds: 0, positions: [
      { id: 1, stock_id: 1, symbol: '510300', name: '沪深300ETF', market: 'CN', asset_type: 'etf', quantity: 100, cost_price: 4.1, current_price: 4.385 },
    ] }], total }
    if (path === '/quotes/batch') return [{ symbol: '510300', market: 'CN', current_price: 4.385 }]
    if (path.startsWith('/suggestions')) return {}
    return []
  })
  render(<MemoryRouter><MarketColorProvider><StocksPage /></MarketColorProvider></MemoryRouter>)
  expect((await screen.findAllByText('4.385')).length).toBeGreaterThan(0)
  expect(screen.queryByText('4.38')).toBeNull()
})

it('corrects a legacy Hong Kong ETF classification from the existing holding editor', async () => {
  const stock = { id: 1, symbol: '02800', name: '盈富基金', market: 'HK', asset_type: 'stock', agents: [] }
  vi.mocked(fetchAPI).mockImplementation(async (path, options) => {
    if (path === '/stocks') return [stock]
    if (path === '/stocks/1' && options?.method === 'PUT') {
      stock.asset_type = JSON.parse(String(options.body)).asset_type
      return stock
    }
    if (path.startsWith('/portfolio/summary')) return { accounts: [{ id: 1, name: 'Legacy ETF account', available_funds: 0, positions: [
      { id: 1, stock_id: 1, ...stock, quantity: 500, cost_price: 24.8, current_price: 24.84 },
    ] }], total }
    if (path.startsWith('/suggestions')) return {}
    return []
  })
  const user = userEvent.setup()
  render(<MemoryRouter><MarketColorProvider><StocksPage /></MarketColorProvider></MemoryRouter>)
  await user.click((await screen.findAllByRole('button', { name: '编辑持仓' }))[0])
  const dialog = screen.getByRole('dialog')
  await user.click(within(dialog).getByRole('combobox', { name: '标的类型' }))
  await user.click(await screen.findByRole('option', { name: 'ETF', exact: true }))
  await user.click(within(dialog).getByRole('button', { name: '保存' }))
  await waitFor(() => expect(stock.asset_type).toBe('etf'))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
})
