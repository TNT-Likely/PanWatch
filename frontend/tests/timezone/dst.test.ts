import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { browserTimezone, expiryToISO, toLocalDateTimeInput } from '@panwatch/base-ui'

beforeEach(() => { vi.stubEnv('TZ', 'America/New_York') })
afterEach(() => { vi.unstubAllEnvs() })

it('retains the second occurrence of a repeated hour when editing without changes', () => {
  expect(browserTimezone()).toBe('America/New_York')
  const instant = '2026-11-01T01:30:12.123-05:00'
  expect(toLocalDateTimeInput(instant)).toBe('2026-11-01T01:30:12.123')
  expect(expiryToISO(toLocalDateTimeInput(instant), instant)).toBe(instant)
})

it('rejects the skipped spring hour and correctly converts dates across midnight', () => {
  expect(() => expiryToISO('2026-03-08T02:30')).toThrow(RangeError)
  expect(toLocalDateTimeInput('2026-10-10T00:30:00Z')).toBe('2026-10-09T20:30:00.000')
  expect(expiryToISO('2026-10-09T20:30')).toBe('2026-10-10T00:30:00.000Z')
})
