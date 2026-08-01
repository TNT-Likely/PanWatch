import { useId } from 'react'

interface SparklineProps {
  /** 数值序列(如近20日收盘价/净值),自动 min-max 归一到画布高度 */
  data: number[]
  width?: number
  height?: number
  /** 线条颜色,支持 currentColor 或任意 CSS 颜色(含 hsl(var(--xx))),亮暗主题都可读 */
  stroke?: string
  /** 传入则渲染渐变面积(顶部半透明 → 底部透明);不传则只画线 */
  fill?: string
  className?: string
}

/**
 * 极简走势线,无第三方图表库依赖:SVG polyline + 可选渐变面积 + 尾端点圆。
 * 用 viewBox 精确匹配 width/height 并配合 preserveAspectRatio="none" + width="100%"
 * 让父容器控制实际渲染宽度;线宽用 vector-effect="non-scaling-stroke" 避免横向拉伸变形。
 */
export default function Sparkline({
  data,
  width = 100,
  height = 28,
  stroke = 'currentColor',
  fill,
  className,
}: SparklineProps) {
  const gradId = useId()
  const vals = (data || []).filter((v) => typeof v === 'number' && Number.isFinite(v))
  if (vals.length < 2) return null

  let min = Math.min(...vals)
  let max = Math.max(...vals)
  if (max - min < 1e-9) {
    const pad = Math.abs(max) * 0.02 || 1
    max += pad
    min -= pad
  }

  const n = vals.length
  const padY = Math.max(1.5, height * 0.12)
  const innerH = height - padY * 2
  const xAt = (i: number) => (width * i) / (n - 1)
  const yAt = (v: number) => padY + innerH - (innerH * (v - min)) / (max - min)
  const points = vals.map((v, i) => [xAt(i), yAt(v)] as const)
  const pointsAttr = points.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(' ')
  const [lastX, lastY] = points[n - 1]
  const fillColor = fill || stroke
  const gradientId = `spark-fill-${gradId.replace(/[^a-zA-Z0-9_-]/g, '')}`

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      width="100%"
      height={height}
      className={className}
      role="img"
      aria-hidden="true"
    >
      {fill && (
        <>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={fillColor} stopOpacity={0.32} />
              <stop offset="100%" stopColor={fillColor} stopOpacity={0} />
            </linearGradient>
          </defs>
          <polygon
            points={`${xAt(0).toFixed(2)},${height} ${pointsAttr} ${xAt(n - 1).toFixed(2)},${height}`}
            fill={`url(#${gradientId})`}
            stroke="none"
          />
        </>
      )}
      <polyline
        points={pointsAttr}
        fill="none"
        stroke={stroke}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      <circle cx={lastX} cy={lastY} r={2.2} fill={stroke} />
    </svg>
  )
}
