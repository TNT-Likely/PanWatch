import type { NotificationItem } from '@panwatch/api'
import { localizeAgentName } from '@/i18n/agent-labels'

export function notificationTitle(
  item: Pick<NotificationItem, 'source' | 'title' | 'template_params'>,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  if (item.source !== 'agent') return item.title || t(`notifications.sources.${item.source}`)
  const agent = String(item.template_params.agent_name || item.title)
  const label = localizeAgentName(agent, item.title, t)
  const symbol = item.template_params.stock_symbol
  const name = item.template_params.stock_name
  return symbol ? `${label} · ${name && name !== symbol ? `${name} (${symbol})` : symbol}` : label
}

export const NOTIFICATIONS_CHANGED = 'panwatch:notifications-changed'
const STORAGE_KEY = 'panwatch:notifications-notified:installation:default'
let fallback: number[] = []
/** Presentation deduplication is independent from durable read/approval state. */
export function claimNotifications(ids: number[]): number[] {
  let previous: number[] = []
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    if (Array.isArray(stored)) previous = stored.filter((id): id is number => Number.isSafeInteger(id))
  } catch { /* Storage can be disabled. */ }
  previous = [...new Set([...previous, ...fallback])]
  const unseen = ids.filter(id => !previous.includes(id))
  const next = [...previous, ...unseen].slice(-500)
  fallback = next
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)) } catch { /* Keep in-memory deduplication. */ }
  return unseen
}
