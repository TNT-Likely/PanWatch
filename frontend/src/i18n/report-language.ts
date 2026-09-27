import { useEffect, useState } from 'react'
import { fetchAPI } from '@panwatch/api'

export const REPORT_LANGUAGE_STORAGE_KEY = 'panwatch-ai-report-language'
export const REPORT_LANGUAGES = ['zh-CN', 'en-US'] as const
export type ReportLanguage = (typeof REPORT_LANGUAGES)[number]

const CHANGE_EVENT = 'panwatch:ai-report-language-changed'

function isReportLanguage(value: unknown): value is ReportLanguage {
  return REPORT_LANGUAGES.includes(value as ReportLanguage)
}

export function getStoredReportLanguage(): ReportLanguage {
  if (typeof window === 'undefined') return 'zh-CN'
  const stored = window.localStorage.getItem(REPORT_LANGUAGE_STORAGE_KEY)
  return isReportLanguage(stored) ? stored : 'zh-CN'
}

export function setStoredReportLanguage(language: ReportLanguage): void {
  window.localStorage.setItem(REPORT_LANGUAGE_STORAGE_KEY, language)
  window.dispatchEvent(new CustomEvent<ReportLanguage>(CHANGE_EVENT, { detail: language }))
}

interface AppSetting {
  key: string
  value: string
}

export async function loadReportLanguage(): Promise<ReportLanguage> {
  const settings = await fetchAPI<AppSetting[]>('/settings')
  const value = settings.find((setting) => setting.key === 'ai_report_language')?.value
  const language = isReportLanguage(value) ? value : 'zh-CN'
  setStoredReportLanguage(language)
  return language
}

export async function saveReportLanguage(language: ReportLanguage): Promise<void> {
  const previous = getStoredReportLanguage()
  setStoredReportLanguage(language)
  try {
    await fetchAPI('/settings/ai_report_language', {
      method: 'PUT',
      body: JSON.stringify({ value: language }),
    })
  } catch (error) {
    setStoredReportLanguage(previous)
    throw error
  }
}

export function useReportLanguage(): ReportLanguage {
  const [language, setLanguage] = useState<ReportLanguage>(getStoredReportLanguage)

  useEffect(() => {
    let active = true
    loadReportLanguage().catch(() => undefined)
    const onChange = (event: Event) => {
      const next = (event as CustomEvent<ReportLanguage>).detail
      if (active && isReportLanguage(next)) setLanguage(next)
    }
    const onStorage = (event: StorageEvent) => {
      if (event.key === REPORT_LANGUAGE_STORAGE_KEY && active) setLanguage(getStoredReportLanguage())
    }
    window.addEventListener(CHANGE_EVENT, onChange)
    window.addEventListener('storage', onStorage)
    return () => {
      active = false
      window.removeEventListener(CHANGE_EVENT, onChange)
      window.removeEventListener('storage', onStorage)
    }
  }, [])

  return language
}
