import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

import { resources } from './resources'

export const DEFAULT_LOCALE = 'zh-CN' as const
export const LOCALE_STORAGE_KEY = 'panwatch-locale'
export const SUPPORTED_LOCALES = ['zh-CN', 'en-US'] as const

export type SupportedLocale = (typeof SUPPORTED_LOCALES)[number]

export function isSupportedLocale(value: string | null | undefined): value is SupportedLocale {
  return SUPPORTED_LOCALES.includes(value as SupportedLocale)
}

export function normalizeLocale(value: string | null | undefined): SupportedLocale {
  if (isSupportedLocale(value)) return value
  if (value?.toLowerCase().startsWith('en')) return 'en-US'
  return DEFAULT_LOCALE
}

function readStoredLocale(): SupportedLocale {
  if (typeof window === 'undefined') return DEFAULT_LOCALE
  return normalizeLocale(window.localStorage.getItem(LOCALE_STORAGE_KEY))
}

function applyLocale(locale: string) {
  const normalized = normalizeLocale(locale)
  if (typeof document !== 'undefined') document.documentElement.lang = normalized
  if (typeof window !== 'undefined') window.localStorage.setItem(LOCALE_STORAGE_KEY, normalized)
}

i18n.on('languageChanged', applyLocale)

void i18n
  .use(initReactI18next)
  .init({
    resources,
    lng: readStoredLocale(),
    fallbackLng: DEFAULT_LOCALE,
    supportedLngs: [...SUPPORTED_LOCALES],
    defaultNS: 'common',
    ns: ['common', 'auth', 'navigation', 'settings'],
    interpolation: {
      escapeValue: false,
    },
    returnNull: false,
    initAsync: false,
  })

export function getCurrentLocale(): SupportedLocale {
  return normalizeLocale(i18n.resolvedLanguage || i18n.language)
}

export async function changeLocale(locale: SupportedLocale): Promise<void> {
  await i18n.changeLanguage(locale)
}

export default i18n
