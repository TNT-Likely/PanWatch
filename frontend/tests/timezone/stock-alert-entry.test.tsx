import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import StockPriceAlertPanel from '@panwatch/biz-ui/components/stock-price-alert-panel'

const api = vi.hoisted(() => ({ fetch: vi.fn(), stocks: vi.fn(), toast: vi.fn() }))
vi.mock('@panwatch/api', () => ({ fetchAPI: api.fetch, stocksApi: { list: api.stocks } }))
vi.mock('@panwatch/base-ui/components/ui/toast', () => ({ useToast: () => ({ toast: api.toast }) }))
vi.mock('@panwatch/base-ui/components/ui/confirm-dialog', () => ({ useConfirm: () => vi.fn() }))

afterEach(() => { cleanup(); vi.resetAllMocks(); vi.unstubAllEnvs() })

const zones = [
  ['UTC', '08:00', '2026-12-10T09:15:00.000Z', '2026-12-10T16:00:12.345678+08:00'],
  ['Asia/Shanghai', '16:00', '2026-12-10T01:15:00.000Z', '2026-12-10T16:00:12.345678+08:00'],
  ['America/New_York', '03:00', '2026-12-10T14:15:00.000Z', '2026-12-10T16:00:12.345678+08:00'],
  ['Asia/Kolkata', '13:30', '2026-12-10T03:45:00.000Z', '2026-12-10T16:00:12.345678+08:00'],
  ['Asia/Kathmandu', '13:45', '2026-12-10T03:30:00.000Z', '2026-12-10T16:00:12.345678+08:00'],
  ['Pacific/Kiritimati', '22:00', '2026-12-09T19:15:00.000Z', '2026-12-10T16:00:12.345678+08:00'],
  ['America/New_York', '01:30', '2026-11-01T14:15:00.000Z', '2026-11-01T01:30:12.345678-05:00'],
] as const

it.each(zones)('preserves, edits and cancels expiry through the stock entry in %s (%s)', async (zone, localTime, editedInstant, instant) => {
  vi.stubEnv('TZ', zone)
  const rule = {
    id: 101, stock_id: 1, stock_symbol: 'AAPL', market: 'US', name: 'Timezone rule', enabled: false,
    condition_group: { op: 'and', items: [{ type: 'price', op: '>=', value: 9999 }] },
    expire_at: instant, notify_channel_ids: [],
  }
  api.stocks.mockResolvedValue([{ id: 1, symbol: 'AAPL', name: 'Apple', market: 'US' }])
  api.fetch.mockImplementation(async (path, options) => {
    if (options?.method === 'PUT') { rule.expire_at = JSON.parse(options.body).expire_at; return rule }
    return path === '/price-alerts' ? [rule] : []
  })
  render(<StockPriceAlertPanel symbol="AAPL" market="US" stockId={1} mode="inline" initialTotal={1} />)
  await userEvent.click(screen.getByRole('button', { name: '提醒 0/1' }))
  const row = (await screen.findByText('Timezone rule')).closest('.rounded-lg') as HTMLElement
  await userEvent.click(within(row).getByRole('button', { name: '编辑提醒规则' }))
  expect(await screen.findByDisplayValue(localTime)).toBeTruthy()
  await userEvent.click(screen.getByRole('button', { name: '保存规则' }))
  await waitFor(() => expect(api.fetch).toHaveBeenCalledWith('/price-alerts/101', {
    method: 'PUT', body: expect.any(String),
  }))
  const [, request] = api.fetch.mock.calls.find(([, options]) => options?.method === 'PUT')!
  expect(JSON.parse(request.body).expire_at).toBe(instant)
  await waitFor(() => expect(screen.queryByDisplayValue(localTime)).toBeNull())
  await userEvent.click(screen.getByRole('button', { name: '编辑提醒规则' }))
  const input = await screen.findByDisplayValue(localTime)
  fireEvent.change(input, { target: { value: '09:15' } })
  await userEvent.click(screen.getByRole('button', { name: '保存规则' }))
  await waitFor(() => expect(rule.expire_at).toBe(editedInstant))
  await waitFor(() => expect(screen.queryByDisplayValue('09:15')).toBeNull())
  await userEvent.click(screen.getByRole('button', { name: '编辑提醒规则' }))
  expect(await screen.findByDisplayValue('09:15')).toBeTruthy()
  await userEvent.keyboard('{Escape}')
  expect(api.fetch.mock.calls.filter(([, options]) => options?.method === 'PUT')).toHaveLength(2)
})
