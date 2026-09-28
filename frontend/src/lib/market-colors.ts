import type { SupportedLocale } from '@/i18n'

export const MARKET_COLOR_STORAGE_KEY = 'panwatch-market-color-scheme'

export type MarketColorPreference = 'auto' | 'red-up' | 'green-up'
export type EffectiveMarketColorScheme = Exclude<MarketColorPreference, 'auto'>
export type MarketDirection = 'up' | 'down' | 'flat'

export interface MarketTone {
  text: string
  bright: string
  soft: string
  border: string
  gradientFrom: string
}

export interface MarketColorPalette {
  up: MarketTone
  down: MarketTone
  flat: string
}

const RED: MarketTone = {
  text: '#e11d48',
  bright: '#ef4444',
  soft: '#fff1f2',
  border: '#fecdd3',
  gradientFrom: '#fb7185',
}

const GREEN: MarketTone = {
  text: '#059669',
  bright: '#10b981',
  soft: '#ecfdf5',
  border: '#a7f3d0',
  gradientFrom: '#34d399',
}

export function normalizeMarketColorPreference(value: string | null | undefined): MarketColorPreference {
  return value === 'red-up' || value === 'green-up' || value === 'auto' ? value : 'auto'
}

export function resolveMarketColorScheme(
  preference: MarketColorPreference,
  locale: SupportedLocale | string,
): EffectiveMarketColorScheme {
  if (preference !== 'auto') return preference
  return locale.toLowerCase().startsWith('en') ? 'green-up' : 'red-up'
}

export function getMarketColorPalette(scheme: EffectiveMarketColorScheme): MarketColorPalette {
  return scheme === 'green-up'
    ? { up: GREEN, down: RED, flat: '#64748b' }
    : { up: RED, down: GREEN, flat: '#64748b' }
}

export function marketColorWithAlpha(hex: string, alpha: number): string {
  const normalized = hex.replace('#', '')
  if (!/^[0-9a-f]{6}$/i.test(normalized)) return hex
  const red = Number.parseInt(normalized.slice(0, 2), 16)
  const green = Number.parseInt(normalized.slice(2, 4), 16)
  const blue = Number.parseInt(normalized.slice(4, 6), 16)
  return `rgba(${red}, ${green}, ${blue}, ${Math.max(0, Math.min(1, alpha))})`
}

export function marketDirection(value: number | null | undefined): MarketDirection {
  if (value == null || !Number.isFinite(value) || value === 0) return 'flat'
  return value > 0 ? 'up' : 'down'
}

export function marketSignTextClass(value: number | null | undefined): string {
  const direction = marketDirection(value)
  if (direction === 'up') return 'text-market-up'
  if (direction === 'down') return 'text-market-down'
  return 'text-market-flat'
}

export function readMarketColorPreference(storage?: Pick<Storage, 'getItem'>): MarketColorPreference {
  const target = storage ?? (typeof window !== 'undefined' ? window.localStorage : undefined)
  return normalizeMarketColorPreference(target?.getItem(MARKET_COLOR_STORAGE_KEY))
}

export function applyMarketColorScheme(
  preference: MarketColorPreference,
  locale: SupportedLocale | string,
  root?: HTMLElement,
): EffectiveMarketColorScheme {
  const effective = resolveMarketColorScheme(preference, locale)
  const target = root ?? (typeof document !== 'undefined' ? document.documentElement : undefined)
  if (target) target.dataset.marketColorScheme = effective
  return effective
}
