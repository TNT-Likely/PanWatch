import { useEffect, useMemo, useRef, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { Button } from '@panwatch/base-ui/components/ui/button'

type PerformancePoint = {
  ts: number
  value: number
  return_pct: number | null
}

type TimeRange = '1m' | '3m' | '6m' | '1y' | '3y' | 'all'

type HoverTip = {
  visible: boolean
  x: number
  y: number
  date: string
  value: number
  dayChange: number | null
}

function getLW() {
  return (window as any)?.LightweightCharts || null
}

function addAreaSeries(chart: any, LW: any, options: any) {
  if (typeof chart?.addAreaSeries === 'function') return chart.addAreaSeries(options)
  if (typeof chart?.addSeries === 'function' && LW?.AreaSeries) return chart.addSeries(LW.AreaSeries, options)
  throw new Error('Area series API not available')
}

function addLine(chart: any, LW: any, options: any) {
  if (typeof chart?.addLineSeries === 'function') return chart.addLineSeries(options)
  if (typeof chart?.addSeries === 'function' && LW?.LineSeries) return chart.addSeries(LW.LineSeries, options)
  throw new Error('Line series API not available')
}

function formatDate(ts: number): string {
  const d = new Date(ts)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function filterByRange(points: PerformancePoint[], range: TimeRange): PerformancePoint[] {
  if (range === 'all' || !points.length) return points
  const now = Date.now()
  const rangeMs: Record<Exclude<TimeRange, 'all'>, number> = {
    '1m': 30 * 24 * 60 * 60 * 1000,
    '3m': 90 * 24 * 60 * 60 * 1000,
    '6m': 180 * 24 * 60 * 60 * 1000,
    '1y': 365 * 24 * 60 * 60 * 1000,
    '3y': 3 * 365 * 24 * 60 * 60 * 1000,
  }
  const cutoff = now - rangeMs[range]
  return points.filter(p => p.ts >= cutoff)
}

// Calculate period return based on first and last value in filtered range
function calcPeriodReturn(points: PerformancePoint[]): number | null {
  if (points.length < 2) return null
  const first = points[0].value
  const last = points[points.length - 1].value
  if (!first || first === 0) return null
  return ((last / first) - 1) * 100
}

// Calculate MA for return_pct
function sma(values: (number | null)[], period: number): (number | null)[] {
  const out: (number | null)[] = []
  for (let i = 0; i < values.length; i++) {
    if (i < period - 1) {
      out.push(null)
      continue
    }
    let sum = 0
    let count = 0
    for (let j = i - period + 1; j <= i; j++) {
      const v = values[j]
      if (v != null) {
        sum += v
        count++
      }
    }
    out.push(count > 0 ? sum / count : null)
  }
  return out
}

export default function InteractiveFundChart(props: {
  points: PerformancePoint[]
  loading?: boolean
  onRefresh?: () => void
  initialRange?: TimeRange
}) {
  const [lwReady, setLwReady] = useState(!!getLW())
  const [libError, setLibError] = useState(false)
  const [range, setRange] = useState<TimeRange>(props.initialRange || 'all')
  const [hoverTip, setHoverTip] = useState<HoverTip>({ visible: false, x: 0, y: 0, date: '', value: 0, dayChange: null })
  const [showMa, setShowMa] = useState(true)

  const containerRef = useRef<HTMLDivElement | null>(null)

  // Filter points by selected range
  const filteredPoints = useMemo(() => filterByRange(props.points || [], range), [props.points, range])

  // Normalize return_pct to start from 0 for the filtered range
  const normalizedPoints = useMemo(() => {
    if (!filteredPoints.length) return []
    const baseReturnPct = filteredPoints[0].return_pct ?? 0
    return filteredPoints.map(p => ({
      ...p,
      normalized_pct: (p.return_pct ?? 0) - baseReturnPct
    }))
  }, [filteredPoints])

  const periodReturn = useMemo(() => calcPeriodReturn(filteredPoints), [filteredPoints])
  const isPositive = (periodReturn ?? 0) >= 0

  // MA lines
  const ma5 = useMemo(() => sma(normalizedPoints.map(p => p.normalized_pct), 5), [normalizedPoints])
  const ma20 = useMemo(() => sma(normalizedPoints.map(p => p.normalized_pct), 20), [normalizedPoints])

  // Index by timestamp for crosshair lookup
  const indexByTs = useMemo(() => {
    const m = new Map<number, number>()
    normalizedPoints.forEach((p, i) => m.set(p.ts, i))
    return m
  }, [normalizedPoints])

  useEffect(() => {
    if (lwReady) return
    let cancelled = false
    const start = Date.now()
    const t = window.setInterval(() => {
      if (cancelled) return
      if (getLW()) {
        setLwReady(true)
        clearInterval(t)
        return
      }
      if (Date.now() - start > 3500) {
        setLibError(true)
        clearInterval(t)
      }
    }, 200)
    return () => {
      cancelled = true
      clearInterval(t)
    }
  }, [lwReady])

  useEffect(() => {
    const LW = getLW()
    if (!LW || !lwReady) return
    if (!containerRef.current) return
    if (!normalizedPoints.length) return

    const container = containerRef.current
    container.innerHTML = ''

    const rootStyle = getComputedStyle(document.documentElement)
    const bg = rootStyle.getPropertyValue('--card').trim()
    const fg = rootStyle.getPropertyValue('--foreground').trim()

    const chart = LW.createChart(container, {
      width: container.clientWidth,
      height: 340,
      layout: {
        background: { color: `hsl(${bg})` },
        textColor: `hsl(${fg} / 0.85)`,
      },
      rightPriceScale: {
        borderVisible: false,
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderVisible: false,
        fixRightEdge: true,
        rightOffset: 2,
        barSpacing: 6,
        minBarSpacing: 1,
        lockVisibleTimeRangeOnResize: true,
        timeVisible: false,
      },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      grid: {
        vertLines: { color: 'rgba(148, 163, 184, 0.08)' },
        horzLines: { color: 'rgba(148, 163, 184, 0.08)' },
      },
      crosshair: { mode: 1 },
      localization: {
        priceFormatter: (price: number) => `${price >= 0 ? '+' : ''}${price.toFixed(2)}%`,
      },
    })

    // Main area series for performance
    const lineColor = isPositive ? '#fb7185' : '#34d399'
    const areaTopColor = isPositive ? 'rgba(251, 113, 133, 0.3)' : 'rgba(52, 211, 153, 0.3)'
    const areaBottomColor = isPositive ? 'rgba(251, 113, 133, 0.02)' : 'rgba(52, 211, 153, 0.02)'

    const areaSeries = addAreaSeries(chart, LW, {
      lineColor,
      topColor: areaTopColor,
      bottomColor: areaBottomColor,
      lineWidth: 2,
      priceFormat: {
        type: 'custom',
        formatter: (price: number) => `${price >= 0 ? '+' : ''}${price.toFixed(2)}%`,
      },
    })

    // Convert to LightweightCharts time format (UTCTimestamp in seconds)
    const chartData = normalizedPoints.map(p => ({
      time: Math.floor(p.ts / 1000) as any,
      value: p.normalized_pct,
    }))
    areaSeries.setData(chartData)

    // MA lines
    let ma5Series: any = null
    let ma20Series: any = null
    if (showMa) {
      ma5Series = addLine(chart, LW, { color: 'rgba(99, 102, 241, 0.7)', lineWidth: 1 })
      ma20Series = addLine(chart, LW, { color: 'rgba(245, 158, 11, 0.7)', lineWidth: 1 })

      const ma5Data = normalizedPoints
        .map((p, i) => {
          const v = ma5[i]
          return v == null ? null : { time: Math.floor(p.ts / 1000) as any, value: v }
        })
        .filter(Boolean)
      const ma20Data = normalizedPoints
        .map((p, i) => {
          const v = ma20[i]
          return v == null ? null : { time: Math.floor(p.ts / 1000) as any, value: v }
        })
        .filter(Boolean)

      ma5Series.setData(ma5Data as any)
      ma20Series.setData(ma20Data as any)
    }

    // Zero line
    areaSeries.createPriceLine?.({
      price: 0,
      color: 'rgba(148, 163, 184, 0.4)',
      lineWidth: 1,
      lineStyle: 2,
      axisLabelVisible: false,
    })

    // Crosshair move handler
    chart.subscribeCrosshairMove?.((param: any) => {
      const point = param?.point
      const time = param?.time
      if (!point || time == null) {
        setHoverTip(prev => (prev.visible ? { ...prev, visible: false } : prev))
        return
      }
      const inBounds =
        point.x >= 0 &&
        point.y >= 0 &&
        point.x <= container.clientWidth &&
        point.y <= container.clientHeight
      if (!inBounds) {
        setHoverTip(prev => (prev.visible ? { ...prev, visible: false } : prev))
        return
      }

      // time is UTCTimestamp in seconds
      const tsMs = (typeof time === 'number' ? time : 0) * 1000
      const idx = indexByTs.get(tsMs)
      if (idx == null || idx < 0 || idx >= normalizedPoints.length) {
        setHoverTip(prev => (prev.visible ? { ...prev, visible: false } : prev))
        return
      }

      const p = normalizedPoints[idx]
      // Calculate day change (vs previous day)
      let dayChange: number | null = null
      if (idx > 0) {
        const prevValue = normalizedPoints[idx - 1].value
        if (prevValue && prevValue !== 0) {
          dayChange = ((p.value - prevValue) / prevValue) * 100
        }
      }

      const tooltipWidth = 180
      const tooltipHeight = 80
      let x = point.x + 12
      let y = point.y + 12
      if (x + tooltipWidth > container.clientWidth - 6) x = point.x - tooltipWidth - 12
      if (y + tooltipHeight > container.clientHeight - 6) y = point.y - tooltipHeight - 12
      x = Math.max(6, Math.min(x, Math.max(6, container.clientWidth - tooltipWidth - 6)))
      y = Math.max(6, Math.min(y, Math.max(6, container.clientHeight - tooltipHeight - 6)))

      setHoverTip({
        visible: true,
        x,
        y,
        date: formatDate(p.ts),
        value: p.value,
        dayChange,
      })
    })

    // Resize observer
    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: container.clientWidth })
    })
    ro.observe(container)

    // Set initial visible range (show recent data)
    const total = chartData.length
    const defaultBars = Math.min(total, 180)
    const from = Math.max(0, total - defaultBars)
    const to = Math.max(total - 1, 0)
    chart.timeScale().setVisibleLogicalRange({ from, to })

    return () => {
      ro.disconnect()
      try {
        chart.remove()
      } catch {
        // ignore
      }
    }
  }, [normalizedPoints, lwReady, isPositive, showMa, ma5, ma20, indexByTs])

  const showSkeleton = props.loading && !normalizedPoints.length

  return (
    <div className="card p-4">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-2 mb-3">
        <div className="flex items-center gap-3">
          <div className="text-sm font-medium">业绩走势</div>
          {periodReturn != null && (
            <span className={`text-sm font-semibold ${isPositive ? 'text-rose-500' : 'text-emerald-500'}`}>
              {isPositive ? '+' : ''}{periodReturn.toFixed(2)}%
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <Button
            variant={showMa ? 'default' : 'secondary'}
            size="sm"
            className="h-7 px-2 text-[11px]"
            onClick={() => setShowMa(v => !v)}
          >
            均线
          </Button>
          <div className="inline-flex rounded-lg border border-border/60 bg-accent/20 p-0.5">
            {(['1m', '3m', '6m', '1y', '3y', 'all'] as const).map(item => (
              <button
                key={item}
                type="button"
                className={`h-6 min-w-[36px] rounded-md px-2 text-[11px] transition-colors ${
                  range === item
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                }`}
                onClick={() => setRange(item)}
              >
                {item === 'all' ? '全部' : item.toUpperCase()}
              </button>
            ))}
          </div>
          {props.onRefresh && (
            <Button
              variant="secondary"
              size="sm"
              className="h-7 px-2"
              onClick={props.onRefresh}
              disabled={props.loading}
            >
              <RefreshCw className={`w-3 h-3 ${props.loading ? 'animate-spin' : ''}`} />
            </Button>
          )}
        </div>
      </div>

      {!lwReady && libError && (
        <div className="text-[12px] text-amber-700 dark:text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2 mb-3">
          图表库加载失败（网络受限时可能发生）。可稍后重试或检查网络/代理。
        </div>
      )}

      {showSkeleton ? (
        <div className="h-[340px] rounded-lg bg-accent/20 animate-pulse" />
      ) : !normalizedPoints.length ? (
        <div className="h-[340px] rounded-lg bg-card border border-border flex items-center justify-center text-sm text-muted-foreground">
          暂无走势数据
        </div>
      ) : (
        <div className="relative">
          <div ref={containerRef} className="w-full h-[340px] rounded-lg overflow-hidden" />

          {/* Hover tooltip */}
          {hoverTip.visible && (
            <div
              className="pointer-events-none absolute z-20 rounded-lg border border-border bg-popover/95 backdrop-blur-sm px-3 py-2 shadow-lg"
              style={{ left: hoverTip.x, top: hoverTip.y }}
            >
              <div className="text-[11px] text-muted-foreground mb-1">{hoverTip.date}</div>
              <div className="flex items-center gap-3">
                <div>
                  <div className="text-[10px] text-muted-foreground">累计净值</div>
                  <div className="text-[13px] font-semibold text-foreground">{hoverTip.value.toFixed(4)}</div>
                </div>
                <div>
                  <div className="text-[10px] text-muted-foreground">日涨跌</div>
                  <div className={`text-[13px] font-semibold ${(hoverTip.dayChange ?? 0) >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                    {hoverTip.dayChange != null ? `${hoverTip.dayChange >= 0 ? '+' : ''}${hoverTip.dayChange.toFixed(2)}%` : '--'}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* MA Legend */}
          {showMa && (
            <div className="absolute top-2 left-3 flex items-center gap-3 text-[10px]">
              <span className="flex items-center gap-1">
                <span className="w-3 h-0.5 bg-indigo-500 rounded" />
                <span className="text-muted-foreground">MA5</span>
              </span>
              <span className="flex items-center gap-1">
                <span className="w-3 h-0.5 bg-amber-500 rounded" />
                <span className="text-muted-foreground">MA20</span>
              </span>
            </div>
          )}
        </div>
      )}

      {/* Date range footer */}
      {normalizedPoints.length >= 2 && (
        <div className="mt-2 flex items-center justify-between text-[11px] text-muted-foreground">
          <span>{formatDate(normalizedPoints[0].ts)}</span>
          <span>{formatDate(normalizedPoints[normalizedPoints.length - 1].ts)}</span>
        </div>
      )}
    </div>
  )
}
