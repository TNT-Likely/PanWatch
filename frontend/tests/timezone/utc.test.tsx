import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { browserTimezone, expiryToISO, toLocalDateTimeInput } from '@panwatch/base-ui'
import PriceAlertFormDialog from '@panwatch/biz-ui/components/price-alert-form-dialog'
import { homeApi } from '@panwatch/api'

beforeEach(() => { vi.stubEnv('TZ', 'UTC') })
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs() })

it('uses UTC browser time and preserves an unchanged offset-bearing expiry', () => {
  expect(browserTimezone()).toBe('UTC')
  const instant = '2026-10-10T16:00:12.345+08:00'
  expect(toLocalDateTimeInput(instant)).toBe('2026-10-10T08:00:12.345')
  expect(expiryToISO(toLocalDateTimeInput(instant), instant)).toBe(instant)
  expect(expiryToISO('2026-10-10T09:15')).toBe('2026-10-10T09:15:00.000Z')
  expect(expiryToISO('')).toBeNull()
  expect(() => expiryToISO('invalid')).toThrow(RangeError)
})

it('saves a rule through the visible form without adding eight hours', async () => {
  const onSubmit = vi.fn()
  render(<PriceAlertFormDialog open onOpenChange={() => {}} title="编辑" description="UTC expiry" stocks={[{ id: 1, symbol: 'AAPL', name: 'Apple', market: 'US' }]} submitLabel="保存" initial={{ stock_id: 1, expire_at: '2026-10-10T16:00:12.345678+08:00' }} onSubmit={onSubmit} />)
  expect(await screen.findByDisplayValue('08:00')).toBeTruthy()
  expect(screen.getByText('(UTC)')).toBeTruthy()
  await userEvent.click(screen.getByRole('button', { name: '保存' }))
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ expire_at: '2026-10-10T16:00:12.345678+08:00' }))
})

it('sends the viewer timezone with the dashboard today request', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 0, data: [] }), { status: 200 }))
  vi.stubGlobal('fetch', fetch)
  await homeApi.alertHitsToday()
  expect(fetch).toHaveBeenCalledWith('/api/price-alerts/hits/today?timezone=UTC', expect.any(Object))
})
