import { moveColor } from '../../lib/stock-format'

interface PnlTextProps {
  value?: number | null
  /** 数值→文本；默认 fmtPct（+2.34%）。传自定义 format 时注意是否已含正负号。 */
  format?: (v: number) => string
  /** 显示 ▲▼ 方向符号（色盲友好辅助），默认开。 */
  showSign?: boolean
  /** null/无效值时的占位文本。 */
  flatText?: string
  className?: string
}

const DEFAULT_FMT = (v: number) => `${v > 0 ? '+' : ''}${v.toFixed(2)}%`

/**
 * 盈亏/涨跌数字：语义色 + 方向符号 + 等宽数字。
 * null/NaN 渲染占位符，不渲染为 0（与旧「null 显示 --」口径一致）。
 */
export function PnlText({ value, format = DEFAULT_FMT, showSign = true, flatText = '--', className }: PnlTextProps) {
  if (value == null || !isFinite(value)) {
    return <span className={`tabular-nums text-muted-foreground ${className ?? ''}`}>{flatText}</span>
  }
  const sign = showSign && value !== 0 ? (value > 0 ? '▲' : '▼') : ''
  return (
    <span className={`tabular-nums ${moveColor(value)} ${className ?? ''}`}>
      {sign && (
        <span className="mr-0.5 text-[0.8em]" aria-hidden="true">
          {sign}
        </span>
      )}
      <span>{format(value)}</span>
    </span>
  )
}

/** 供序列化场景（分享卡等）取方向符号。 */
export function pnlSign(v?: number | null): string {
  if (v == null || v === 0) return ''
  return v > 0 ? '▲' : '▼'
}
