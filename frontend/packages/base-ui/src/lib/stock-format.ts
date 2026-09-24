/**
 * 涨跌语义与数字格式化的全站唯一实现（此前在各页重复定义 3+ 份）。
 * 口径：A股 红涨绿跌（stock-up / stock-down token，随主题亮暗切换）。
 */

/** 涨跌文字色类：null → 中性灰。 */
export function moveColor(v?: number | null, flatCls = 'text-muted-foreground'): string {
  if (v == null) return 'text-muted-foreground'
  return v > 0 ? 'text-stock-up' : v < 0 ? 'text-stock-down' : flatCls
}

/** 涨跌 chip 的底色+文字类：null/平盘 → 灰底。 */
export function pctChipCls(v?: number | null): string {
  if (v == null) return 'bg-accent text-muted-foreground'
  if (v > 0) return 'bg-stock-up/10 text-stock-up'
  if (v < 0) return 'bg-stock-down/10 text-stock-down'
  return 'bg-accent text-muted-foreground'
}

/** 百分比文本：+2.34% / -1.20% / --。 */
export function fmtPct(v?: number | null, digits = 2): string {
  if (v == null || !isFinite(v)) return '--'
  return `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`
}

/** 金额文本：+¥2,175 / -¥300 / --。 */
export function fmtMoney(v?: number | null): string {
  if (v == null || !isFinite(v)) return '--'
  const sign = v > 0 ? '+' : v < 0 ? '-' : ''
  return `${sign}¥${Math.abs(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`
}

/** 千分位数字：1,234.56 / --。 */
export function fmtNum(v?: number | null, digits = 2): string {
  if (v == null || !isFinite(v)) return '--'
  return v.toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: 0 })
}
