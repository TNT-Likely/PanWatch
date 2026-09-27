import { afterEach, describe, expect, it } from 'vitest'

import i18n, {
  changeLocale,
  getCurrentLocale,
  LOCALE_STORAGE_KEY,
  normalizeLocale,
} from '@/i18n'
import {
  formatCurrency,
  formatDate,
  formatMarketName,
  formatPercent,
} from '@/i18n/format'

afterEach(async () => {
  await changeLocale('zh-CN')
  window.localStorage.removeItem(LOCALE_STORAGE_KEY)
})

describe('internationalization runtime', () => {
  it('normalizes unknown locales to Chinese without browser-language detection', () => {
    expect(normalizeLocale('zh-CN')).toBe('zh-CN')
    expect(normalizeLocale('en-GB')).toBe('en-US')
    expect(normalizeLocale('ja-JP')).toBe('zh-CN')
    expect(normalizeLocale(null)).toBe('zh-CN')
  })

  it('persists language changes and updates the document language', async () => {
    await changeLocale('en-US')

    expect(getCurrentLocale()).toBe('en-US')
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en-US')
    expect(document.documentElement.lang).toBe('en-US')
    expect(i18n.t('navigation:items.portfolio')).toBe('Portfolio')
  })

  it('uses Chinese as the configured fallback language', () => {
    expect(i18n.options.fallbackLng).toEqual(['zh-CN'])
  })
})

describe('locale-sensitive formatters', () => {
  it('formats interface values without coupling locale to currency or market', async () => {
    await changeLocale('en-US')

    expect(formatCurrency(1234.5, 'CNY', { minimumFractionDigits: 2 })).toContain('1,234.50')
    expect(formatPercent(0.125)).toBe('12.5%')
    expect(formatMarketName('CN')).toBe('China A-shares')
    expect(formatDate('2026-09-27T00:00:00Z', { timeZone: 'UTC', year: 'numeric' })).toBe('2026')
  })
})
