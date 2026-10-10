import { StrictMode } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { insightApi, stocksApi, tradingAgentsApi } from '@panwatch/api'
import StockInsightModal from '@panwatch/biz-ui/components/stock-insight-modal'
import { MarketColorProvider } from '@/hooks/use-market-colors'
import i18n from '@/i18n'
vi.mock('@panwatch/api', async original => ({
 ...await original<typeof import('@panwatch/api')>(),
 insightApi: { quote: vi.fn(), klineSummary: vi.fn(), klines: vi.fn(), portfolioSummary: vi.fn(), suggestions: vi.fn(), news: vi.fn(), history: vi.fn() },
 stocksApi: { list: vi.fn(), create: vi.fn(), remove: vi.fn(), updateAgents: vi.fn(), triggerAgent: vi.fn() },
 tradingAgentsApi: { getLatestForStock:vi.fn(), getHistoryComparison:vi.fn() },
}))
vi.mock('@panwatch/biz-ui/components/stock-price-alert-panel', () => ({ default: () => null }))
vi.mock('@panwatch/biz-ui/components/add-position-calculator', () => ({ default: () => null }))
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail }); return { promise, resolve, reject } }
function modal(market='US',open=true) { return <MemoryRouter><MarketColorProvider><StockInsightModal open={open} onOpenChange={vi.fn()} symbol="T" market={market} hasPosition stockName={market==='US'?'AT&T':'TELUS'} /></MarketColorProvider></MemoryRouter> }
async function askAI() { let detail:any; const listener=(event:Event)=>{detail=(event as CustomEvent).detail}; window.addEventListener('panwatch-open-chat',listener); try { await userEvent.setup().click(screen.getAllByRole('button',{name:'Ask AI'})[0]) } finally {window.removeEventListener('panwatch-open-chat',listener)} return detail }
function quote(market='US', price=111) { return {symbol:'T',market,name:market==='US'?'AT&T':'TELUS',currency:market==='US'?'USD':'CAD',current_price:price} }
function result(mark:string) { return { agent_name:'tradingagents', title:mark, content:mark, timestamp:'2026-10-09T20:00:00Z', raw_data:{ final_decision:mark, suggestion:{action:'hold',action_label:'Hold',confidence:7} } } }
beforeEach(async()=>{
 vi.resetAllMocks(); await i18n.changeLanguage('en-US'); localStorage.setItem('stock_insight_auto_refresh_enabled','false')
 vi.mocked(stocksApi.list).mockResolvedValue([])
 vi.mocked(stocksApi.create).mockResolvedValue({ id: 1, symbol: 'T', market: 'US', name: 'AT&T', agents: [] } as any)
 vi.mocked(stocksApi.updateAgents).mockResolvedValue({} as any)
 vi.mocked(stocksApi.triggerAgent).mockResolvedValue({} as any)
 vi.mocked(insightApi.quote).mockResolvedValue(quote())
 vi.mocked(insightApi.klineSummary).mockResolvedValue({summary:null})
 vi.mocked(insightApi.klines).mockResolvedValue({klines:[]})
 vi.mocked(insightApi.portfolioSummary).mockResolvedValue({accounts:[]})
 vi.mocked(insightApi.suggestions).mockResolvedValue([])
 vi.mocked(insightApi.news).mockResolvedValue([])
 vi.mocked(insightApi.history).mockResolvedValue([])
 vi.mocked(tradingAgentsApi.getLatestForStock).mockResolvedValue(null)
 vi.mocked(tradingAgentsApi.getHistoryComparison).mockResolvedValue({items:[],stats:{}} as any)
})
afterEach(()=>{cleanup();localStorage.clear();vi.useRealTimers()})
it('clears US quote before reopening TSX namesake with its quote pending',async()=>{
 const view=render(modal()); await waitFor(()=>expect(screen.getAllByText('111.00').length).toBeGreaterThan(0))
 view.rerender(modal('US',false))
 const canada=deferred<any>(); vi.mocked(insightApi.quote).mockReturnValueOnce(canada.promise)
 view.rerender(modal('TSX'))
 await waitFor(()=>expect(insightApi.quote).toHaveBeenCalledWith('T','TSX'))
 const detail=await askAI()
 expect(detail.market).toBe('TSX')
 expect(detail.pageContext).not.toContain('111.00 USD')
 await act(async()=>canada.resolve(quote('TSX',22)))
})
it('ignores late US quote after reopening TSX namesake and receiving TSX quote',async()=>{
 const us=deferred<any>();vi.mocked(insightApi.quote).mockReturnValueOnce(us.promise)
 const view=render(modal()); await waitFor(()=>expect(insightApi.quote).toHaveBeenCalledWith('T','US'))
 view.rerender(modal('US',false));vi.mocked(insightApi.quote).mockResolvedValue(quote('TSX',22));view.rerender(modal('TSX'))
 await waitFor(()=>expect(screen.getAllByText('22.00').length).toBeGreaterThan(0))
 await act(async()=>us.resolve(quote()))
 const detail=await askAI();expect(detail.market).toBe('TSX');expect(detail.pageContext).not.toContain('111.00 USD')
})
it('reloads TSX deep analysis after a pending US deep result resolves following close and reopen',async()=>{
 const us=deferred<any>();vi.mocked(tradingAgentsApi.getLatestForStock).mockReturnValueOnce(us.promise)
 const view=render(modal());await userEvent.setup().click(screen.getByRole('button',{name:'Deep analysis'}))
 await waitFor(()=>expect(tradingAgentsApi.getLatestForStock).toHaveBeenCalledWith('T'))
 view.rerender(modal('US',false));view.rerender(modal('TSX'))
 await userEvent.setup().click(screen.getByRole('button',{name:'Deep analysis'}))
 await act(async()=>us.resolve(result('US AT&T DECISION')))
 expect(screen.queryByText('US AT&T DECISION')).toBeNull()
 await waitFor(()=>expect(tradingAgentsApi.getLatestForStock).toHaveBeenCalledWith('TSX:T'))
})

it.each(['US', 'TSX'])('starts a fresh same-symbol session after closing %s', async market => {
  vi.mocked(insightApi.quote).mockResolvedValue(quote(market, 111))
  const view = render(modal(market))
  await screen.findAllByText('111.00')
  view.rerender(modal(market, false))
  const pending = deferred<any>()
  vi.mocked(insightApi.quote).mockReturnValueOnce(pending.promise)
  view.rerender(modal(market))
  expect(screen.queryByText('111.00')).toBeNull()
  expect((await askAI()).pageContext).not.toContain('111.00')
  await act(async () => pending.resolve(quote(market, 22)))
  expect(screen.getAllByText('22.00').length).toBeGreaterThan(0)
})

it('isolates navigation without a closed render and ignores stale core finally', async () => {
  const old = deferred<any>(), current = deferred<any>()
  vi.mocked(insightApi.quote).mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise)
  const view = render(modal())
  view.rerender(modal('TSX'))
  await act(async () => old.resolve(quote()))
  expect(screen.getAllByRole('button', { name: 'Refresh' })[0].hasAttribute('disabled')).toBe(true)
  expect((await askAI()).pageContext).not.toContain('111.00 USD')
  await act(async () => current.resolve(quote('TSX', 22)))
  expect(screen.getAllByRole('button', { name: 'Refresh' })[0].hasAttribute('disabled')).toBe(false)
})

it.each(['resolve', 'reject'] as const)('ignores old suggestions %s after current Canadian suggestions arrive', async settle => {
  const old = deferred<any>()
  vi.mocked(insightApi.suggestions).mockReturnValueOnce(old.promise).mockResolvedValue([{ signal: 'Canadian signal', action: 'hold', agent_name: 'intraday_monitor' }])
  const view = render(modal())
  view.rerender(modal('TSX'))
  await screen.findAllByText('Canadian signal')
  await act(async () => settle === 'resolve' ? old.resolve([{ signal: 'US signal', action: 'buy' }]) : old.reject(new Error('Old request failed')))
  expect((await askAI()).pageContext).toContain('Canadian signal')
  expect(screen.queryByText('US signal')).toBeNull()
})

it.each(['resolve', 'reject'] as const)('ignores old holding %s after current Canadian valuation arrives', async settle => {
  const old = deferred<any>()
  vi.mocked(insightApi.portfolioSummary).mockReturnValueOnce(old.promise).mockResolvedValue({ accounts: [{ positions: [{
    symbol: 'T', market: 'TSX', quantity: 3, cost_price: 10, currency: 'CAD',
    market_value: 66, market_value_cny: 330, pnl: 180, valuation_status: 'available',
  }] }] })
  const view = render(modal())
  view.rerender(modal('TSX'))
  await screen.findByText('3')
  await act(async () => settle === 'resolve' ? old.resolve({ accounts: [] }) : old.reject(new Error('Old holding failure')))
  expect((await askAI()).pageContext).toContain('3 shares; cost 10.00 CAD')
})

it('ignores late technical and mini-chart responses after a listing change', async () => {
  const technical = deferred<any>(), candles = deferred<any>()
  vi.mocked(insightApi.klineSummary).mockReturnValueOnce(technical.promise)
  vi.mocked(insightApi.klines).mockReturnValueOnce(candles.promise)
  const view = render(modal())
  view.rerender(modal('TSX'))
  await act(async () => {
    technical.resolve({ summary: { support: 987, resistance: 999 } })
    candles.resolve({ klines: [{ date: '2026-10-09', open: 111, close: 112, high: 113, low: 110, volume: 10 }] })
  })
  expect((await askAI()).pageContext).not.toContain('Support 987')
  expect(screen.queryByTitle('Open interactive candlestick chart')).toBeNull()
})

it('uses the newest overlapping same-listing refresh without clearing the valid quote while pending', async () => {
  vi.useFakeTimers()
  localStorage.setItem('stock_insight_auto_refresh_enabled', 'true')
  localStorage.setItem('stock_insight_auto_refresh_sec', '10')
  const older = deferred<any>(), newer = deferred<any>()
  vi.mocked(insightApi.quote).mockResolvedValueOnce(quote('TSX', 22)).mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise)
  render(modal('TSX'))
  await act(async () => {})
  await act(async () => vi.advanceTimersByTimeAsync(10_000))
  expect(screen.getAllByText('22.00').length).toBeGreaterThan(0)
  fireEvent.click(screen.getAllByRole('button', { name: 'Refresh' })[0])
  await act(async () => newer.resolve(quote('TSX', 24)))
  await act(async () => older.resolve(quote('TSX', 23)))
  expect(screen.getAllByText('24.00').length).toBeGreaterThan(0)
  expect(screen.queryByText('23.00')).toBeNull()
})

it('does not run news/report fallbacks after unmount', async () => {
  const news = deferred<any>(), report = deferred<any>()
  vi.mocked(insightApi.news).mockReturnValue(news.promise)
  vi.mocked(insightApi.history).mockReturnValue(report.promise)
  const view = render(modal('TSX'))
  const newsCalls = vi.mocked(insightApi.news).mock.calls.length
  const reportCalls = vi.mocked(insightApi.history).mock.calls.length
  view.unmount()
  await act(async () => { news.resolve([]); report.resolve([]) })
  expect(insightApi.news).toHaveBeenCalledTimes(newsCalls)
  expect(insightApi.history).toHaveBeenCalledTimes(reportCalls)
})

it('ignores watch creation from an old market and preserves the new market follow control', async () => {
  const old = deferred<any>()
  vi.mocked(stocksApi.create).mockReturnValueOnce(old.promise)
  const view = render(modal())
  fireEvent.click(screen.getAllByRole('button', { name: 'Follow' })[0])
  view.rerender(modal('TSX'))
  await act(async () => old.resolve({ id: 1, symbol: 'T', market: 'US' }))
  expect(screen.getAllByRole('button', { name: 'Follow' })[0].hasAttribute('disabled')).toBe(false)
  expect(screen.queryByRole('button', { name: 'Unfollow' })).toBeNull()
})

it.each(['pending', 'active'])('does not leave %s suggestion polling after unmount', async state => {
  vi.useFakeTimers()
  const trigger = deferred<any>()
  if (state === 'pending') vi.mocked(stocksApi.triggerAgent).mockReturnValueOnce(trigger.promise)
  const view = render(modal())
  await act(async () => {})
  fireEvent.click(screen.getAllByRole('button', { name: 'Set alert' })[0])
  await act(async () => {})
  expect(stocksApi.triggerAgent).toHaveBeenCalledTimes(1)
  view.unmount()
  const calls = vi.mocked(insightApi.suggestions).mock.calls.length
  await act(async () => { trigger.resolve({}); await vi.advanceTimersByTimeAsync(130_000) })
  expect(insightApi.suggestions).toHaveBeenCalledTimes(calls)
  expect(vi.getTimerCount()).toBe(0)
})

it('survives StrictMode effect replay without accepting the abandoned first request', async () => {
  const old = deferred<any>()
  vi.mocked(insightApi.quote).mockReturnValueOnce(old.promise).mockResolvedValue(quote('TSX', 22))
  render(<StrictMode>{modal('TSX')}</StrictMode>)
  await screen.findAllByText('22.00')
  await act(async () => old.resolve(quote('TSX', 111)))
  expect(screen.queryByText('111.00')).toBeNull()
  expect((await askAI()).pageContext).toContain('22.00 CAD')
})

it('coalesces recurring refreshes so a slow provider response can still finish', async () => {
  vi.useFakeTimers()
  localStorage.setItem('stock_insight_auto_refresh_enabled', 'true')
  localStorage.setItem('stock_insight_auto_refresh_sec', '10')
  vi.mocked(insightApi.quote).mockResolvedValueOnce(quote('TSX', 22)).mockImplementation(() => new Promise(resolve => setTimeout(() => resolve(quote('TSX', 24)), 15_000)))
  render(modal('TSX'))
  await act(async () => {})
  await act(async () => vi.advanceTimersByTimeAsync(26_000))
  expect(screen.getAllByText('24.00').length).toBeGreaterThan(0)
  expect(insightApi.quote).toHaveBeenCalledTimes(2)
})

it('coalesces slow suggestion polling even while automatic refresh is also enabled', async () => {
  vi.useFakeTimers()
  localStorage.setItem('stock_insight_auto_refresh_enabled', 'true')
  localStorage.setItem('stock_insight_auto_refresh_sec', '10')
  vi.mocked(insightApi.suggestions).mockResolvedValueOnce([]).mockImplementation(() => new Promise(resolve => setTimeout(() => resolve([{ signal: 'Completed slow signal', action: 'hold' }]), 6_000)))
  render(modal())
  await act(async () => {})
  fireEvent.click(screen.getAllByRole('button', { name: 'Set alert' })[0])
  await act(async () => {})
  await act(async () => vi.advanceTimersByTimeAsync(20_000))
  expect(screen.getAllByText('Completed slow signal').length).toBeGreaterThan(0)
})
