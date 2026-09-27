import i18n, { getCurrentLocale, type SupportedLocale } from './index'

export type MarketCode = 'CN' | 'HK' | 'US'

const MARKET_CURRENCIES: Record<MarketCode, string> = {
  CN: 'CNY',
  HK: 'HKD',
  US: 'USD',
}

function localeOrCurrent(locale?: SupportedLocale): SupportedLocale {
  return locale ?? getCurrentLocale()
}

export function formatNumber(
  value: number,
  options?: Intl.NumberFormatOptions,
  locale?: SupportedLocale,
): string {
  return new Intl.NumberFormat(localeOrCurrent(locale), options).format(value)
}

export function formatPercent(
  value: number,
  options?: Intl.NumberFormatOptions,
  locale?: SupportedLocale,
): string {
  return formatNumber(value, { style: 'percent', maximumFractionDigits: 2, ...options }, locale)
}

export function formatCurrency(
  value: number,
  currency: string,
  options?: Intl.NumberFormatOptions,
  locale?: SupportedLocale,
): string {
  return formatNumber(value, { style: 'currency', currency, ...options }, locale)
}

export function formatMarketCurrency(
  value: number,
  market: MarketCode,
  options?: Intl.NumberFormatOptions,
  locale?: SupportedLocale,
): string {
  return formatCurrency(value, MARKET_CURRENCIES[market], options, locale)
}

export function formatDate(
  value: Date | string | number,
  options?: Intl.DateTimeFormatOptions,
  locale?: SupportedLocale,
): string {
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat(localeOrCurrent(locale), options).format(date)
}

export function formatMarketName(market: MarketCode | 'all' | string): string {
  if (market === 'CN' || market === 'HK' || market === 'US' || market === 'all') {
    return i18n.t(`common:markets.${market}`)
  }
  return market
}
