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
