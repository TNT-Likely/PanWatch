/**
 * lightweight-charts 专用颜色归一化。
 *
 * 根因：lightweight-charts 4.x 内部会解析部分颜色（如 attribution logo 的主题
 * 灰度计算读 layout.textColor），其解析器只认 legacy 颜色语法；而主题 CSS 变量
 * 是 Tailwind v4 现代空格分隔 HSL（如 `--foreground: 28 18% 12%`），拼出的
 * `hsl(28 18% 12% / 0.85)` 浏览器 canvas 能解析，但传给 lightweight-charts 会抛
 * "Cannot parse color" 并崩掉整个路由页（RouteErrorBoundary 兜底）。
 *
 * 因此凡是传给 lightweight-charts 的颜色，一律先经 toLwColor 归一化为 rgba。
 * 当前覆盖 hsl() 的现代空格语法与 legacy 逗号语法（含 alpha）；其他格式原样
 * 返回并告警——若未来主题变量引入新颜色格式（如 oklch），需在此扩展。
 */
const HSL_RE =
  /^hsl\(\s*(-?[\d.]+)(?:\s*,\s*|\s+)(-?[\d.]+)%(?:\s*,\s*|\s+)(-?[\d.]+)%(?:\s*[/,]\s*([\d.]+%?))?\s*\)\s*$/i

const warned = new Set<string>()

export function toLwColor(color: string): string {
  const m = String(color || '')
    .trim()
    .match(HSL_RE)
  if (!m) {
    if (color && !warned.has(color)) {
      warned.add(color)
      console.warn(`[chart-color] 未识别的颜色格式，原样传给 lightweight-charts: ${color}`)
    }
    return color
  }
  // 色相归一化到 [0,360)：CSS 允许负值/超一圈，且 JS 负数取模保持符号会算错
  const h = ((Number(m[1]) % 360) + 360) % 360
  const s = Math.min(Math.max(Number(m[2]), 0), 100) / 100
  const l = Math.min(Math.max(Number(m[3]), 0), 100) / 100
  const rawA = m[4]
  const a = rawA ? (rawA.endsWith('%') ? Number(rawA.slice(0, -1)) / 100 : Number(rawA)) : 1

  // CSS Color 4 hsl→rgb 标准换算
  const k = (n: number) => (n + h / 30) % 12
  const chroma = s * Math.min(l, 1 - l)
  const f = (n: number) => l - chroma * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)))
  const ch = (n: number) => Math.round(255 * f(n))
  return `rgba(${ch(0)}, ${ch(8)}, ${ch(4)}, ${Math.min(Math.max(a, 0), 1)})`
}
