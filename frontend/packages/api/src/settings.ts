import { fetchAPI } from './client'

export type AppSetting = {
  key: string
  value: string
  description: string
}

let cache: AppSetting[] | null = null
let inflight: Promise<AppSetting[]> | null = null

export const APP_SETTINGS_CHANGED_EVENT = 'panwatch-settings-changed'

export function invalidateAppSettingsCache(): void {
  cache = null
  inflight = null
}

export function notifyAppSettingsChanged(key?: string): void {
  invalidateAppSettingsCache()
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(APP_SETTINGS_CHANGED_EVENT, { detail: { key } }))
  }
}

export async function fetchAppSettings(): Promise<AppSetting[]> {
  if (cache) return cache
  if (inflight) return inflight
  inflight = fetchAPI<AppSetting[]>('/settings')
    .then((data) => {
      cache = data
      return data
    })
    .finally(() => {
      inflight = null
    })
  return inflight
}

export function parseBoolSetting(value: string | undefined | null, defaultValue = false): boolean {
  if (value == null || value === '') return defaultValue
  return String(value).trim().toLowerCase() === 'true'
}

export async function getAppSettingValue(key: string): Promise<string | undefined> {
  const settings = await fetchAppSettings()
  return settings.find((s) => s.key === key)?.value
}

export async function isKlineExternalLinkEnabled(): Promise<boolean> {
  const value = await getAppSettingValue('kline_external_link_enabled')
  return parseBoolSetting(value, false)
}
