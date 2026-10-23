import i18n, { type BackendModule } from 'i18next'
import { initReactI18next } from 'react-i18next'

import { resourceLoaders } from './loaders'

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

export function detectInitialLocale(
  storedLocale: string | null | undefined,
  browserLanguages: readonly string[] = [],
): SupportedLocale {
  if (isSupportedLocale(storedLocale)) return storedLocale
  const browserLocale = browserLanguages.find(Boolean)?.toLowerCase()
  return browserLocale?.startsWith('zh') ? 'zh-CN' : 'en-US'
}

function readInitialLocale(): SupportedLocale {
  if (typeof window === 'undefined') return DEFAULT_LOCALE
  const browserLanguages = typeof navigator === 'undefined'
    ? []
    : navigator.languages?.length
      ? navigator.languages
      : [navigator.language]
  return detectInitialLocale(window.localStorage.getItem(LOCALE_STORAGE_KEY), browserLanguages)
}

function applyLocale(locale: string) {
  const normalized = normalizeLocale(locale)
  if (typeof document !== 'undefined') {
    const metadata = i18n.getResource(normalized, 'common', 'product')
    if (!metadata) return
    document.documentElement.lang = normalized
    document.title = metadata.pageTitle
    document.querySelector<HTMLMetaElement>('meta[name="description"]')?.setAttribute('content', metadata.description)
    document.querySelector<HTMLMetaElement>('meta[name="apple-mobile-web-app-title"]')?.setAttribute('content', metadata.shortTitle)
    document.querySelector<HTMLLinkElement>('link[rel="manifest"]')?.setAttribute('href', normalized === 'zh-CN' ? '/manifest.zh-CN.json' : '/manifest.json')
  }
  if (typeof window !== 'undefined') window.localStorage.setItem(LOCALE_STORAGE_KEY, normalized)
}

i18n.on('languageChanged', applyLocale)

const backend: BackendModule = {
  type: 'backend',
  init() {},
  read(language, namespace, callback) {
    const loader = resourceLoaders[normalizeLocale(language)][namespace as keyof typeof resourceLoaders['zh-CN']]
    if (!loader) { callback(new Error(`Unknown translation namespace: ${namespace}`), false); return }
    void loader().then(resource => callback(null, resource), error => callback(error, false))
  },
}

export const i18nReady = i18n
  .use(backend)
  .use(initReactI18next)
  .init({
    lng: readInitialLocale(),
    fallbackLng: DEFAULT_LOCALE,
    supportedLngs: [...SUPPORTED_LOCALES],
    defaultNS: 'common',
    ns: ['common', 'auth', 'navigation'],
    load: 'currentOnly',
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
