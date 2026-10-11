import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { fetchAPI } from '@panwatch/api'
import { klinesApi } from '@panwatch/api/klines'
import StocksPage from '@/pages/Stocks'
import { MarketColorProvider } from '@/hooks/use-market-colors'

vi.mock('@panwatch/api', async importOriginal => ({ ...await importOriginal<typeof import('@panwatch/api')>(), fetchAPI: vi.fn() }))
vi.mock('@panwatch/api/klines', () => ({ klinesApi: { summaryBatch: vi.fn() } }))
afterEach(() => { cleanup(); vi.clearAllMocks(); localStorage.clear() })

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function fixture(initiallyAdded = true) {
  let added = initiallyAdded
  let quantity = .125
  let edited = false
  const fxRequests: { signal: AbortSignal; response: ReturnType<typeof deferred<ReturnType<typeof summary>>> }[] = []
  const stock = { id: 1, symbol: 'ZSP', name: 'Canadian ETF', market: 'TSX', agents: [] }
  function summary(rate: number | null = null) {
    return {
      exchange_rates: { HKD_CNY: .9, USD_CNY: 7, CAD_CNY: rate, CANADA_USD_CNY: rate === null ? null : 7 },
      accounts: [{ id: 1, name: 'Canadian account', available_funds: 0, positions: added ? [{
        id: 1, stock_id: 1, symbol: 'ZSP', name: 'Canadian ETF', market: 'TSX', currency: null,
        cost_price: 8, quantity, invested_amount: null, trading_style: '', current_price: null, change_pct: null,
      }] : [] }],
      total: { total_market_value: 0, total_cost: 0, total_pnl: 0, total_pnl_pct: 0, total_daily_pnl: 0, available_funds: 0, total_assets: 0 },
    }
  }
  // A slow unrelated lane must not delay either mutation metadata or FX.
  vi.mocked(klinesApi.summaryBatch).mockImplementation(() => new Promise(() => {}))
  vi.mocked(fetchAPI).mockImplementation(async (path, options) => {
    if (path === '/stocks') return [stock]
    if (path.startsWith('/stocks/search')) return [stock]
    if (path === '/positions' && options?.method === 'POST') { added = true; return { id: 1 } }
    if (path === '/positions/1' && options?.method === 'PUT') {
      edited = true; quantity = JSON.parse(String(options.body)).quantity; return { id: 1 }
    }
    if (path === '/portfolio/summary?include_quotes=false&refresh_exchange_rates=false') return summary(edited || !initiallyAdded ? null : 5)
    if (path === '/portfolio/summary?include_quotes=false') {
      const response = deferred<ReturnType<typeof summary>>()
      fxRequests.push({ signal: options!.signal!, response })
      return response.promise
    }
    if (path === '/quotes/batch') return [{ symbol: 'ZSP', market: 'TSX', currency: 'CAD', current_price: 10, change_pct: null, daily_move_status: 'closed' }]
    if (path.startsWith('/suggestions')) return {}
    return []
  })
  const view = render(<MemoryRouter><MarketColorProvider><StocksPage /></MarketColorProvider></MemoryRouter>)
  return { ...view, fxRequests, summary, user: userEvent.setup() }
}

async function editQuantity(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getAllByRole('button', { name: '编辑持仓' })[0])
  const dialog = screen.getByRole('dialog')
  const input = within(dialog).getByPlaceholderText('0')
  await user.clear(input)
  await user.type(input, '0.25')
  await user.click(within(dialog).getByRole('button', { name: '保存' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
}

it('refreshes expired FX after an edit without blocking metadata or reloading quotes', async () => {
  const { user, fxRequests, summary } = fixture()
  await waitFor(() => expect(fxRequests).toHaveLength(1))
  await act(async () => fxRequests[0].response.resolve(summary(5)))
  await screen.findAllByText('10.00 CAD')
  const quoteCount = vi.mocked(fetchAPI).mock.calls.filter(([path]) => path === '/quotes/batch').length
  await editQuantity(user)
  await waitFor(() => expect(fxRequests).toHaveLength(2))
  expect(screen.getAllByText('0.25').length).toBeGreaterThan(0)
  expect(screen.getAllByText('10.00 CAD').length).toBeGreaterThan(0)
  expect(document.body.textContent).toContain('折算汇总不完整')
  expect(vi.mocked(fetchAPI).mock.calls.filter(([path]) => path === '/quotes/batch')).toHaveLength(quoteCount)
  await act(async () => fxRequests[1].response.resolve(summary(5)))
  await waitFor(() => expect(document.body.textContent).not.toContain('折算汇总不完整'))
  expect(document.body.textContent).toContain('12.50')
  expect(fxRequests).toHaveLength(2)
  expect(klinesApi.summaryBatch).toHaveBeenCalledTimes(1)
})

it('fetches FX after the first Canadian holding is added', async () => {
  const { user, fxRequests, summary } = fixture(false)
  await waitFor(() => expect(fxRequests).toHaveLength(1))
  await act(async () => fxRequests[0].response.resolve(summary()))
  const card = (await screen.findByText('Canadian account')).closest('.card')!
  await user.click(card.querySelector('svg.lucide-plus')!.closest('button')!)
  const dialog = screen.getByRole('dialog')
  await user.type(within(dialog).getByPlaceholderText(/代码或名称/), 'ZSP')
  await user.click(await within(dialog).findByRole('button', { name: /ZSP.*Canadian ETF/ }))
  await user.type(within(dialog).getByPlaceholderText('0.00'), '8')
  await user.type(within(dialog).getByPlaceholderText('0'), '0.125')
  await user.click(within(dialog).getByRole('button', { name: '添加' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  await waitFor(() => expect(fxRequests).toHaveLength(2))
  expect(document.body.textContent).toContain('折算汇总不完整')
  await act(async () => fxRequests[1].response.resolve(summary(5)))
  await waitFor(() => expect(document.body.textContent).not.toContain('折算汇总不完整'))
  expect(screen.getAllByText('10.00 CAD').length).toBeGreaterThan(0)
  expect(document.body.textContent).toContain('6.25')
  expect(fxRequests).toHaveLength(2)
})

it('keeps failed FX incomplete, then recovers on manual refresh without a retry loop', async () => {
  const { user, fxRequests, summary } = fixture()
  await waitFor(() => expect(fxRequests).toHaveLength(1))
  await act(async () => fxRequests[0].response.resolve(summary(5)))
  await editQuantity(user)
  await waitFor(() => expect(fxRequests).toHaveLength(2))
  await act(async () => fxRequests[1].response.reject(new Error('FX offline')))
  expect(document.body.textContent).toContain('折算汇总不完整')
  expect(fxRequests).toHaveLength(2)
  await user.click(screen.getAllByRole('button', { name: '刷新' })[0])
  await waitFor(() => expect(fxRequests).toHaveLength(3))
  await act(async () => fxRequests[2].response.resolve(summary(5)))
  await waitFor(() => expect(document.body.textContent).not.toContain('折算汇总不完整'))
  expect(document.body.textContent).toContain('12.50')
  expect(fxRequests).toHaveLength(3)
})

it('ignores an older FX result after mutation and repeated manual refresh', async () => {
  const { user, fxRequests, summary } = fixture()
  await waitFor(() => expect(fxRequests).toHaveLength(1))
  const oldInitial = summary(null)
  await editQuantity(user)
  await waitFor(() => expect(fxRequests).toHaveLength(2))
  expect(fxRequests[0].signal.aborted).toBe(true)
  const oldMutation = summary(9)
  await user.click(screen.getAllByRole('button', { name: '刷新' })[0])
  await waitFor(() => expect(fxRequests).toHaveLength(3))
  expect(fxRequests[1].signal.aborted).toBe(true)
  await act(async () => fxRequests[2].response.resolve(summary(5)))
  await waitFor(() => expect(document.body.textContent).not.toContain('折算汇总不完整'))
  await act(async () => { fxRequests[0].response.resolve(oldInitial); fxRequests[1].response.resolve(oldMutation) })
  expect(document.body.textContent).not.toContain('折算汇总不完整')
  expect(document.body.textContent).toContain('12.50')
  expect(screen.getAllByText('0.25').length).toBeGreaterThan(0)
  expect(fxRequests).toHaveLength(3)
})

it('aborts the independent FX request when the page unmounts', async () => {
  const { unmount, fxRequests, summary } = fixture()
  await waitFor(() => expect(fxRequests).toHaveLength(1))
  unmount()
  expect(fxRequests[0].signal.aborted).toBe(true)
  await act(async () => fxRequests[0].response.resolve(summary(5)))
  expect(fxRequests).toHaveLength(1)
})


it('marks Canadian conversion incomplete when a manual FX refresh fails after valid rates', async () => {
  const { user, fxRequests, summary } = fixture()
  await waitFor(() => expect(fxRequests).toHaveLength(1))
  await act(async () => fxRequests[0].response.resolve(summary(5)))
  await screen.findAllByText('10.00 CAD')
  expect(document.body.textContent).not.toContain('折算汇总不完整')
  await user.click(screen.getAllByRole('button', { name: '刷新' })[0])
  await waitFor(() => expect(fxRequests).toHaveLength(2))
  await act(async () => fxRequests[1].response.reject(new Error('FX offline')))
  await waitFor(() => expect(document.body.textContent).toContain('折算汇总不完整'))
  expect(screen.getAllByText('10.00 CAD').length).toBeGreaterThan(0)
  expect(fxRequests).toHaveLength(2)
})
