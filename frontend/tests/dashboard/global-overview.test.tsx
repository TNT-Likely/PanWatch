import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { DashboardOverviewInstrument } from '@panwatch/api'
import GlobalMarketOverview, { loadOverviewPreferences, OVERVIEW_GROUPS } from '@/components/GlobalMarketOverview'
import i18n from '@/i18n'

const row = (id: string, group: string): DashboardOverviewInstrument => ({
  id, group, name_key: id, symbol: id, name: id, market: group,
  instrument_type: 'index', currency: 'USD', unit: 'points', provider: 'Test provider', provider_symbol: id,
  current_price: 100, change_pct: 1, change_amount: 1, prev_close: 99, change_bps: null,
  as_of: '2026-10-09T20:00:00+00:00', history_as_of: '2026-10-09T20:00:00+00:00',
  timezone: 'America/New_York', status: 'delayed', spark: [],
})
const rows = Object.entries({
  china: ['csi300', 'star50', 'chinext'], hongKong: ['hsi', 'hstech'], us: ['sp500', 'nasdaq', 'dow'],
  canada: ['tsx'], europe: ['cac40', 'dax', 'ftse100', 'stoxx50'], asia: ['nikkei225', 'vnindex'],
  commodities: ['gold', 'oil', 'corn'], crypto: ['btc', 'eth'],
  fixedIncome: ['us3m', 'us5y', 'us10y', 'ca3m', 'ca5y', 'ca10y', 'cn3m', 'cn5y', 'cn10y'],
}).flatMap(([group, ids]) => ids.map(id => row(id, group)))
const storageKey = 'panwatch.globalOverview.v1'
const defaultNames = ['CSI 300', 'ChiNext', 'Hang Seng', 'S&P 500', 'Nasdaq Composite']
const visibleNames = () => screen.getAllByRole('article').map(card => card.getAttribute('aria-label'))
const showAll = () => localStorage.setItem(storageKey, JSON.stringify({ order: [], hidden: [] }))

beforeEach(async () => { localStorage.clear(); await i18n.changeLanguage('en-US') })
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear() })

describe('global market overview', () => {
  it('defaults to exactly five indices without empty group headings or auto-saving', async () => {
    const user = userEvent.setup()
    render(<GlobalMarketOverview instruments={rows} />)
    expect(visibleNames()).toEqual(defaultNames)
    expect(screen.getAllByRole('heading', { level: 3 }).map(heading => heading.textContent)).toEqual(['China', 'Hong Kong', 'US'])
    await user.click(screen.getByRole('button', { name: 'Customize' }))
    expect(screen.getAllByRole('checkbox')).toHaveLength(rows.length + OVERVIEW_GROUPS.length)
    await user.click(screen.getByRole('button', { name: 'Done' }))
    expect(localStorage.getItem(storageKey)).toBeNull()
  })

  it('keeps all 29 instruments in nine groups available to customize', async () => {
    const user = userEvent.setup()
    render(<GlobalMarketOverview instruments={rows} />)
    await user.click(screen.getByRole('button', { name: 'Customize' }))
    for (const checkbox of screen.getAllByRole('checkbox') as HTMLInputElement[]) {
      if (!checkbox.checked) await user.click(checkbox)
    }
    await user.click(screen.getByRole('button', { name: 'Done' }))
    expect(screen.getAllByRole('article')).toHaveLength(29)
    expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(9)
    expect(loadOverviewPreferences()).toEqual({ order: [], hidden: [] })
  })

  it('preserves an explicitly saved show-all layout until Reset', async () => {
    const user = userEvent.setup()
    showAll()
    localStorage.setItem('otherPreference', 'keep')
    const view = render(<GlobalMarketOverview instruments={rows} />)
    expect(screen.getAllByRole('article')).toHaveLength(29)
    expect(loadOverviewPreferences()).toEqual({ order: [], hidden: [] })
    await user.click(screen.getByRole('button', { name: 'Customize' }))
    await user.click(screen.getByRole('button', { name: 'Reset' }))
    expect(visibleNames()).toEqual(defaultNames)
    expect(localStorage.getItem(storageKey)).toBeNull()
    expect(localStorage.getItem('otherPreference')).toBe('keep')
    view.unmount()
    render(<GlobalMarketOverview instruments={rows} />)
    expect(visibleNames()).toEqual(defaultNames)
  })

  it('uses defaults after asynchronous loading and prevents saving an empty catalog', async () => {
    const user = userEvent.setup()
    const view = render(<GlobalMarketOverview instruments={[]} loading />)
    expect(screen.getByRole('status').textContent).toContain('Loading')
    await user.click(screen.getByRole('button', { name: 'Customize' }))
    const groupCheckbox = screen.getByRole('checkbox', { name: 'Show China', exact: true }) as HTMLInputElement
    const moveGroup = screen.getByRole('button', { name: 'Move US up', exact: true }) as HTMLButtonElement
    expect(groupCheckbox.disabled).toBe(true)
    expect(moveGroup.disabled).toBe(true)
    await user.click(groupCheckbox)
    await user.click(moveGroup)
    expect(localStorage.getItem(storageKey)).toBeNull()
    view.rerender(<GlobalMarketOverview instruments={rows} />)
    expect(groupCheckbox.disabled).toBe(false)
    expect(visibleNames()).toEqual(defaultNames)
  })

  it('resolves Reset from the catalog that arrives later', async () => {
    const user = userEvent.setup()
    showAll()
    const view = render(<GlobalMarketOverview instruments={rows} />)
    await user.click(screen.getByRole('button', { name: 'Customize' }))
    view.rerender(<GlobalMarketOverview instruments={[]} loading />)
    await user.click(screen.getByRole('button', { name: 'Reset' }))
    expect(localStorage.getItem(storageKey)).toBeNull()
    view.rerender(<GlobalMarketOverview instruments={rows} />)
    expect(visibleNames()).toEqual(defaultNames)
  })

  it('honors saved group and instrument order and hidden choices without rewriting them', () => {
    const saved = { order: ['group:us', 'group:china', 'nasdaq', 'sp500'], hidden: ['group:hongKong', 'csi300', 'dow'] }
    const stored = JSON.stringify(saved)
    localStorage.setItem(storageKey, stored)
    const view = render(<GlobalMarketOverview instruments={rows} />)
    expect(screen.getAllByRole('heading', { level: 3 })[0].textContent).toBe('US')
    expect(visibleNames().slice(0, 2)).toEqual(['Nasdaq Composite', 'S&P 500'])
    expect(visibleNames()).toContain('STAR 50')
    expect(visibleNames()).not.toContain('CSI 300')
    expect(screen.queryByRole('heading', { name: 'Hong Kong', exact: true })).toBeNull()
    expect(localStorage.getItem(storageKey)).toBe(stored)
    view.unmount()
    render(<GlobalMarketOverview instruments={rows} />)
    expect(visibleNames().slice(0, 2)).toEqual(['Nasdaq Composite', 'S&P 500'])
    expect(loadOverviewPreferences()).toEqual(saved)
  })

  it.each(['null', '{}', '[]', '42', '"invalid"', '{bad json', '{"order":false,"hidden":"bad"}'])('recovers invalid preferences %s with the five-index defaults', (stored) => {
    localStorage.setItem(storageKey, stored)
    expect(loadOverviewPreferences()).toBeNull()
    render(<GlobalMarketOverview instruments={rows} />)
    expect(visibleNames()).toEqual(defaultNames)
  })

  it('retains valid partial preferences and normalizes stored string arrays', () => {
    localStorage.setItem(storageKey, JSON.stringify({ hidden: ['btc', 'btc', null, 42], order: ['group:us', 'group:us', false] }))
    expect(loadOverviewPreferences()).toEqual({ hidden: ['btc'], order: ['group:us'] })
    localStorage.setItem(storageKey, JSON.stringify({ hidden: [] }))
    expect(loadOverviewPreferences()).toEqual({ hidden: [], order: [] })
  })

  it('keeps defaults, customization and Reset usable when storage is unavailable', async () => {
    const user = userEvent.setup()
    for (const method of ['getItem', 'setItem', 'removeItem'] as const) {
      vi.spyOn(Storage.prototype, method).mockImplementation(() => { throw new Error('Storage disabled') })
    }
    render(<GlobalMarketOverview instruments={rows} />)
    expect(visibleNames()).toEqual(defaultNames)
    await user.click(screen.getByRole('button', { name: 'Customize' }))
    await user.click(screen.getByRole('checkbox', { name: 'Show Bitcoin', exact: true }))
    expect(visibleNames()).toContain('Bitcoin')
    await user.click(screen.getByRole('button', { name: 'Reset' }))
    expect(visibleNames()).toEqual(defaultNames)
  })

  it('removes empty groups when the five defaults are hidden and allows repeated Reset', async () => {
    const user = userEvent.setup()
    render(<GlobalMarketOverview instruments={rows} />)
    await user.click(screen.getByRole('button', { name: 'Customize' }))
    for (const name of defaultNames) {
      await user.click(screen.getByRole('checkbox', { name: `Show ${name}`, exact: true }))
    }
    expect(screen.queryAllByRole('article')).toHaveLength(0)
    expect(screen.queryAllByRole('heading', { level: 3 })).toHaveLength(0)
    expect(screen.getByText('All markets are hidden. Use Customize to show them.')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Reset' }))
    await user.click(screen.getByRole('button', { name: 'Reset' }))
    expect(visibleNames()).toEqual(defaultNames)
  })

  it('localizes the five default indices in Chinese', async () => {
    await i18n.changeLanguage('zh-CN')
    render(<GlobalMarketOverview instruments={rows} />)
    expect(visibleNames()).toEqual(['沪深300', '创业板指', '恒生指数', '标普500', '纳斯达克综合'])
  })

  it('shows yields in percent with basis-point movement and unavailable history', () => {
    showAll()
    render(<GlobalMarketOverview instruments={[{ ...row('us3m', 'fixedIncome'), instrument_type: 'yield', current_price: 4.25, change_bps: 2, change_pct: null, unit: 'percent', currency: null, status: 'daily', as_of: '2026-10-09' }]} />)
    expect(screen.getByText('4.250%')).toBeTruthy()
    expect(screen.getByText('+2.00 bp')).toBeTruthy()
    expect(screen.getByText('Chart unavailable')).toBeTruthy()
    expect(screen.getByText(/Observation date: 2026-10-09/)).toBeTruthy()
  })

  it('keeps a futures quote and chart neutral when its session change is unknown', () => {
    showAll()
    const corn: DashboardOverviewInstrument = {
      ...row('corn', 'commodities'), instrument_type: 'futures', provider_symbol: 'ZC=F',
      current_price: 500.50, prev_close: null, change_amount: null, change_pct: null,
      spark: [502, 500.25], unit: 'US cents/bushel', timezone: 'America/Chicago',
    }
    const view = render(<GlobalMarketOverview instruments={[corn]} />)
    const card = screen.getByRole('article')
    expect(within(card).getByText('500.50')).toBeTruthy()
    expect(within(card).getByText('--').classList.contains('text-market-flat')).toBe(true)
    expect(card.querySelector('svg')).not.toBeNull()
    expect(card.querySelector('.text-market-up, .text-market-down')).toBeNull()
    expect(within(card).queryByText('Chart unavailable')).toBeNull()

    view.rerender(<GlobalMarketOverview instruments={[{ ...corn, prev_close: 500.25, change_amount: 0.25, change_pct: (500.50 / 500.25 - 1) * 100 }]} />)
    expect(screen.getByText('+0.05 %').classList.contains('text-market-up')).toBe(true)
  })

  it.each([
    [-37.63, 10.01, 47.64, null, '--', 'up'],
    [-37.63, -10.01, 27.62, null, '--', 'up'],
    [-10.01, -37.63, -27.62, null, '--', 'down'],
    [-37.63, -37.63, 0, null, '--', 'flat'],
    [0, 10.01, 10.01, null, '--', 'up'],
    [0, -10.01, -10.01, null, '--', 'down'],
    [0, 0, 0, null, '--', 'flat'],
    [100, 110, 10, 10, '+10.00 %', 'up'],
    [100, 90, -10, -10, '-10.00 %', 'down'],
    [18, 0, -18, -100, '-100.00 %', 'down'],
    [18, -36, -54, -300, '-300.00 %', 'down'],
    [100, 100, 0, 0, '0.00 %', 'flat'],
    [null, 10.01, null, null, '--', 'flat'],
    [null, null, null, null, '--', 'flat'],
  ] as const)('uses the explicit futures direction for baseline %s and quote %s', (prior, price, amount, pct, label, direction) => {
    showAll()
    // Controlled API rows, including the historical WTI negative-price boundary;
    // this exercises the real overview and Sparkline components, not a mock.
    const oil: DashboardOverviewInstrument = {
      ...row('oil', 'commodities'), instrument_type: 'futures', provider_symbol: 'CL=F',
      current_price: price, prev_close: prior, change_amount: amount, change_pct: pct,
      spark: price == null ? [] : [prior ?? -37.63, price], unit: 'USD/barrel',
      status: price == null ? 'unavailable' : 'delayed',
    }
    render(<GlobalMarketOverview instruments={[oil]} />)
    const card = screen.getByRole('article')
    const change = within(card).getAllByText(label).at(-1)!
    expect(change.classList.contains(`text-market-${direction}`)).toBe(true)
    if (price == null) {
      expect(within(card).getAllByText('--')).toHaveLength(2)
      expect(card.querySelector('svg')).toBeNull()
    } else {
      expect(within(card).getByText(price.toFixed(2))).toBeTruthy()
      expect(card.querySelector('svg')?.classList.contains(`text-market-${direction}`)).toBe(true)
      const points = card.querySelector('polyline')!.getAttribute('points')!.split(' ').map(point => point.split(',').map(Number))
      expect(points.every(point => point.every(Number.isFinite))).toBe(true)
      expect(Math.sign(points[0][1] - points[1][1])).toBe(Math.sign(price - (prior ?? -37.63)))
    }
    if (pct == null) expect(card.textContent).not.toContain('%')
  })

  it('persists hiding and ordering across remounts and resets safely', async () => {
    const user = userEvent.setup()
    const view = render(<GlobalMarketOverview instruments={rows} />)
    await user.click(screen.getByRole('button', { name: 'Customize' }))
    await user.click(screen.getByRole('checkbox', { name: 'Show China', exact: true }))
    await user.click(screen.getByRole('button', { name: 'Move US up', exact: true }))
    await user.click(screen.getByRole('button', { name: 'Done' }))
    expect(screen.queryByRole('heading', { name: 'China', exact: true })).toBeNull()
    expect(screen.getAllByRole('heading', { level: 3 })[0].textContent).toBe('US')
    view.unmount()
    render(<GlobalMarketOverview instruments={rows} />)
    expect(screen.queryByRole('heading', { name: 'China', exact: true })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Customize' }))
    await user.click(screen.getByRole('button', { name: 'Reset' }))
    expect(visibleNames()).toEqual(defaultNames)
  })

  it('supports per-instrument hiding, reordering and repeated customize clicks', async () => {
    const user = userEvent.setup()
    render(<GlobalMarketOverview instruments={[row('sp500', 'us'), row('nasdaq', 'us'), row('dow', 'us')]} />)
    await user.click(screen.getByRole('button', { name: 'Customize' }))
    await user.click(screen.getByRole('button', { name: 'Move Nasdaq Composite up' }))
    expect(screen.getAllByRole('article')[0].getAttribute('aria-label')).toBe('Nasdaq Composite')
    await user.click(screen.getByRole('checkbox', { name: 'Show S&P 500' }))
    expect(visibleNames()).toEqual(['Nasdaq Composite'])
    await user.click(screen.getByRole('checkbox', { name: 'Show Dow Jones' }))
    expect(visibleNames()).toEqual(['Nasdaq Composite', 'Dow Jones'])
    await user.click(screen.getByRole('button', { name: 'Done' }))
    await user.click(screen.getByRole('button', { name: 'Customize' }))
    expect(screen.getByRole('checkbox', { name: 'Show S&P 500' }).getAttribute('checked')).toBeNull()
  })

  it('recovers malformed preferences and handles loading and unavailable values', () => {
    localStorage.setItem('panwatch.globalOverview.v1', 'null')
    expect(loadOverviewPreferences()).toBeNull()
    localStorage.setItem('panwatch.globalOverview.v1', '{bad json')
    const view = render(<GlobalMarketOverview instruments={[]} loading />)
    expect(screen.getByRole('status').textContent).toContain('Loading')
    view.rerender(<GlobalMarketOverview instruments={[{ ...row('csi300', 'china'), current_price: null, change_pct: null, as_of: null, status: 'unavailable' }]} />)
    expect(within(screen.getByRole('article')).getAllByText('--')).toHaveLength(2)
    expect(screen.getByText(/Observation time unavailable/)).toBeTruthy()
  })

  it('localizes Chinese group, instrument and state labels', async () => {
    await i18n.changeLanguage('zh-CN')
    showAll()
    render(<GlobalMarketOverview instruments={[{ ...row('star50', 'china'), status: 'stale' }]} />)
    expect(screen.getByRole('heading', { name: '中国' })).toBeTruthy()
    expect(screen.getByText('科创50')).toBeTruthy()
    expect(screen.getByText(/历史观测（已过期）/)).toBeTruthy()
  })
})
