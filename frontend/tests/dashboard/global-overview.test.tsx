import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
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
const rows = ['csi300', 'hsi', 'sp500', 'tsx', 'dax', 'nikkei225', 'gold', 'btc', 'us3m'].map((id, i) => row(id, OVERVIEW_GROUPS[i]))

beforeEach(async () => { localStorage.clear(); await i18n.changeLanguage('en-US') })
afterEach(() => { cleanup(); localStorage.clear() })

describe('global market overview', () => {
  it('renders all nine groups without a five-instrument cap', () => {
    render(<GlobalMarketOverview instruments={rows} />)
    expect(screen.getAllByRole('article')).toHaveLength(9)
    for (const label of ['China', 'Hong Kong', 'US', 'Canada', 'Europe', 'Asia', 'Commodities / Futures', 'Crypto', 'Fixed income']) {
      expect(screen.getByRole('heading', { name: label })).toBeTruthy()
    }
  })

  it('shows yields in percent with basis-point movement and unavailable history', () => {
    render(<GlobalMarketOverview instruments={[{ ...row('us3m', 'fixedIncome'), instrument_type: 'yield', current_price: 4.25, change_bps: 2, change_pct: null, unit: 'percent', currency: null, status: 'daily', as_of: '2026-10-09' }]} />)
    expect(screen.getByText('4.250%')).toBeTruthy()
    expect(screen.getByText('+2.00 bp')).toBeTruthy()
    expect(screen.getByText('Chart unavailable')).toBeTruthy()
    expect(screen.getByText(/Observation date: 2026-10-09/)).toBeTruthy()
  })

  it('keeps a futures quote and chart neutral when its session change is unknown', () => {
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
    expect(screen.getAllByRole('article')).toHaveLength(9)
  })

  it('supports per-instrument hiding, reordering and repeated customize clicks', async () => {
    const user = userEvent.setup()
    render(<GlobalMarketOverview instruments={[row('sp500', 'us'), row('nasdaq', 'us'), row('dow', 'us')]} />)
    await user.click(screen.getByRole('button', { name: 'Customize' }))
    await user.click(screen.getByRole('button', { name: 'Move Nasdaq Composite up' }))
    expect(screen.getAllByRole('article')[0].getAttribute('aria-label')).toBe('Nasdaq Composite')
    await user.click(screen.getByRole('checkbox', { name: 'Show S&P 500' }))
    expect(screen.getAllByRole('article')).toHaveLength(2)
    await user.click(screen.getByRole('button', { name: 'Done' }))
    await user.click(screen.getByRole('button', { name: 'Customize' }))
    expect(screen.getByRole('checkbox', { name: 'Show S&P 500' }).getAttribute('checked')).toBeNull()
  })

  it('recovers malformed preferences and handles loading and unavailable values', () => {
    localStorage.setItem('panwatch.globalOverview.v1', 'null')
    expect(loadOverviewPreferences()).toEqual({ order: [], hidden: [] })
    localStorage.setItem('panwatch.globalOverview.v1', '{bad json')
    const view = render(<GlobalMarketOverview instruments={[]} loading />)
    expect(screen.getByRole('status').textContent).toContain('Loading')
    view.rerender(<GlobalMarketOverview instruments={[{ ...row('btc', 'crypto'), current_price: null, change_pct: null, as_of: null, status: 'unavailable' }]} />)
    expect(within(screen.getByRole('article')).getAllByText('--')).toHaveLength(2)
    expect(screen.getByText(/Observation time unavailable/)).toBeTruthy()
  })

  it('localizes Chinese group, instrument and state labels', async () => {
    await i18n.changeLanguage('zh-CN')
    render(<GlobalMarketOverview instruments={[{ ...row('star50', 'china'), status: 'stale' }]} />)
    expect(screen.getByRole('heading', { name: '中国' })).toBeTruthy()
    expect(screen.getByText('科创50')).toBeTruthy()
    expect(screen.getByText(/历史观测（已过期）/)).toBeTruthy()
  })
})
