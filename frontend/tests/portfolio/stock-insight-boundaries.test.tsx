import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { insightApi } from '@panwatch/api'
import StockInsightModal from '@panwatch/biz-ui/components/stock-insight-modal'
import { MarketColorProvider } from '@/hooks/use-market-colors'
import i18n from '@/i18n'
import unavailableFxSummary from '../fixtures/stock-insight-fx-unavailable.json'

vi.mock('@panwatch/api', async original => ({
  ...await original<typeof import('@panwatch/api')>(),
  insightApi: {
    quote: vi.fn(), klineSummary: vi.fn(), klines: vi.fn(), portfolioSummary: vi.fn(),
    suggestions: vi.fn(), news: vi.fn(), history: vi.fn(),
  },
  stocksApi: { list: vi.fn().mockResolvedValue([]) },
}))
vi.mock('@panwatch/biz-ui/components/stock-price-alert-panel', () => ({ default: () => null }))
vi.mock('@panwatch/biz-ui/components/add-position-calculator', () => ({ default: () => null }))

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.setItem('stock_insight_auto_refresh_enabled', 'false')
  vi.mocked(insightApi.quote).mockResolvedValue({ symbol: 'T', market: 'TSX', name: 'TELUS', current_price: 20, currency: 'CAD' })
  vi.mocked(insightApi.klineSummary).mockResolvedValue({ summary: null })
  vi.mocked(insightApi.klines).mockResolvedValue({ klines: [] })
  vi.mocked(insightApi.portfolioSummary).mockResolvedValue({ accounts: [] })
  vi.mocked(insightApi.suggestions).mockResolvedValue([])
  vi.mocked(insightApi.news).mockResolvedValue([])
  vi.mocked(insightApi.history).mockResolvedValue([])
})
afterEach(() => { cleanup(); localStorage.clear() })

function modal(symbol = 'T', market = 'TSX', open = true) {
  return <MemoryRouter><MarketColorProvider><StockInsightModal open={open} onOpenChange={vi.fn()} symbol={symbol} market={market} stockName="TELUS" /></MarketColorProvider></MemoryRouter>
}
function position(overrides = {}) {
  // Selected-field contract for GET /portfolio/summary?include_quotes=true:
  // src/modules/portfolio/api/accounts.py:get_portfolio_summary, verified by
  // tests/test_canada_valuation.py:test_unavailable_conversion_is_explicit.
  // Fixture: 1.5 TSX shares, cost 10, quote 20 CAD, unavailable CAD/CNY rate.
  // Account/total valuation_incomplete=true; native 30 survives null conversion.
  return { symbol: 'T', market: 'TSX', quantity: 1.5, cost_price: 10, currency: 'CAD',
    market_value: 30, market_value_cny: null, pnl: null, valuation_status: 'fx_unavailable', ...overrides }
}
function setPositions(...positions: ReturnType<typeof position>[]) {
  vi.mocked(insightApi.portfolioSummary).mockResolvedValue({ accounts: positions.map(value => ({ positions: [value], valuation_incomplete: value.valuation_status !== 'available' })), total: { valuation_incomplete: positions.some(value => value.valuation_status !== 'available') } })
}
function metric(label: string) { return screen.getByText(label).parentElement!.textContent }
async function askAI() {
  let detail: { symbol: string; market: string; pageContext: string } | undefined
  const listener = (event: Event) => { detail = (event as CustomEvent).detail }
  window.addEventListener('panwatch-open-chat', listener)
  try { await userEvent.setup().click(screen.getAllByRole('button', { name: /^(问 AI|Ask AI)$/ })[0]) }
  finally { window.removeEventListener('panwatch-open-chat', listener) }
  expect(detail).toBeDefined()
  return detail!
}

it.each(['zh-CN', 'en-US'])('preserves unknown CNY values and native CAD in the rendered holding and AskAI context (%s)', async language => {
  await i18n.changeLanguage(language)
  vi.mocked(insightApi.portfolioSummary).mockResolvedValue(unavailableFxSummary)
  render(modal())
  await screen.findByText('1.5')
  const english = language === 'en-US'
  expect(metric(english ? 'Average cost' : '持仓成本(单价)')).toContain('10.00 CAD')
  expect(metric(english ? 'Market value' : '持仓市值')).toContain('-- CNY')
  expect(metric(english ? 'Market value' : '持仓市值')).toContain('30.00 CAD')
  expect(metric(english ? 'Total P&L' : '总盈亏')).toContain('-- CNY')
  expect(metric(english ? 'Total P&L' : '总盈亏')).not.toContain('+--')
  expect(screen.getByRole('status').textContent).toMatch(english ? /incomplete/ : /不完整/)
  const detail = await askAI()
  expect(detail.market).toBe('TSX')
  expect(detail.pageContext).toContain('30.00 CAD')
  expect(detail.pageContext).toContain('10.00 CAD')
  expect(detail.pageContext).toContain(english ? 'market value Unavailable CNY; P&L Unavailable CNY' : '折算市值暂不可用 CNY，盈亏暂不可用 CNY')
})

it('does not present a partially converted multi-account sum as complete or include a US namesake', async () => {
  setPositions(position(), position({ quantity: 2, market_value: 40, market_value_cny: 200, pnl: 100, valuation_status: 'available' }),
    position({ market: 'US', quantity: 50, currency: 'USD', market_value: 1000, market_value_cny: 7000, pnl: 3500 }))
  render(modal())
  await screen.findByText('3.5')
  expect(metric('持仓市值')).toContain('70.00 CAD')
  expect(metric('持仓市值')).toContain('-- CNY')
  expect(metric('总盈亏')).toContain('-- CNY')
  const { pageContext } = await askAI()
  expect(pageContext).toContain('3.5股')
  expect(pageContext).not.toContain('200.00 CNY')
})

it('retains real zero, converted values, and verified USD denomination for Canadian classes', async () => {
  vi.mocked(insightApi.quote).mockResolvedValue({ symbol: 'ZSP-U', market: 'TSX', current_price: 20, currency: 'USD' })
  setPositions(position({ symbol: 'ZSP-U', currency: 'USD', market_value_cny: 210, pnl: 0, valuation_status: 'available' }))
  render(modal('ZSP.U.TO'))
  await screen.findByText('1.5')
  expect(metric('持仓成本(单价)')).toContain('10.00 USD')
  expect(metric('持仓市值')).toContain('210 CNY')
  expect(metric('持仓市值')).toContain('30.00 USD')
  expect(metric('总盈亏')).toContain('+0 CNY')
  expect(screen.queryByRole('status')).toBeNull()
  const { pageContext } = await askAI()
  expect(pageContext).toContain('折算市值210.00 CNY，盈亏0.00 CNY')
})

it('does not invent CAD when quote currency is unknown', async () => {
  vi.mocked(insightApi.quote).mockResolvedValue({ symbol: 'T', market: 'TSX', current_price: 20, currency: null })
  setPositions(position({ currency: null, valuation_status: 'currency_unavailable' }))
  render(modal())
  await screen.findByText('1.5')
  expect(metric('持仓市值')).toContain('30.00 --')
  const { pageContext } = await askAI()
  expect(pageContext).not.toContain('CAD')
  expect(pageContext).toContain('原币市值30.00 暂不可用')
})

it('keeps unavailable native quotes unknown rather than zero', async () => {
  setPositions(position({ market_value: null, valuation_status: 'quote_unavailable' }))
  render(modal())
  await screen.findByText('1.5')
  expect(metric('持仓市值')).toContain('原币市值：-- CAD')
  expect((await askAI()).pageContext).toContain('原币市值暂不可用 CAD')
})

it('does not add native amounts or costs from contradictory currencies', async () => {
  setPositions(position(), position({ currency: 'USD', quantity: 2, market_value: 40 }))
  render(modal())
  await screen.findByText('3.5')
  expect(metric('持仓成本(单价)')).toContain('-- --')
  expect(metric('持仓市值')).toContain('原币市值：-- --')
})

function report(overrides: Record<string, unknown> = {}) {
  // Production writers: daily_report.py:context_payload and premarket_outlook.py:context_payload
  // persist { [symbol]: { market, ... } } on global '*' history. The history router
  // in src/modules/portfolio/api/history.py forwards it without reshaping.
  return { id: 1, agent_name: 'daily_report', stock_symbol: '*', title: 'US AT&T report', content: 'US AT&T risk for T',
    analysis_date: '2026-10-09', created_at: '2026-10-09T20:00:00Z',
    suggestions: { T: { action_label: 'Hold' } }, context_payload: { T: { market: 'US' } }, ...overrides }
}
function setReports(rows: ReturnType<typeof report>[]) {
  vi.mocked(insightApi.history).mockImplementation(async params => rows.filter(row => row.stock_symbol === params.stock_symbol && row.agent_name === params.agent_name))
}
async function reportsSettled(minCalls = 6) {
  await waitFor(() => expect(vi.mocked(insightApi.history).mock.calls.length).toBeGreaterThanOrEqual(minCalls))
  await act(async () => {})
}

describe('rendered report identity boundaries', () => {
  it.each(['*', 'T'])('rejects US suggestions/context for TELUS from stock_symbol=%s', async stock_symbol => {
    setReports([report({ stock_symbol })])
    render(modal())
    await reportsSettled()
    expect(screen.queryByText('US AT&T report')).toBeNull()
    expect(screen.getByRole('button', { name: '报告 (0)' })).toBeTruthy()
  })

  it.each([
    { suggestions: { T: { action_label: 'Hold' } }, context_payload: null },
    { suggestions: null, context_payload: null, content: 'TELUS ticker T and the market outlook' },
    { suggestions: { 'US:T': { action_label: 'Hold' } }, context_payload: null },
    { suggestions: { 'T.V': { action_label: 'Hold' } }, context_payload: null },
    { suggestions: { 'TSX:T': { action_label: 'Hold' } }, context_payload: { 'TSX:T': { market: 'US' } } },
  ])('rejects ambiguous, text-only, wrong-exchange or contradictory global records %#', async evidence => {
    setReports([report(evidence)])
    render(modal())
    await reportsSettled()
    expect(screen.queryByText('US AT&T report')).toBeNull()
    expect(screen.getByRole('button', { name: '报告 (0)' })).toBeTruthy()
  })

  it.each([
    ['T', 'TSX', 'T', { T: { market: 'TSX' } }],
    ['T', 'TSX', '*', { 't.to': { market: 'tsx' } }],
    ['T', 'TSXV', '*', { 'TSXV:T': { market: 'TSXV' } }],
    ['ZSP-U', 'TSX', '*', { 'ZSP.U.TO': { market: 'TSX' } }],
    ['ENB-PV', 'TSX', '*', { 'ENB.PR.V': { market: 'TSX' } }],
  ])('shows verified reports for %s/%s using canonical market evidence', async (symbol, market, stock_symbol, context_payload) => {
    setReports([report({ title: 'Verified Canadian report', stock_symbol, context_payload, suggestions: null })])
    render(modal(symbol as string, market as string))
    expect(await screen.findByText('Verified Canadian report')).toBeTruthy()
    expect(screen.queryByText('US AT&T report')).toBeNull()
  })

  it('loads a qualified direct Canadian report and independently falls back for the other agent', async () => {
    setReports([
      report({ stock_symbol: 'TSX:T', title: 'Canadian close', context_payload: null, suggestions: null }),
      report({ agent_name: 'premarket_outlook', title: 'Canadian open', context_payload: { T: { market: 'TSX' } } }),
    ])
    render(modal())
    await screen.findByRole('button', { name: '报告 (2)' })
    expect(insightApi.history).toHaveBeenCalledWith({ agent_name: 'daily_report', stock_symbol: 'TSX:T', limit: 20 })
    await userEvent.setup().click(screen.getByRole('button', { name: '报告 (2)' }))
    expect(await screen.findByText('Canadian open')).toBeTruthy()
    await userEvent.setup().click(screen.getByRole('button', { name: '盘后' }))
    expect(await screen.findByText('Canadian close')).toBeTruthy()
  })

  it('finds an older verified direct record when the newest direct record is a US namesake', async () => {
    setReports([
      report({ stock_symbol: 'T', created_at: '2026-10-10T20:00:00Z' }),
      report({ id: 2, stock_symbol: 'T', title: 'Earlier verified Canadian report', context_payload: { T: { market: 'TSX' } } }),
    ])
    render(modal())
    expect(await screen.findByText('Earlier verified Canadian report')).toBeTruthy()
    expect(screen.queryByText('US AT&T report')).toBeNull()
  })

  it('rejects bare legacy ticker records for the US view too when exchange evidence is missing', async () => {
    setReports([report({ stock_symbol: 'T', context_payload: null })])
    render(modal('T', 'US'))
    await reportsSettled(4)
    expect(screen.queryByText('US AT&T report')).toBeNull()
  })

  it('preserves unambiguous legacy mainland numeric records', async () => {
    setReports([report({ stock_symbol: '600000', title: 'Legacy mainland report', context_payload: null, suggestions: null })])
    render(modal('600000', 'CN'))
    expect(await screen.findByText('Legacy mainland report')).toBeTruthy()
  })
})

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}

it('clears prior holding context when changing listing before the next summary resolves', async () => {
  setPositions(position())
  const view = render(modal())
  await screen.findByText('1.5')
  const pending = deferred<unknown>()
  vi.mocked(insightApi.portfolioSummary).mockImplementationOnce(() => pending.promise)
  view.rerender(modal('T', 'US'))
  expect(screen.queryByText('1.5')).toBeNull()
  const detail = await askAI()
  expect(detail.market).toBe('US')
  expect(detail.pageContext).not.toContain('持仓：')
  await act(async () => pending.resolve({ accounts: [] }))
})

it('ignores delayed holdings and reports from a prior listing or a closed modal', async () => {
  const holding = deferred<unknown>()
  const history = deferred<ReturnType<typeof report>[]>()
  vi.mocked(insightApi.portfolioSummary).mockImplementationOnce(() => holding.promise)
  vi.mocked(insightApi.history).mockImplementation(params => params.stock_symbol === 'T' && params.agent_name === 'daily_report'
    ? history.promise : Promise.resolve([]))
  const view = render(modal('T', 'US'))
  await waitFor(() => expect(insightApi.portfolioSummary).toHaveBeenCalledTimes(1))
  view.rerender(modal('T', 'US', false))
  await act(async () => {
    holding.resolve({ accounts: [{ positions: [position({ market: 'US', quantity: 100, currency: 'USD' })] }] })
    history.resolve([report({ stock_symbol: 'T' })])
  })
  const nextHolding = deferred<unknown>()
  vi.mocked(insightApi.portfolioSummary).mockImplementationOnce(() => nextHolding.promise)
  vi.mocked(insightApi.history).mockResolvedValue([])
  view.rerender(modal())
  await reportsSettled()
  expect(screen.queryByText('100')).toBeNull()
  expect(screen.queryByText('US AT&T report')).toBeNull()
  expect((await askAI()).pageContext).not.toContain('持仓：')
  await act(async () => nextHolding.resolve({ accounts: [] }))
})

it('rechecks report identity when the same ticker changes markets and ignores old pending history', async () => {
  const oldHistory = deferred<ReturnType<typeof report>[]>()
  vi.mocked(insightApi.history).mockImplementation(params => params.stock_symbol === 'T' && params.agent_name === 'daily_report'
    ? oldHistory.promise : Promise.resolve([]))
  const view = render(modal('T', 'US'))
  await waitFor(() => expect(insightApi.history).toHaveBeenCalledWith({ agent_name: 'daily_report', stock_symbol: 'T', limit: 20 }))
  setReports([report({ title: 'Current Canadian report', context_payload: { T: { market: 'TSX' } } })])
  view.rerender(modal())
  expect(await screen.findByText('Current Canadian report')).toBeTruthy()
  await act(async () => oldHistory.resolve([report({ stock_symbol: 'T' })]))
  expect(screen.getByText('Current Canadian report')).toBeTruthy()
  expect(screen.queryByText('US AT&T report')).toBeNull()
})

it('retains unambiguous legacy five-digit HK reports', async () => {
  setReports([report({ stock_symbol: '00700', title: 'Legacy HK report', context_payload: null, suggestions: null })])
  render(modal('00700', 'HK'))
  expect(await screen.findByText('Legacy HK report')).toBeTruthy()
})

it('shows only the market-qualified suggestion badge within a report covering two namesakes', async () => {
  setReports([report({ title: 'Multi-market report',
    suggestions: { T: { action_label: 'Ambiguous US badge' }, 'TSX:T': { action_label: 'Verified Canadian badge' } },
    context_payload: { 'US:T': { market: 'US' }, 'TSX:T': { market: 'TSX' } },
  })])
  render(modal())
  await userEvent.setup().click(await screen.findByRole('button', { name: '报告 (1)' }))
  await userEvent.setup().click(screen.getByRole('button', { name: '盘后' }))
  expect(screen.queryByText('Ambiguous US badge')).toBeNull()
  expect(screen.getByText('Verified Canadian badge')).toBeTruthy()
})

it.each([
  ['600000', 'CN'], ['00700', 'HK'], ['T', 'US'],
])('preserves current global writer-shaped reports for %s/%s', async (symbol, market) => {
  const evidence = { [symbol]: { market, data_quality: {}, kline_history: {}, constraints: {}, memory: {}, news: { realtime: [], extended: [], history: [] } } }
  setReports([report({ title: 'Current production writer report', context_payload: evidence,
    context_summary: { [symbol]: { data_quality: {}, constraints: {} } }, suggestions: { [symbol]: { action_label: 'Verified current badge' } },
  })])
  render(modal(symbol, market))
  expect(await screen.findByText('Current production writer report')).toBeTruthy()
  await userEvent.setup().click(screen.getByRole('button', { name: '报告 (1)' }))
  await userEvent.setup().click(screen.getByRole('button', { name: '盘后' }))
  expect(screen.getByText('Verified current badge')).toBeTruthy()
})

it('does not mistake a conflicting alias suffix for the requested exchange', async () => {
  setReports([report({ suggestions: { 'T.V': { market: 'TSX', action_label: 'Conflicting alias badge' } }, context_payload: null })])
  render(modal())
  await reportsSettled()
  expect(screen.getByRole('button', { name: '报告 (0)' })).toBeTruthy()
})

it('supports a qualified suggestion-only report when no legacy context is available', async () => {
  setReports([report({ title: 'Qualified Canadian suggestion', suggestions: { 'TSX:T': { action_label: 'Canadian action' } }, context_payload: null })])
  render(modal())
  expect(await screen.findByText('Qualified Canadian suggestion')).toBeTruthy()
  await userEvent.setup().click(screen.getByRole('button', { name: '报告 (1)' }))
  await userEvent.setup().click(screen.getByRole('button', { name: '盘后' }))
  expect(screen.getByText('Canadian action')).toBeTruthy()
})

it('resolves a direct native preferred-share alias only with verified TSX evidence', async () => {
  setReports([report({ title: 'Preferred-share report', stock_symbol: 'ENB.PR.V',
    suggestions: { 'ENB.PR.V': { action_label: 'Verified preferred action' } },
    context_payload: { 'ENB.PR.V': { market: 'TSX' } },
  })])
  render(modal('ENB.PR.V'))
  expect(await screen.findByText('Preferred-share report')).toBeTruthy()
  await userEvent.setup().click(screen.getByRole('button', { name: '报告 (1)' }))
  await userEvent.setup().click(screen.getByRole('button', { name: '盘后' }))
  expect(screen.getByText('Verified preferred action')).toBeTruthy()
})

function newsItem(title: string) {
  return { source: 'fixture', source_label: 'Fixture', title, symbols: ['T'], publish_time: '2026-10-09T20:00:00Z', url: 'https://example.test/news' }
}

it('retains TSX on every news and announcement search, including bare-symbol and global fallbacks', async () => {
  vi.mocked(insightApi.news).mockImplementation(async params => {
    if (params.market !== 'TSX') return [newsItem('US AT&T news leak')]
    if (!params.symbols && !params.names) return [newsItem(params.source === 'eastmoney' ? 'TELUS announcement' : 'TELUS news')]
    return []
  })
  render(modal())
  expect(await screen.findByText('TELUS news')).toBeTruthy()
  await screen.findByRole('button', { name: '公告 (1)' })
  const calls = vi.mocked(insightApi.news).mock.calls.map(([params]) => params)
  expect(calls.length).toBeGreaterThanOrEqual(10)
  expect(calls.every(params => params.market === 'TSX')).toBe(true)
  for (const source of [undefined, 'eastmoney']) {
    const lane = calls.filter(params => params.source === source)
    expect(lane.some(params => params.names === 'TELUS')).toBe(true)
    expect(lane.some(params => params.symbols === 'T')).toBe(true)
    expect(lane.some(params => !params.symbols && !params.names)).toBe(true)
  }
  expect(screen.queryByText('US AT&T news leak')).toBeNull()
  await userEvent.setup().click(screen.getByRole('button', { name: '公告 (1)' }))
  expect(await screen.findByText('TELUS announcement')).toBeTruthy()
})

it('ignores pending news and announcements after switching a namesake ticker to TSX', async () => {
  const oldNews = deferred<ReturnType<typeof newsItem>[]>()
  vi.mocked(insightApi.news).mockImplementation(params => params.market === 'US'
    ? oldNews.promise : Promise.resolve([newsItem(params.source === 'eastmoney' ? 'Current TELUS announcement' : 'Current TELUS news')]))
  const view = render(modal('T', 'US'))
  await waitFor(() => expect(insightApi.news).toHaveBeenCalled())
  view.rerender(modal())
  expect(await screen.findByText('Current TELUS news')).toBeTruthy()
  await screen.findByRole('button', { name: '公告 (1)' })
  await act(async () => oldNews.resolve([newsItem('Stale US news')]))
  expect(screen.getByText('Current TELUS news')).toBeTruthy()
  expect(screen.queryByText('Stale US news')).toBeNull()
  await userEvent.setup().click(screen.getByRole('button', { name: '公告 (1)' }))
  expect(await screen.findByText('Current TELUS announcement')).toBeTruthy()
  expect(screen.queryByText('Stale US news')).toBeNull()
})
