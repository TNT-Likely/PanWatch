/** Instants use ISO offsets; date-only market values must stay date-only. */
export function browserTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
}

/** Convert an instant before populating a browser-local wall-clock input. */
export function toLocalDateTimeInput(instant: string): string {
  const date = new Date(instant)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (value: number, length = 2) => String(value).padStart(length, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${pad(date.getMilliseconds(), 3)}`
}

export function expiryToISO(wallClock: string, originalInstant?: string): string | null {
  if (!wallClock) return null
  // Preserve the exact instant during a repeated DST hour when nothing changed.
  if (originalInstant && wallClock === toLocalDateTimeInput(originalInstant)) {
    return new Date(originalInstant).toISOString()
  }
  const date = new Date(wallClock)
  if (Number.isNaN(date.getTime()) || toLocalDateTimeInput(date.toISOString()).slice(0, 16) !== wallClock.slice(0, 16)) {
    throw new RangeError('Invalid local expiry time')
  }
  return date.toISOString()
}
