import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchAPI } from '@panwatch/api'
import { changeLocale } from '@/i18n'
import { AssistantStockPicker, type AssistantStockSearchResult } from '@/components/assistant/AssistantStockPicker'

vi.mock('@panwatch/api', () => ({ fetchAPI: vi.fn() }))

beforeEach(async () => {
  vi.resetAllMocks()
  await changeLocale('en-US')
})

afterEach(() => { cleanup(); localStorage.clear() })

const collisions: AssistantStockSearchResult[] = [
  { market: 'TSX', symbol: 'AAA', name: 'Toronto listing' },
  { market: 'TSXV', symbol: 'AAA', name: 'Venture listing' },
  { market: 'US', symbol: 'AAA', name: 'US listing' },
]

function mockSearch(stocks: AssistantStockSearchResult[]) {
  vi.mocked(fetchAPI).mockImplementation(async path => {
    const url = new URL(path, 'https://panwatch.test')
    if (url.pathname !== '/stocks/search') throw new Error(`Unexpected request: ${path}`)
    const market = url.searchParams.get('market')
    if (!['CN', 'HK', 'US', 'TSX', 'TSXV'].includes(market || '')) {
      throw new Error(`Invalid backend search market: ${market}`)
    }
    return stocks.filter(stock => stock.market === market)
  })
}

function expectCanadaRequests(query: string) {
  for (const market of ['TSX', 'TSXV']) {
    expect(fetchAPI).toHaveBeenCalledWith(`/stocks/search?q=${encodeURIComponent(query)}&market=${market}`, expect.objectContaining({ signal: expect.any(AbortSignal) }))
  }
  expect(vi.mocked(fetchAPI).mock.calls.some(([path]) => /[?&]market=CA(?:&|$)/.test(path))).toBe(false)
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(resolvePromise => { resolve = resolvePromise })
  return { promise, resolve }
}

describe('Canadian research stock picker', () => {
  it.each([
    ['en-US', 'Canada'],
    ['zh-CN', '加拿大'],
  ])('shows one localized Canada category in %s', async (locale, label) => {
    await changeLocale(locale as 'en-US' | 'zh-CN')
    render(<AssistantStockPicker onSelect={vi.fn()} onCancel={vi.fn()} />)
    const marketGroup = screen.getByRole('group')
    expect(within(marketGroup).getAllByRole('button')).toHaveLength(4)
    expect(within(marketGroup).getByRole('button', { name: label, exact: true })).toBeTruthy()
    expect(within(marketGroup).queryByRole('button', { name: /TSX|多伦多/ })).toBeNull()
  })

  it.each([
    ['TSX', 'REI-UN', 'RioCan REIT'],
    ['TSX', 'ZSP', 'BMO S&P 500 ETF'],
    ['TSXV', 'TBRD', 'Thunderbird'],
  ])('searches both exchanges and selects %s:%s without changing its identity', async (market, symbol, name) => {
    const stock = { market, symbol, name }
    mockSearch([stock])
    const onSelect = vi.fn()
    render(<AssistantStockPicker onSelect={onSelect} onCancel={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Canada', exact: true }))
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: symbol } })
    const result = await screen.findByRole('button', { name: `${name} ${market}:${symbol}` })
    expectCanadaRequests(symbol)
    fireEvent.click(result)
    expect(onSelect).toHaveBeenCalledWith(stock)
  })

  it('keeps same-symbol TSX and TSXV results separate from each other and a US listing', async () => {
    mockSearch(collisions)
    const onSelect = vi.fn()
    render(<AssistantStockPicker onSelect={onSelect} onCancel={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Canada', exact: true }))
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'AAA' } })
    for (const stock of collisions.slice(0, 2)) {
      fireEvent.click(await screen.findByRole('button', { name: `${stock.name} ${stock.market}:AAA` }))
      expect(onSelect).toHaveBeenLastCalledWith(stock)
    }
    expectCanadaRequests('AAA')
    expect(screen.queryByText('US listing')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'US', exact: true }))
    fireEvent.click(await screen.findByRole('button', { name: 'US listing US:AAA' }))
    expect(onSelect).toHaveBeenLastCalledWith(collisions[2])
    expect(screen.queryByText('Toronto listing')).toBeNull()
    expect(screen.queryByText('Venture listing')).toBeNull()
  })

  it('ignores superseded Canada responses after editing the query and switching to US', async () => {
    const oldToronto = deferred<AssistantStockSearchResult[]>()
    const oldVenture = deferred<AssistantStockSearchResult[]>()
    const signals: AbortSignal[] = []
    vi.mocked(fetchAPI).mockImplementation(async (path, options) => {
      const url = new URL(path, 'https://panwatch.test')
      const market = url.searchParams.get('market')
      if (url.searchParams.get('q') === 'OLD') {
        signals.push(options!.signal as AbortSignal)
        return market === 'TSX' ? oldToronto.promise : oldVenture.promise
      }
      return collisions.filter(stock => stock.market === market)
    })
    render(<AssistantStockPicker onSelect={vi.fn()} onCancel={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Canada', exact: true }))
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'OLD' } })
    await waitFor(() => expectCanadaRequests('OLD'))
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'AAA' } })
    await screen.findByRole('button', { name: 'Venture listing TSXV:AAA' })
    expectCanadaRequests('AAA')
    fireEvent.click(screen.getByRole('button', { name: 'US', exact: true }))
    await screen.findByRole('button', { name: 'US listing US:AAA' })
    expect(signals).toHaveLength(2)
    expect(signals.every(signal => signal.aborted)).toBe(true)
    await act(async () => {
      oldToronto.resolve([{ market: 'TSX', symbol: 'OLD', name: 'Stale Toronto' }])
      oldVenture.resolve([{ market: 'TSXV', symbol: 'OLD', name: 'Stale Venture' }])
    })
    expect(screen.queryByText(/Stale/)).toBeNull()
    expect(screen.getByRole('button', { name: 'US listing US:AAA' })).toBeTruthy()
  })

  it('shows healthy results on partial failure, clears results on full failure, and recovers on retry', async () => {
    let mode: 'partial' | 'failed' | 'healthy' = 'partial'
    vi.mocked(fetchAPI).mockImplementation(async path => {
      const market = new URL(path, 'https://panwatch.test').searchParams.get('market')
      if (mode === 'failed' || (mode === 'partial' && market === 'TSXV')) throw new Error(`${market} unavailable`)
      return collisions.filter(stock => stock.market === market)
    })
    const onSelect = vi.fn()
    render(<AssistantStockPicker onSelect={onSelect} onCancel={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Canada', exact: true }))
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'AAA' } })
    fireEvent.click(await screen.findByRole('button', { name: 'Toronto listing TSX:AAA' }))
    expect(onSelect).toHaveBeenCalledWith(collisions[0])
    expect(screen.getByRole('status').textContent).toContain('TSXV')
    expect(screen.queryByRole('button', { name: 'Venture listing TSXV:AAA' })).toBeNull()
    expectCanadaRequests('AAA')

    mode = 'failed'
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'BROKEN' } })
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('TSX unavailable'))
    expectCanadaRequests('BROKEN')
    expect(screen.queryByText('Toronto listing')).toBeNull()

    mode = 'healthy'
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'AAA' } })
    await screen.findByRole('button', { name: 'Venture listing TSXV:AAA' })
    expect(screen.getByRole('button', { name: 'Toronto listing TSX:AAA' })).toBeTruthy()
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('aborts both pending exchange searches when closed and starts fresh when reopened', async () => {
    const stale = deferred<AssistantStockSearchResult[]>()
    const signals: AbortSignal[] = []
    vi.mocked(fetchAPI).mockImplementation(async (_path, options) => {
      signals.push(options!.signal as AbortSignal)
      return stale.promise
    })
    const onCancel = vi.fn()
    const first = render(<AssistantStockPicker onSelect={vi.fn()} onCancel={onCancel} />)
    fireEvent.click(screen.getByRole('button', { name: 'Canada', exact: true }))
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'AAA' } })
    await waitFor(() => expectCanadaRequests('AAA'))
    fireEvent.click(screen.getByRole('button', { name: 'Back to research' }))
    expect(onCancel).toHaveBeenCalledOnce()
    first.unmount()
    expect(signals).toHaveLength(2)
    expect(signals.every(signal => signal.aborted)).toBe(true)

    mockSearch(collisions)
    render(<AssistantStockPicker onSelect={vi.fn()} onCancel={onCancel} />)
    expect((screen.getByRole('searchbox') as HTMLInputElement).value).toBe('')
    fireEvent.click(screen.getByRole('button', { name: 'Canada', exact: true }))
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'AAA' } })
    await screen.findByRole('button', { name: 'Venture listing TSXV:AAA' })
    await act(async () => { stale.resolve([{ market: 'TSX', symbol: 'OLD', name: 'Closed picker result' }]) })
    expect(screen.queryByText('Closed picker result')).toBeNull()
    expect(screen.getByRole('button', { name: 'Toronto listing TSX:AAA' })).toBeTruthy()
  })
})
