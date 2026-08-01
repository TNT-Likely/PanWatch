import { useLayoutEffect, useRef, useState } from 'react'
import type { BenchmarkCurvePoint } from '@panwatch/api'

interface BenchChartProps {
  curve: BenchmarkCurvePoint[]
  height?: number
  className?: string
}

// 3 条内部参考线(均匀分布,非顶/底边框线)
const GRID_FRACS = [0.2, 0.5, 0.8]

function BenchChartSvg({
  points,
  width,
  height,
}: {
  points: BenchmarkCurvePoint[]
  width: number
  height: number
}) {
  const padLeft = 2
  const padRight = 40 // 预留右侧 % 刻度文字
  const padTop = 12
  const padBottom = 12
  const innerW = Math.max(10, width - padLeft - padRight)
  const innerH = Math.max(10, height - padTop - padBottom)

  const allVals: number[] = []
  for (const p of points) {
    allVals.push(p.portfolio, p.benchmark)
  }
  let min = Math.min(...allVals)
  let max = Math.max(...allVals)
  if (max - min < 1e-6) {
    max += 1
    min -= 1
  }

  const n = points.length
  const xAt = (i: number) => padLeft + (innerW * i) / (n - 1)
  const yAt = (v: number) => padTop + innerH - (innerH * (v - min)) / (max - min)

  const portfolioPts = points.map((p, i) => [xAt(i), yAt(p.portfolio)] as const)
  const benchmarkPts = points.map((p, i) => [xAt(i), yAt(p.benchmark)] as const)
  const portfolioAttr = portfolioPts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const benchmarkAttr = benchmarkPts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const [x0, y0] = portfolioPts[0]
  const [xN, yN] = portfolioPts[n - 1]
  const baseline = padTop + innerH
  const areaAttr = `${xAt(0).toFixed(1)},${baseline.toFixed(1)} ${portfolioAttr} ${xAt(n - 1).toFixed(1)},${baseline.toFixed(1)}`

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="组合净值 vs 基准走势图">
      <defs>
        <linearGradient id="benchchart-area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity={0.22} />
          <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0} />
        </linearGradient>
      </defs>

      {/* 网格线 + 右侧 % 刻度(以曲线起点=100 为基准的累计收益率) */}
      {GRID_FRACS.map((frac) => {
        const y = padTop + innerH * frac
        const v = min + (max - min) * (1 - frac)
        const pctVal = v - 100
        const label = `${pctVal >= 0 ? '+' : ''}${pctVal.toFixed(1)}%`
        return (
          <g key={frac}>
            <line
              x1={padLeft}
              x2={padLeft + innerW}
              y1={y}
              y2={y}
              stroke="hsl(var(--border))"
              strokeWidth={1}
              strokeDasharray="4 4"
            />
            <text
              x={padLeft + innerW + 6}
              y={y}
              dominantBaseline="middle"
              fontSize={10}
              fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
              fill="hsl(var(--muted-foreground))"
            >
              {label}
            </text>
          </g>
        )
      })}

      {/* 基准:虚线(中性色) */}
      <polyline
        points={benchmarkAttr}
        fill="none"
        stroke="hsl(var(--muted-foreground))"
        strokeWidth={1.5}
        strokeDasharray="5 4"
        strokeLinejoin="round"
        strokeLinecap="round"
      />

      {/* 组合:实线 + 浅面积(主角) */}
      <polygon points={areaAttr} fill="url(#benchchart-area)" stroke="none" />
      <polyline
        points={portfolioAttr}
        fill="none"
        stroke="hsl(var(--primary))"
        strokeWidth={2}
        strokeLinejoin="round"
        strokeLinecap="round"
      />

      {/* 两端点圆(组合线起止) */}
      <circle cx={x0} cy={y0} r={4} fill="hsl(var(--card))" />
      <circle cx={x0} cy={y0} r={2.5} fill="hsl(var(--primary))" />
      <circle cx={xN} cy={yN} r={4} fill="hsl(var(--card))" />
      <circle cx={xN} cy={yN} r={2.5} fill="hsl(var(--primary))" />
    </svg>
  )
}

/**
 * 组合净值 vs 基准 双线图,无第三方图表库依赖。
 * 组合(primary)实线+浅面积、基准虚线(中性色)、3条虚网格线 + 右侧 % 刻度、组合线两端点圆。
 * 用容器 clientWidth(ResizeObserver 实测)而非 CSS 缩放渲染 SVG,保证轴文字/线宽不随宽度变化而变形。
 * curve 为空/有效点数 < 2 时不渲染(由上层负责展示"计算中"等占位文案)。
 */
export default function BenchChart({ curve, height = 150, className }: BenchChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)

  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const update = () => setWidth(el.clientWidth)
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const points = (curve || []).filter(
    (p) => Number.isFinite(p.portfolio) && Number.isFinite(p.benchmark),
  )
  if (points.length < 2) return null

  return (
    <div ref={containerRef} className={className} style={{ width: '100%', height }}>
      {width > 0 && <BenchChartSvg points={points} width={width} height={height} />}
    </div>
  )
}
