import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { fetchAPI, insightApi, stocksApi } from '@panwatch/api'
import InteractiveKline from '@panwatch/biz-ui/components/InteractiveKline'
import AddPositionCalculator from '@panwatch/biz-ui/components/add-position-calculator'
import StockPriceAlertPanel from '@panwatch/biz-ui/components/stock-price-alert-panel'
import { MarketColorProvider } from '@/hooks/use-market-colors'
import i18n from '@/i18n'

const toast = vi.hoisted(() => vi.fn())
vi.mock('@panwatch/api', () => ({ fetchAPI: vi.fn(), insightApi: { addPositionEval: vi.fn() }, stocksApi: { list: vi.fn(), create: vi.fn() } }))
vi.mock('@panwatch/base-ui/components/ui/toast', () => ({ useToast: () => ({ toast }) }))
vi.mock('@panwatch/base-ui/components/ui/confirm-dialog', () => ({ useConfirm: () => vi.fn() }))
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}
function candles(close: number) { return { klines: [{ date: '2026-10-09', open: close - 1, close, high: close + 1, low: close - 2, volume: 10 }] } }
function chart(market = 'TSX') { return <MarketColorProvider><InteractiveKline symbol="T" market={market} /></MarketColorProvider> }
function calculator(market = 'TSX') { return <AddPositionCalculator symbol="T" market={market} currency="CAD" currentQuantity={1} currentCost={10} currentPrice={20} /> }
beforeEach(async () => {
  vi.resetAllMocks()
  await i18n.changeLanguage('en-US')
  vi.mocked(fetchAPI).mockResolvedValue([])
  vi.mocked(stocksApi.list).mockResolvedValue([])
})
afterEach(() => cleanup())

it.each(['resolve', 'reject'] as const)('keeps newer chart interval metrics after older request %s', async settle => {
  const old = deferred<any>()
  vi.mocked(fetchAPI).mockReturnValueOnce(old.promise).mockResolvedValue(candles(22))
  render(chart())
  fireEvent.click(screen.getByRole('button', { name: 'Weekly' }))
  await screen.findByText('22.00')
  await act(async () => settle === 'resolve' ? old.resolve(candles(111)) : old.reject(new Error('Old daily failure')))
  expect(screen.getByText('22.00')).toBeTruthy()
  expect(screen.queryByText('111.00')).toBeNull()
  expect(screen.queryByText('Old daily failure')).toBeNull()
  expect(screen.getByRole('button', { name: 'Refresh' }).hasAttribute('disabled')).toBe(false)
  expect(fetchAPI).toHaveBeenCalledTimes(2)
})

it('clears the previous listing chart before the next quote series arrives', async () => {
  vi.mocked(fetchAPI).mockResolvedValueOnce(candles(111))
  const view = render(chart('US'))
  await screen.findByText('111.00')
  const pending = deferred<any>()
  vi.mocked(fetchAPI).mockReturnValueOnce(pending.promise)
  view.rerender(chart('TSX'))
  expect(screen.queryByText('111.00')).toBeNull()
  await act(async () => pending.resolve(candles(22)))
  expect(screen.getByText('22.00')).toBeTruthy()
})

it.each(['resolve', 'reject'] as const)('ignores add-position evaluation %s after a market change', async settle => {
  const old = deferred<any>()
  vi.mocked(insightApi.addPositionEval).mockReturnValueOnce(old.promise)
  const view = render(calculator('US'))
  fireEvent.click(screen.getByRole('button', { name: /Add-position calculator/ }))
  fireEvent.change(screen.getByLabelText('Shares to add'), { target: { value: '3' } })
  fireEvent.click(screen.getByRole('button', { name: 'Ask AI whether adding is suitable' }))
  expect(insightApi.addPositionEval).toHaveBeenCalledTimes(1)
  view.rerender(calculator('TSX'))
  fireEvent.click(screen.getByRole('button', { name: /Add-position calculator/ }))
  await act(async () => settle === 'resolve' ? old.resolve({ verdict: '谨慎', content: 'Old US evaluation' }) : old.reject(new Error('Old evaluation failed')))
  expect(screen.queryByText('Old US evaluation')).toBeNull()
  expect((screen.getByLabelText('Shares to add') as HTMLInputElement).value).toBe('')
  expect(toast).not.toHaveBeenCalled()
})

it('does not create a stock or open an alert form after leaving a pending create flow', async () => {
  const view = render(<StockPriceAlertPanel symbol="T" market="US" stockName="AT&T" />)
  fireEvent.click(screen.getByRole('button', { name: 'Price alerts' }))
  await screen.findByText('No alert rules for this stock')
  const old = deferred<any>()
  vi.mocked(stocksApi.list).mockReturnValueOnce(old.promise)
  fireEvent.click(screen.getByRole('button', { name: 'New alert' }))
  await waitFor(() => expect(stocksApi.list).toHaveBeenCalledTimes(2))
  view.rerender(<StockPriceAlertPanel symbol="T" market="TSX" stockName="TELUS" />)
  await act(async () => old.resolve([]))
  expect(stocksApi.create).not.toHaveBeenCalled()
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(toast).not.toHaveBeenCalled()
})

it('does not resume alert creation after Escape closes its own dialog while the panel stays mounted', async () => {
  render(<StockPriceAlertPanel symbol="T" market="TSX" stockName="TELUS" />)
  fireEvent.click(screen.getByRole('button', { name: 'Price alerts' }))
  await screen.findByText('No alert rules for this stock')
  const old = deferred<any>()
  vi.mocked(stocksApi.list).mockReturnValueOnce(old.promise)
  fireEvent.click(screen.getByRole('button', { name: 'New alert' }))
  await waitFor(() => expect(stocksApi.list).toHaveBeenCalledTimes(2))
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('dialog')).toBeNull()
  await act(async () => old.resolve([]))
  expect(stocksApi.create).not.toHaveBeenCalled()
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(toast).not.toHaveBeenCalled()
})

it('still invalidates parent summaries when an approved mutation completes after dialog close', async () => {
  const onChanged = vi.fn(), mutation = deferred<any>()
  const rule = { id: 1, stock_id: 1, stock_symbol: 'T', market: 'TSX', stock_name: 'TELUS', name: 'Canadian rule', enabled: true,
    condition_group: { op: 'and', items: [] }, notify_channel_ids: [] }
  vi.mocked(fetchAPI).mockImplementation((path, options) => options?.method === 'POST' ? mutation.promise : Promise.resolve(path === '/price-alerts' ? [rule] : []))
  const view = render(<StockPriceAlertPanel symbol="T" market="TSX" stockName="TELUS" onChanged={onChanged} />)
  fireEvent.click(screen.getByRole('button', { name: 'Price alerts' }))
  await screen.findByText('Canadian rule')
  fireEvent.click(screen.getByRole('button', { name: 'Disable' }))
  fireEvent.keyDown(document, { key: 'Escape' })
  view.rerender(<StockPriceAlertPanel symbol="T" market="US" stockName="AT&T" onChanged={onChanged} />)
  await act(async () => mutation.resolve({}))
  expect(onChanged).toHaveBeenCalledTimes(1)
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(toast).not.toHaveBeenCalled()
})
