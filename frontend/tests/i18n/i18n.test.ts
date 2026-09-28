import { afterEach, describe, expect, it } from 'vitest'

import i18n, {
  changeLocale,
  detectInitialLocale,
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

  it('uses a saved preference before browser language', () => {
    expect(detectInitialLocale('zh-CN', ['en-US'])).toBe('zh-CN')
    expect(detectInitialLocale('en-US', ['zh-CN'])).toBe('en-US')
  })

  it('defaults new visitors by browser language', () => {
    expect(detectInitialLocale(null, ['zh-Hans-CN'])).toBe('zh-CN')
    expect(detectInitialLocale(null, ['en-GB'])).toBe('en-US')
    expect(detectInitialLocale(null, ['ja-JP'])).toBe('en-US')
    expect(detectInitialLocale(null, [])).toBe('en-US')
  })

  it('persists language changes and updates the document language', async () => {
    const description = document.createElement('meta')
    description.name = 'description'
    const appTitle = document.createElement('meta')
    appTitle.name = 'apple-mobile-web-app-title'
    const manifest = document.createElement('link')
    manifest.rel = 'manifest'
    document.head.append(description, appTitle, manifest)

    await changeLocale('en-US')

    expect(getCurrentLocale()).toBe('en-US')
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en-US')
    expect(document.documentElement.lang).toBe('en-US')
    expect(document.title).toBe('PanWatch | AI stock monitoring')
    expect(description.content).toContain('TradingAgents')
    expect(appTitle.content).toBe('PanWatch')
    expect(manifest.getAttribute('href')).toBe('/manifest.json')
    expect(i18n.t('navigation:items.portfolio')).toBe('Portfolio')

    await changeLocale('zh-CN')
    expect(document.title).toBe('盯盘侠 | PanWatch')
    expect(description.content).toContain('A 股')
    expect(appTitle.content).toBe('盯盘侠')
    expect(manifest.getAttribute('href')).toBe('/manifest.zh-CN.json')

    description.remove()
    appTitle.remove()
    manifest.remove()
  })

  it('uses Chinese as the configured fallback language', () => {
    expect(i18n.options.fallbackLng).toEqual(['zh-CN'])
  })

  it('resolves scoped Agent and price-alert interface copy', async () => {
    expect(i18n.t('configuration:agentsPage.pageTitle')).toBe('Agent')
    expect(i18n.t('configuration:agentsPage.schedule.weekdaysHint')).toBe('周一至周五')
    expect(i18n.t('configuration:priceAlerts.form.stock')).toBe('股票')
    expect(i18n.t('bizUi:stockPriceAlert.between')).toBe('介于')

    await changeLocale('en-US')

    expect(i18n.t('configuration:agentsPage.pageTitle')).toBe('Agents')
    expect(i18n.t('configuration:agentsPage.schedule.weekdaysHint')).toBe('Monday to Friday')
    expect(i18n.t('configuration:priceAlerts.form.stock')).toBe('Stock')
    expect(i18n.t('bizUi:stockPriceAlert.between')).toBe('between')
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
