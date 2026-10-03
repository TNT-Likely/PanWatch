import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchAPI } from '@panwatch/api'
import { calendarTime, MarketCalendarStatus, type MarketStatus } from '@/components/MarketCalendarStatus'

vi.mock('@panwatch/api', () => ({ fetchAPI: vi.fn() }))
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.clearAllMocks() })

const markets: MarketStatus[] = ['CN', 'HK', 'US'].map(code => ({
  code, status: code === 'US' ? 'after_hours' : 'closed', status_text: '',
  is_trading: false, sessions: [], local_time: code === 'US' ? '20:30' : '08:30',
  local_date: code === 'US' ? '2026-10-02' : '2026-10-03',
  timezone: code === 'US' ? 'America/New_York' : 'Asia/Shanghai',
}))
const labelMarket = (code: string) => ({ CN: 'A股', HK: '港股', US: '美股' })[code] ?? code
const data = {
  timezone: 'Asia/Shanghai', markets: markets.map(market => ({
    market: market.code, status: market.status, timezone: market.timezone,
    local_date: market.local_date, local_time: market.local_time,
    next_open: market.code === 'US' ? '2026-10-05T09:30:00-04:00' : '2026-10-05T09:30:00+08:00',
    days: [{ date: '2026-10-05', reason: market.code === 'CN' ? 'holiday' : 'trading',
      is_trading_day: market.code !== 'CN', session_times: market.code === 'US' ? [{
        open: '2026-10-05T09:30:00-04:00', close: '2026-10-05T16:00:00-04:00',
      }] : [] }],
  })),
}

function beijingTimezone() {
  const resolvedOptions = Intl.DateTimeFormat.prototype.resolvedOptions
  vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockImplementation(function () {
    return { ...resolvedOptions.call(this), timeZone: 'Asia/Shanghai' }
  })
}

describe('market calendar', () => {
  it('converts US opening times with daylight saving and midnight correctly', () => {
    expect(calendarTime('2026-10-05T09:30:00-04:00', 'Asia/Shanghai', 'zh-CN')).toMatchObject({ date: '2026-10-05', clock: '21:30' })
    expect(calendarTime('2026-11-02T09:30:00-05:00', 'Asia/Shanghai', 'zh-CN')).toMatchObject({ date: '2026-11-02', clock: '22:30' })
    expect(calendarTime('2026-10-05T12:00:00-04:00', 'Asia/Shanghai', 'en-US').clock).toBe('00:00')
  })

  it('loads all markets only when opened and explains their different local dates', async () => {
    beijingTimezone()
    vi.mocked(fetchAPI).mockResolvedValue(data)
    const view = render(<MarketCalendarStatus markets={markets} labelMarket={labelMarket} />)
    expect(fetchAPI).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: '查看各市场交易日历' }))
    const table = await screen.findByRole('table')
    expect(within(table).getAllByRole('columnheader').map(node => node.textContent)).toEqual(['交易日期', 'A股', '港股', '美股'])
    expect(screen.getByText('21:30')).toBeTruthy()
    expect(screen.getByText('21:30 – 次日 04:00')).toBeTruthy()
    expect(screen.getByText('当地 10/02 20:30')).toBeTruthy()
    expect(screen.getByText(/状态按市场当地日期判断/)).toBeTruthy()
    expect(fetchAPI).toHaveBeenCalledWith('/stocks/markets/calendar?days=14&timezone=Asia%2FShanghai', expect.objectContaining({ signal: expect.any(AbortSignal) }))
    view.rerender(<MarketCalendarStatus markets={markets.map(market => ({ ...market, status: 'trading' }))} labelMarket={labelMarket} />)
    await waitFor(() => expect(fetchAPI).toHaveBeenCalledTimes(2))
    await screen.findByRole('table')
    await userEvent.click(screen.getByRole('button', { name: '关闭交易日历' }))
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('allows retrying a failed calendar request', async () => {
    vi.mocked(fetchAPI).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(data)
    render(<MarketCalendarStatus markets={markets} labelMarket={labelMarket} />)
    await userEvent.click(screen.getByRole('button', { name: '查看各市场交易日历' }))
    await screen.findByText('交易日历读取失败')
    await userEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByRole('table')).toBeTruthy()
    expect(fetchAPI).toHaveBeenCalledTimes(2)
  })
})
