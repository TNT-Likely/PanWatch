import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchAPI, stocksApi } from '@panwatch/api'
import StocksPage from '@/pages/Stocks'
import { MarketColorProvider } from '@/hooks/use-market-colors'
import { changeLocale } from '@/i18n'

vi.mock('@panwatch/api', async importOriginal => ({
  ...await importOriginal<typeof import('@panwatch/api')>(),
  fetchAPI: vi.fn(),
  stocksApi: { create: vi.fn() },
}))
vi.mock('@panwatch/api/klines', () => ({ klinesApi: { summaryBatch: vi.fn().mockResolvedValue({}) } }))
vi.mock('@panwatch/biz-ui/components/stock-insight-modal', () => ({
  default: ({ open, market, symbol }: { open: boolean; market: string; symbol: string }) => open ? <div data-testid="stock-insight-identity">{market}:{symbol}</div> : null,
}))

type Instrument = { id: number; symbol: string; name: string; market: string; asset_type: 'stock' | 'etf'; agents: [] }
type Search = (market: string | null, query: string, signal: AbortSignal | undefined) => Instrument[] | Promise<Instrument[]>
const total = { total_market_value: 0, total_cost: 0, total_pnl: 0, total_pnl_pct: 0, total_daily_pnl: 0, available_funds: 0, total_assets: 0 }
const collisions: Instrument[] = [
  { id: 1, market: 'TSX', symbol: 'AAA', name: 'Toronto listing', asset_type: 'stock', agents: [] },
  { id: 2, market: 'TSXV', symbol: 'AAA', name: 'Venture listing', asset_type: 'stock', agents: [] },
  { id: 3, market: 'US', symbol: 'AAA', name: 'US listing', asset_type: 'stock', agents: [] },
]
const funds: Instrument[] = [
  { id: 7, market: 'TSX', symbol: 'ZSP', name: 'BMO S&P 500 ETF', asset_type: 'etf', agents: [] },
  { id: 8, market: 'TSX', symbol: 'REI-UN', name: 'RioCan REIT', asset_type: 'stock', agents: [] },
  { id: 9, market: 'TSXV', symbol: 'TBRD', name: 'Thunderbird', asset_type: 'stock', agents: [] },
]

beforeEach(() => { vi.clearAllMocks(); localStorage.clear() })
afterEach(() => { cleanup(); vi.clearAllMocks(); localStorage.clear() })

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(resolvePromise => { resolve = resolvePromise })
  return { promise, resolve }
}

function mockApi({ saved = [], search }: { saved?: Instrument[]; search?: Search } = {}) {
  const stocks = [...saved]
  const instruments = [...collisions, ...funds]
  vi.mocked(stocksApi.create).mockImplementation(async payload => {
    const stock = { ...payload, id: 99, agents: [] } as Instrument
    stocks.push(stock)
    return stock
  })
  vi.mocked(fetchAPI).mockImplementation(async (path, options) => {
    if (path === '/stocks' && options?.method === 'POST') {
      const stock = { ...JSON.parse(String(options.body)), id: 99, agents: [] }
      stocks.push(stock)
      return stock
    }
    if (path === '/stocks') return [...stocks]
    if (path.startsWith('/stocks/search?')) {
      const url = new URL(path, 'https://panwatch.test')
      const market = url.searchParams.get('market')
      const query = url.searchParams.get('q') || ''
      if (market && !['CN', 'HK', 'US', 'TSX', 'TSXV'].includes(market)) throw new Error(`Invalid backend search market: ${market}`)
      return search ? search(market, query, options?.signal as AbortSignal | undefined) : instruments.filter(stock => (!market || stock.market === market) && stock.symbol === query)
    }
    if (path.startsWith('/portfolio/summary')) return {
      accounts: [{ id: 4, name: 'Canada account', available_funds: 0, positions: [] }], total,
    }
    if (path === '/quotes/batch') return stocks.map(stock => ({ symbol: stock.symbol, market: stock.market, current_price: stock.id * 11, change_pct: null }))
    if (path === '/positions' && options?.method === 'POST') return { id: 100 }
    if (path.startsWith('/suggestions')) return {}
    if (['/stocks/markets/status', '/price-alerts', '/agents', '/providers/services', '/channels'].includes(path)) return []
    throw new Error(`Unexpected request: ${path}`)
  })
}

function renderStocks() {
  return render(<MemoryRouter><MarketColorProvider><StocksPage /></MarketColorProvider></MemoryRouter>)
}

async function openDialog(kind: 'watchlist' | 'holding') {
  fireEvent.click((await screen.findAllByRole('button', { name: kind === 'watchlist' ? '添加股票 / ETF' : '添加持仓', exact: true }))[0])
  return screen.getByRole('dialog')
}

function searchCanada(dialog: HTMLElement, query: string) {
  fireEvent.click(within(dialog).getByRole('button', { name: '加拿大', exact: true }))
  fireEvent.change(within(dialog).getByPlaceholderText(/代码或名称/), { target: { value: query } })
}

function expectCanadaRequests(query: string) {
  for (const market of ['TSX', 'TSXV']) {
    expect(fetchAPI).toHaveBeenCalledWith(`/stocks/search?q=${encodeURIComponent(query)}&market=${market}`, expect.objectContaining({ signal: expect.any(AbortSignal) }))
  }
  expect(vi.mocked(fetchAPI).mock.calls.some(([path]) => /[?&]market=CA(?:&|$)/.test(path))).toBe(false)
}

function submittedPositions() {
  return vi.mocked(fetchAPI).mock.calls.filter(([path, options]) => path === '/positions' && options?.method === 'POST').map(([, options]) => JSON.parse(String(options!.body)))
}

function fillHolding(dialog: HTMLElement) {
  fireEvent.change(within(dialog).getByPlaceholderText('0.00'), { target: { value: '12.5' } })
  fireEvent.change(within(dialog).getByPlaceholderText('0'), { target: { value: '2.125' } })
  fireEvent.click(within(dialog).getByRole('button', { name: '添加', exact: true }))
}

describe('unified Canada portfolio category', () => {
  it('combines watchlist counts and filters without mixing exchange badges, quotes, or detail identity', async () => {
    mockApi({ saved: collisions })
    localStorage.setItem('panwatch_stocks_viewTab', JSON.stringify('watchlist'))
    renderStocks()
    const canada = await screen.findByRole('button', { name: '加拿大 (2)' })
    expect(screen.queryByRole('button', { name: /多伦多.*\(\d+\)/ })).toBeNull()
    fireEvent.click(canada)
    expect(screen.getByRole('button', { name: 'Toronto listing' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Venture listing' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'US listing' })).toBeNull()
    expect(screen.getByText('TSX', { exact: true })).toBeTruthy()
    expect(screen.getByText('TSXV', { exact: true })).toBeTruthy()
    await screen.findByText('11.00')
    expect(screen.getByText('22.00')).toBeTruthy()
    expect(screen.queryByText('33.00')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Venture listing' }))
    expect(screen.getByTestId('stock-insight-identity').textContent).toBe('TSXV:AAA')
    fireEvent.click(screen.getByRole('button', { name: 'Toronto listing' }))
    expect(screen.getByTestId('stock-insight-identity').textContent).toBe('TSX:AAA')
    fireEvent.click(screen.getByRole('button', { name: '美股 (1)' }))
    expect(screen.getByRole('button', { name: 'US listing' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Toronto listing' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Venture listing' })).toBeNull()
    expect(screen.getByText('33.00')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '全部 (3)' }))
    expect(screen.getAllByRole('button', { name: 'AAA', exact: true })).toHaveLength(3)
  })

  it('uses the English Canada label in both add dialogs and watchlist filters', async () => {
    await changeLocale('en-US')
    mockApi({ saved: collisions })
    localStorage.setItem('panwatch_stocks_viewTab', JSON.stringify('watchlist'))
    renderStocks()
    expect(await screen.findByRole('button', { name: 'Canada (2)' })).toBeTruthy()
    fireEvent.click(screen.getAllByRole('button', { name: 'Add stock / ETF' })[0])
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('button', { name: 'Canada', exact: true })).toBeTruthy()
    expect(within(dialog).queryByRole('button', { name: /^TSX/ })).toBeNull()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel', exact: true }))
    fireEvent.click(screen.getByRole('button', { name: /^Positions/ }))
    fireEvent.click(screen.getAllByRole('button', { name: 'Add position', exact: true })[0])
    expect(within(screen.getByRole('dialog')).getByRole('button', { name: 'Canada', exact: true })).toBeTruthy()
  })

  it.each(funds)('adds $market:$symbol from Canada without changing its symbol or asset type', async stock => {
    mockApi()
    renderStocks()
    const dialog = await openDialog('watchlist')
    expect(within(dialog).queryByRole('button', { name: /多伦多/ })).toBeNull()
    searchCanada(dialog, stock.symbol)
    fireEvent.click(await within(dialog).findByRole('button', { name: new RegExp(`${stock.symbol}.*${stock.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`) }))
    expectCanadaRequests(stock.symbol)
    fireEvent.click(within(dialog).getByRole('button', { name: '确认添加' }))
    await waitFor(() => expect(stocksApi.create).toHaveBeenCalledWith({ symbol: stock.symbol, name: stock.name, market: stock.market, asset_type: stock.asset_type }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it.each(collisions.slice(0, 2))('selects the existing $market holding by market plus symbol instead of a colliding listing', async stock => {
    // Put the US collision first so a symbol-only lookup is wrong for either exchange.
    mockApi({ saved: [collisions[2], collisions[1], collisions[0]] })
    renderStocks()
    const dialog = await openDialog('holding')
    searchCanada(dialog, 'AAA')
    await within(dialog).findByRole('button', { name: /TSX AAA Toronto listing/ })
    await within(dialog).findByRole('button', { name: /TSXV AAA Venture listing/ })
    expect(within(dialog).queryByRole('button', { name: /US listing/ })).toBeNull()
    fireEvent.click(within(dialog).getByRole('button', { name: new RegExp(`${stock.market} AAA ${stock.name}`) }))
    expectCanadaRequests('AAA')
    fillHolding(dialog)
    await waitFor(() => expect(submittedPositions()).toEqual([expect.objectContaining({ stock_id: stock.id, account_id: 4, quantity: 2.125 })]))
    expect(vi.mocked(fetchAPI).mock.calls.some(([path, options]) => path === '/stocks' && options?.method === 'POST')).toBe(false)
  })

  it.each(funds)('creates a new $market:$symbol holding with the original exchange and asset type', async stock => {
    mockApi()
    renderStocks()
    const dialog = await openDialog('holding')
    searchCanada(dialog, stock.symbol)
    fireEvent.click(await within(dialog).findByRole('button', { name: new RegExp(`${stock.market} ${stock.symbol}`) }))
    expectCanadaRequests(stock.symbol)
    fillHolding(dialog)
    await waitFor(() => expect(submittedPositions()).toEqual([expect.objectContaining({ stock_id: 99, account_id: 4, quantity: 2.125 })]))
    const requests = vi.mocked(fetchAPI).mock.calls.filter(([path, options]) => path === '/stocks' && options?.method === 'POST')
    expect(requests).toHaveLength(1)
    expect(JSON.parse(String(requests[0][1]!.body))).toEqual({ symbol: stock.symbol, name: stock.name, market: stock.market, asset_type: stock.asset_type })
  })

  it.each(['watchlist', 'holding'] as const)('keeps only the latest %s search across query changes, Escape, and reopening', async kind => {
    const stale = deferred<Instrument[]>()
    const closed = deferred<Instrument[]>()
    const staleSignals: AbortSignal[] = []
    const closeSignals: AbortSignal[] = []
    mockApi({ search: (market, query, signal) => {
      if (query === 'OLD') { staleSignals.push(signal!); return stale.promise }
      if (query === 'CLOSE') { closeSignals.push(signal!); return closed.promise }
      return collisions.filter(stock => stock.market === market)
    } })
    const user = userEvent.setup()
    renderStocks()
    let dialog = await openDialog(kind)
    searchCanada(dialog, 'OLD')
    await waitFor(() => expectCanadaRequests('OLD'))
    fireEvent.change(within(dialog).getByPlaceholderText(/代码或名称/), { target: { value: 'AAA' } })
    await within(dialog).findByRole('button', { name: /Venture listing/ })
    expectCanadaRequests('AAA')
    expect(staleSignals).toHaveLength(2)
    expect(staleSignals.every(signal => signal.aborted)).toBe(true)
    await act(async () => { stale.resolve([{ ...collisions[0], symbol: 'OLD', name: 'Stale search' }]) })
    expect(within(dialog).queryByText('Stale search')).toBeNull()
    expect(within(dialog).getByRole('button', { name: /Venture listing/ })).toBeTruthy()

    fireEvent.change(within(dialog).getByPlaceholderText(/代码或名称/), { target: { value: 'CLOSE' } })
    await waitFor(() => expectCanadaRequests('CLOSE'))
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(closeSignals).toHaveLength(2)
    expect(closeSignals.every(signal => signal.aborted)).toBe(true)
    dialog = await openDialog(kind)
    expect((within(dialog).getByPlaceholderText(/代码或名称/) as HTMLInputElement).value).toBe('')
    await act(async () => { closed.resolve([{ ...collisions[1], symbol: 'CLOSE', name: 'Closed dialog result' }]) })
    expect(within(dialog).queryByText('Closed dialog result')).toBeNull()
    searchCanada(dialog, 'AAA')
    await within(dialog).findByRole('button', { name: /Toronto listing/ })
    expect(within(dialog).getByRole('button', { name: /Venture listing/ })).toBeTruthy()
  })

  it.each(['watchlist', 'holding'] as const)('preserves healthy %s results on partial failure and retries after both exchanges fail', async kind => {
    let mode: 'partial' | 'failed' | 'healthy' = 'partial'
    mockApi({ search: (market) => {
      if (mode === 'failed' || (mode === 'partial' && market === 'TSXV')) return Promise.reject(new Error(`${market} unavailable`))
      return collisions.filter(stock => stock.market === market)
    } })
    renderStocks()
    const dialog = await openDialog(kind)
    searchCanada(dialog, 'AAA')
    await within(dialog).findByRole('button', { name: /Toronto listing/ })
    expect(within(dialog).getByRole('status').textContent).toContain('TSXV')
    expect(within(dialog).queryByRole('button', { name: /Venture listing/ })).toBeNull()
    expectCanadaRequests('AAA')

    mode = 'failed'
    fireEvent.change(within(dialog).getByPlaceholderText(/代码或名称/), { target: { value: 'BROKEN' } })
    await waitFor(() => expect(within(dialog).getByRole('status').textContent).toContain('股票搜索失败'))
    expectCanadaRequests('BROKEN')
    expect(within(dialog).queryByRole('button', { name: /Toronto listing/ })).toBeNull()

    mode = 'healthy'
    fireEvent.change(within(dialog).getByPlaceholderText(/代码或名称/), { target: { value: 'AAA' } })
    await within(dialog).findByRole('button', { name: /Venture listing/ })
    expect(within(dialog).getByRole('button', { name: /Toronto listing/ })).toBeTruthy()
    expect(within(dialog).queryByRole('status')).toBeNull()
  })

  it.each(['watchlist', 'holding'] as const)('does not let late Canadian %s results replace a same-symbol US search', async kind => {
    const stale = deferred<Instrument[]>()
    const signals: AbortSignal[] = []
    mockApi({ search: (market, _query, signal) => {
      if (market === 'TSX' || market === 'TSXV') { signals.push(signal!); return stale.promise }
      return collisions.filter(stock => stock.market === market)
    } })
    renderStocks()
    const dialog = await openDialog(kind)
    searchCanada(dialog, 'AAA')
    await waitFor(() => expectCanadaRequests('AAA'))
    fireEvent.click(within(dialog).getByRole('button', { name: '美股', exact: true }))
    await within(dialog).findByRole('button', { name: /US listing/ })
    expect(fetchAPI).toHaveBeenCalledWith('/stocks/search?q=AAA&market=US', expect.objectContaining({ signal: expect.any(AbortSignal) }))
    expect(signals).toHaveLength(2)
    expect(signals.every(signal => signal.aborted)).toBe(true)
    await act(async () => { stale.resolve(collisions.slice(0, 2)) })
    expect(within(dialog).queryByRole('button', { name: /Toronto listing|Venture listing/ })).toBeNull()
    expect(within(dialog).getByRole('button', { name: /US listing/ })).toBeTruthy()
  })

  it.each(['market change', 'close and reopen'] as const)('does not replay a Canada search when refresh-list finishes after %s', async transition => {
    const refresh = deferred<{ count: number }>()
    mockApi()
    const api = vi.mocked(fetchAPI).getMockImplementation()!
    vi.mocked(fetchAPI).mockImplementation((path, options) => path === '/stocks/refresh-list' ? refresh.promise : api(path, options))
    renderStocks()
    let dialog = await openDialog('watchlist')
    searchCanada(dialog, 'AAA')
    await within(dialog).findByRole('button', { name: /Venture listing/ })
    fireEvent.click(within(dialog).getByRole('button', { name: '刷新列表', exact: true }))
    expect(fetchAPI).toHaveBeenCalledWith('/stocks/refresh-list', { method: 'POST' })
    if (transition === 'market change') {
      fireEvent.click(within(dialog).getByRole('button', { name: '美股', exact: true }))
      await within(dialog).findByRole('button', { name: /US listing/ })
    } else {
      fireEvent.click(within(dialog).getByRole('button', { name: '取消', exact: true }))
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
      dialog = await openDialog('watchlist')
    }
    const searchesBeforeRefresh = vi.mocked(fetchAPI).mock.calls.filter(([path]) => path.startsWith('/stocks/search?')).length
    await act(async () => { refresh.resolve({ count: 3 }) })
    expect(vi.mocked(fetchAPI).mock.calls.filter(([path]) => path.startsWith('/stocks/search?'))).toHaveLength(searchesBeforeRefresh)
    expect(within(dialog).queryByRole('button', { name: /Toronto listing|Venture listing/ })).toBeNull()
    if (transition === 'market change') {
      expect(within(dialog).getByRole('button', { name: /US listing/ })).toBeTruthy()
    } else {
      expect((within(dialog).getByPlaceholderText(/代码或名称/) as HTMLInputElement).value).toBe('')
    }
  })

  it('aborts pending Canada holding requests when navigating away and does not replay them on return', async () => {
    const stale = deferred<Instrument[]>()
    const signals: AbortSignal[] = []
    let pending = true
    mockApi({ search: (market, _query, signal) => {
      if (pending) { signals.push(signal!); return stale.promise }
      return collisions.filter(stock => stock.market === market)
    } })
    let navigate!: ReturnType<typeof useNavigate>
    function Navigation() { navigate = useNavigate(); return null }
    render(<MemoryRouter initialEntries={['/stocks']}><MarketColorProvider><Navigation /><Routes>
      <Route path="/stocks" element={<StocksPage />} />
      <Route path="/away" element={<p>Away from portfolio</p>} />
    </Routes></MarketColorProvider></MemoryRouter>)
    const dialog = await openDialog('holding')
    searchCanada(dialog, 'AAA')
    await waitFor(() => expectCanadaRequests('AAA'))
    act(() => navigate('/away'))
    expect(screen.getByText('Away from portfolio')).toBeTruthy()
    expect(signals).toHaveLength(2)
    expect(signals.every(signal => signal.aborted)).toBe(true)
    pending = false
    act(() => navigate('/stocks'))
    const reopened = await openDialog('holding')
    searchCanada(reopened, 'AAA')
    await within(reopened).findByRole('button', { name: /Venture listing/ })
    await act(async () => { stale.resolve([{ ...collisions[0], name: 'Departed page result' }]) })
    expect(screen.queryByText('Departed page result')).toBeNull()
    expect(within(reopened).getByRole('button', { name: /Venture listing/ })).toBeTruthy()
  })
})
