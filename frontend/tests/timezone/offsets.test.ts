import { afterEach, expect, it, vi } from 'vitest'
import { browserTimezone, expiryToISO, localDateForInstant, toLocalDateTimeInput } from '@panwatch/base-ui'

afterEach(() => { vi.unstubAllEnvs() })

it.each([
  ['UTC', '2026-10-10T00:30:12.345'],
  ['Asia/Shanghai', '2026-10-10T08:30:12.345'],
  ['America/New_York', '2026-10-09T20:30:12.345'],
  ['Asia/Kolkata', '2026-10-10T06:00:12.345'],
  ['Asia/Kathmandu', '2026-10-10T06:15:12.345'],
  ['Pacific/Kiritimati', '2026-10-10T14:30:12.345'],
])('converts across dates and offsets in %s', (zone, local) => {
  vi.stubEnv('TZ', zone)
  const original = '2026-10-10T00:30:12.345678Z'
  // ICU may return historical IANA aliases (Calcutta/Katmandu).
  expect(browserTimezone()).toBe(new Intl.DateTimeFormat('en', { timeZone: zone }).resolvedOptions().timeZone)
  expect(toLocalDateTimeInput(original)).toBe(local)
  expect(localDateForInstant(original)).toBe(local.slice(0, 10))
  expect(expiryToISO(local, original)).toBe(original)
  expect(expiryToISO(local.slice(0, 16))).toBe('2026-10-10T00:30:00.000Z')
})

it('does not reinterpret a market date or an unqualified timestamp as an instant', () => {
  expect(localDateForInstant('2026-10-10')).toBe('')
  expect(toLocalDateTimeInput('2026-10-10T12:00')).toBe('')
  expect(localDateForInstant(null)).toBe('')
  expect(localDateForInstant('invalid')).toBe('')
})
