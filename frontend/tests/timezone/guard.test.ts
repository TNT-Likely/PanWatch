import { expect, it } from 'vitest'
// @ts-expect-error The repository check is a JavaScript build tool.
import { findTimezoneViolations } from '../../scripts/check-timezones.mjs'

it.each([
  'const initial = { expire_at: rule.expire_at.slice(0, 16) }',
  'const date = trade.closed_at?.slice(0, 10)',
  'const text = hit["trigger_time"].substring(0, 16)',
  'const value = created_at.substr(0, 10)',
  'const formatter = new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Shanghai" })',
])('rejects truncation or an implicit shared display zone: %s', source => {
  expect(findTimezoneViolations(source)).toHaveLength(1)
})

it('allows date-only values, centralized conversion and explicit exchange/display zones', () => {
  expect(findTimezoneViolations(`
    // rule.expire_at.slice(0, 16) is forbidden.
    const initial = { expire_at: rule.expire_at || '' }
    const input = toLocalDateTimeInput(rule.expire_at)
    const date = localDateForInstant(trade.closed_at)
    const reportDate = result.analysis_date.slice(0, 10)
    const display = new Intl.DateTimeFormat(locale, { timeZone: browserTimezone() })
    const exchange = new Intl.DateTimeFormat(locale, { timeZone: exchangeTimezone(market) })
    const weekdayForDateOnly = new Intl.DateTimeFormat(locale, { timeZone: 'UTC' })
  `)).toEqual([])
})
