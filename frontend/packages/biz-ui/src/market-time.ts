import type { Time } from 'lightweight-charts'

/** A股/港股行情时间统一按上海时区展示 */
export const MARKET_TIMEZONE = 'Asia/Shanghai'

/** 将行情时间字符串解析为 Unix 秒（按上海时区墙钟时间） */
export function parseMarketDateTime(timeStr: string): number | null {
  const raw = String(timeStr || '').trim()
  const daily = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (daily) {
    const dt = new Date(`${daily[1]}-${daily[2]}-${daily[3]}T00:00:00+08:00`)
    return Number.isNaN(dt.getTime()) ? null : Math.floor(dt.getTime() / 1000)
  }
  const m = raw.match(/^(\d{4})-?(\d{2})-?(\d{2})[ T](\d{2}):(\d{2})/)
  if (!m) return null
  const dt = new Date(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:00+08:00`)
  if (Number.isNaN(dt.getTime())) return null
  return Math.floor(dt.getTime() / 1000)
}

export function parseMarketChartTime(timeStr: string): Time | null {
  const sec = parseMarketDateTime(timeStr)
  return sec == null ? null : (sec as Time)
}

/** lightweight-charts 时间轴标签（固定上海时区） */
export function formatChartTimeLabel(time: Time): string {
  if (typeof time !== 'number' || !Number.isFinite(time)) return ''
  return new Date(time * 1000).toLocaleString('zh-CN', {
    timeZone: MARKET_TIMEZONE,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

export const chartMarketLocalization = {
  timeFormatter: formatChartTimeLabel,
  dateFormat: 'MM-dd',
} as const
