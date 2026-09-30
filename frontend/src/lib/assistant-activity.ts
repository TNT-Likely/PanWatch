export const ASSISTANT_ACTIVITY_CHANGED = 'panwatch:assistant-activity-changed'
const NOTIFIED_KEY = 'panwatch:assistant-notified'
let storageFallback: number[] = []

export function signalAssistantActivityChange() {
  window.dispatchEvent(new Event(ASSISTANT_ACTIVITY_CHANGED))
}

/** Browser-wide toast deduplication is separate from durable inbox read state. */
export function claimAssistantNotifications(ids: number[]): number[] {
  let previous: number[] = []
  let storageReadable = true
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(NOTIFIED_KEY) || '[]')
    if (Array.isArray(stored)) previous = stored.filter((id): id is number => Number.isSafeInteger(id))
  } catch { storageReadable = false }
  previous = [...new Set([...storageFallback, ...previous])]
  const unseen = ids.filter((id) => !previous.includes(id))
  const next = [...previous, ...unseen].slice(-200)
  try {
    localStorage.setItem(NOTIFIED_KEY, JSON.stringify(next))
    storageFallback = storageReadable ? [] : next
  } catch { storageFallback = next }
  return unseen
}
