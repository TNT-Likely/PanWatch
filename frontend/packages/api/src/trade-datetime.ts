/** 将 datetime-local 值转为 ISO 字符串；空值表示使用当前时间。 */
export function tradeDatetimeLocalToIso(value: string): string | undefined {
  const v = value.trim()
  if (!v) return undefined
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return undefined
  return d.toISOString()
}

/** datetime-local 默认值：当前本地时间。 */
export function nowDatetimeLocalValue(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 将 ISO 时间转为 datetime-local 输入值(本地时区)。 */
export function tradeDatetimeIsoToLocal(value: string | null | undefined): string {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
