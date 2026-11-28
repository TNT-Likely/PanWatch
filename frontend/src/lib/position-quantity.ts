/**
 * 持仓数量输入/展示规则：允许小于 1，最多 8 位小数。
 *
 * 与后端 `src/modules/portfolio/quantity.py` 保持同一口径：
 * 数量必须为正数，小数位数不超过 8 位。
 */
export const MAX_QUANTITY_DECIMALS = 8

/** 数量的小数位数（指数写法按实际值计算，例如 1e-8 记 8 位）。 */
export function quantityDecimals(value: number): number {
  if (!Number.isFinite(value)) return MAX_QUANTITY_DECIMALS + 1
  const text = String(value)
  const exponentIndex = text.indexOf('e') >= 0 ? text.indexOf('e') : text.indexOf('E')
  if (exponentIndex >= 0) {
    const mantissa = text.slice(0, exponentIndex)
    const exponent = Number(text.slice(exponentIndex + 1))
    const mantissaDecimals = mantissa.split('.')[1]?.length ?? 0
    return Math.max(0, mantissaDecimals - exponent)
  }
  return text.split('.')[1]?.length ?? 0
}

/**
 * 解析数量输入。
 * 返回 null 表示输入非法（空、非数字、非正数或超过 8 位小数）。
 */
export function parseQuantity(raw: string): number | null {
  const text = raw.trim()
  if (!text) return null
  const value = Number(text)
  if (!Number.isFinite(value) || value <= 0) return null
  if (quantityDecimals(value) > MAX_QUANTITY_DECIMALS) return null
  return value
}

/** 数量转成可读文本：整数不带小数，小数去掉多余的 0。 */
export function formatQuantity(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  if (Number.isInteger(value)) return String(value)
  const text = value.toFixed(MAX_QUANTITY_DECIMALS).replace(/0+$/, '').replace(/\.$/, '')
  return text || '0'
}
