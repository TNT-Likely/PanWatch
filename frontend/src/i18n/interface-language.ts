import { useEffect, useState } from 'react'
import { fetchAPI } from '@panwatch/api'
import i18n, { getCurrentLocale, normalizeLocale, type SupportedLocale } from '@/i18n'

let lastSyncedInterfaceLanguage: SupportedLocale | null = null

export async function syncInterfaceLanguage(language: SupportedLocale): Promise<void> {
  if (lastSyncedInterfaceLanguage === language) return
  lastSyncedInterfaceLanguage = language
  try {
    await fetchAPI('/settings/ui_language', {
      method: 'PUT',
      body: JSON.stringify({ value: language }),
    })
  } catch (error) {
    if (lastSyncedInterfaceLanguage === language) {
      lastSyncedInterfaceLanguage = null
    }
    throw error
  }
}

/** Persist the interface language so background AI jobs use the same locale. */
export function useInterfaceLanguage(): SupportedLocale {
  const [language, setLanguage] = useState<SupportedLocale>(getCurrentLocale)

  useEffect(() => {
    let active = true
    const onLanguageChanged = (next: string) => {
      const normalized = normalizeLocale(next)
      if (active) setLanguage(normalized)
      void syncInterfaceLanguage(normalized).catch(() => undefined)
    }
    onLanguageChanged(i18n.resolvedLanguage || i18n.language)
    i18n.on('languageChanged', onLanguageChanged)
    return () => {
      active = false
      i18n.off('languageChanged', onLanguageChanged)
    }
  }, [])

  return language
}
