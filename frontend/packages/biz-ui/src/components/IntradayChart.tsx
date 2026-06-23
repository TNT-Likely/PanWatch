import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import {
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  type Time,
} from 'lightweight-charts'
import { insightApi } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'

type TrendPoint = {
  time: string
  price: number
  avg_price: number | null
  volume: number
  turnover: number
}

type TrendsResponse = {
  symbol: string
  market: string
  trade_date: string
  pre_close: number | null
  updated_at: string
  points: TrendPoint[]
}

type HoverRow = {
  time: string
  price: number
  avg_price: number | null
  volume: number
  changePct: number | null
}

const AUTO_REFRESH_MS = 20_000

function parseTrendTime(timeStr: string): Time | null {
  const raw = String(timeStr || '').trim()
  const m = raw.match(/^(\d{4})-?(\d{2})-?(\d{2})[ T](\d{2}):(\d{2})/)
  if (!m) return null
  const dt = new Date(
    Number(m[1]),
    Number(m[2]) - 1,
    Number(m[3]),
    Number(m[4]),
    Number(m[5]),
    0,
  )
  if (Number.isNaN(dt.getTime())) return null
  return Math.floor(dt.getTime() / 1000) as Time
}

export default function IntradayChart(props: {
  symbol: string
  market: string
  autoRefresh?: boolean
}) {
  const autoRefresh = props.autoRefresh !== false
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [data, setData] = useState<TrendsResponse | null>(null)
  const [hover, setHover] = useState<HoverRow | null>(null)

  const containerRef = useRef<HTMLDivElement | null>(null)
  const volRef = useRef<HTMLDivElement | null>(null)

  const load = useCallback(async (opts?: { silent?: boolean }) => {
    if (!props.symbol) return
    const silent = !!opts?.silent
    if (!silent) setLoading(true)
    setError('')
    try {
      const res = await insightApi.intradayTrends<TrendsResponse>(props.symbol, props.market)
      setData(res)
    } catch (e) {
      if (!silent) {
        setError(e instanceof Error ? e.message : '加载分时失败')
      }
      if (!silent) setData(null)
    } finally {
      if (!silent) setLoading(false)
    }
  }, [props.symbol, props.market])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    if (!autoRefresh) return
    const timer = setInterval(() => {
      void load({ silent: true })
    }, AUTO_REFRESH_MS)
    return () => clearInterval(timer)
  }, [autoRefresh, load])

  const series = useMemo(() => {
    const points = (data?.points || []).filter(p => parseTrendTime(p.time) != null)
    const preClose = data?.pre_close ?? null
    const prices = points.map(p => ({
      time: parseTrendTime(p.time) as Time,
      value: p.price,
    }))
    const avgs = points
      .map(p => {
        const t = parseTrendTime(p.time)
        const v = p.avg_price
        return t != null && v != null ? { time: t, value: v } : null
      })
      .filter(Boolean) as Array<{ time: Time; value: number }>
    const volumes = points.map(p => {
      const up = preClose == null ? true : p.price >= preClose
      return {
        time: parseTrendTime(p.time) as Time,
        value: p.volume,
        color: up ? 'rgba(239, 68, 68, 0.35)' : 'rgba(16, 185, 129, 0.35)',
      }
    })
    const last = points.length ? points[points.length - 1] : null
    const changePct =
      last && preClose && preClose > 0 ? ((last.price - preClose) / preClose) * 100 : null
    return { points, prices, avgs, volumes, preClose, last, changePct }
  }, [data])

  const showSkeleton = loading && !series.points.length

  useEffect(() => {
    if (!containerRef.current || !series.prices.length) return

    const container = containerRef.current
    const volEl = volRef.current
    container.innerHTML = ''
    if (volEl) volEl.innerHTML = ''

    const rootStyle = getComputedStyle(document.documentElement)
    const bg = rootStyle.getPropertyValue('--card').trim()
    const fg = rootStyle.getPropertyValue('--foreground').trim()
    const up = series.last && series.preClose != null && series.last.price >= series.preClose

    const chart = createChart(container, {
      width: container.clientWidth,
      height: 300,
      layout: {
        background: { color: `hsl(${bg})` },
        textColor: `hsl(${fg} / 0.85)`,
      },
      rightPriceScale: { borderVisible: false },
      timeScale: {
        borderVisible: false,
        fixRightEdge: true,
        rightOffset: 2,
        barSpacing: 3,
        minBarSpacing: 1,
        timeVisible: true,
        secondsVisible: false,
      },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      grid: {
        vertLines: { color: 'rgba(148, 163, 184, 0.08)' },
        horzLines: { color: 'rgba(148, 163, 184, 0.08)' },
      },
      crosshair: { mode: CrosshairMode.Magnet },
    })

    const priceSeries = chart.addSeries(LineSeries, {
      color: up ? '#ef4444' : '#10b981',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    })
    priceSeries.setData(series.prices)

    if (series.preClose != null) {
      priceSeries.createPriceLine({
        price: series.preClose,
        color: 'rgba(148, 163, 184, 0.75)',
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: '昨收',
      })
    }

    if (series.avgs.length) {
      const avgSeries = chart.addSeries(LineSeries, {
        color: 'rgba(245, 158, 11, 0.9)',
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      })
      avgSeries.setData(series.avgs)
    }

    if (volEl) {
      const volChart = createChart(volEl, {
        width: volEl.clientWidth,
        height: 80,
        layout: {
          background: { color: `hsl(${bg})` },
          textColor: `hsl(${fg} / 0.75)`,
        },
        rightPriceScale: { borderVisible: false },
        timeScale: { borderVisible: false, visible: false },
        grid: {
          vertLines: { color: 'rgba(148, 163, 184, 0.06)' },
          horzLines: { color: 'rgba(148, 163, 184, 0.06)' },
        },
        crosshair: { mode: CrosshairMode.Magnet },
      })
      const volSeries = volChart.addSeries(HistogramSeries, {
        priceFormat: { type: 'volume' },
      })
      volSeries.setData(series.volumes)
      chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (range) volChart.timeScale().setVisibleLogicalRange(range)
      })
      volChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (range) chart.timeScale().setVisibleLogicalRange(range)
      })
      const ro = new ResizeObserver(() => {
        volChart.applyOptions({ width: volEl.clientWidth })
      })
      ro.observe(volEl)
      chart.timeScale().fitContent()
      return () => {
        ro.disconnect()
        volChart.remove()
        chart.remove()
      }
    }

    chart.subscribeCrosshairMove(param => {
      if (!param.time || !param.point) {
        setHover(null)
        return
      }
      const t = typeof param.time === 'number' ? param.time : null
      if (t == null) {
        setHover(null)
        return
      }
      const idx = series.prices.findIndex(p => p.time === t)
      if (idx < 0) {
        setHover(null)
        return
      }
      const row = series.points[idx]
      const cp =
        series.preClose && series.preClose > 0
          ? ((row.price - series.preClose) / series.preClose) * 100
          : null
      setHover({
        time: row.time,
        price: row.price,
        avg_price: row.avg_price,
        volume: row.volume,
        changePct: cp,
      })
    })

    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: container.clientWidth })
    })
    ro.observe(container)
    chart.timeScale().fitContent()

    return () => {
      ro.disconnect()
      chart.remove()
    }
  }, [series])

  const display = hover || (series.last
    ? {
        time: series.last.time,
        price: series.last.price,
        avg_price: series.last.avg_price,
        volume: series.last.volume,
        changePct: series.changePct,
      }
    : null)

  return (
    <div>
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-2 mb-3">
        <div className="text-[13px] font-semibold text-foreground">
          分时
          {data?.trade_date ? (
            <span className="ml-2 text-[11px] font-normal text-muted-foreground">{data.trade_date}</span>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          {autoRefresh ? (
            <span className="text-[11px] text-muted-foreground">每 20 秒自动刷新</span>
          ) : null}
          <Button variant="secondary" size="sm" className="h-8" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            <span className="hidden sm:inline">刷新</span>
          </Button>
        </div>
      </div>

      {error ? (
        <div className="text-[12px] text-rose-600 bg-rose-500/10 border border-rose-500/20 rounded-lg px-3 py-2 mb-3">
          {error}
        </div>
      ) : null}

      {!loading && !error && !series.points.length ? (
        <div className="text-[12px] text-muted-foreground bg-accent/20 border border-border/40 rounded-lg px-3 py-2 mb-3">
          暂无当日分时数据（非交易时段或数据源暂不可用）。请稍后刷新。
        </div>
      ) : null}

      {display ? (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-3">
          <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]">
            <span className="text-muted-foreground">现价</span>
            <span className="font-mono ml-1">{display.price.toFixed(2)}</span>
          </div>
          <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]">
            <span className="text-muted-foreground">涨跌</span>
            <span className={`font-mono ml-1 ${(display.changePct ?? 0) >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
              {display.changePct == null ? '--' : `${display.changePct >= 0 ? '+' : ''}${display.changePct.toFixed(2)}%`}
            </span>
          </div>
          <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]">
            <span className="text-muted-foreground">均价</span>
            <span className="font-mono ml-1">{display.avg_price != null ? display.avg_price.toFixed(2) : '--'}</span>
          </div>
          <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]">
            <span className="text-muted-foreground">昨收</span>
            <span className="font-mono ml-1">{series.preClose != null ? series.preClose.toFixed(2) : '--'}</span>
          </div>
          <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]">
            <span className="text-muted-foreground">成交量</span>
            <span className="font-mono ml-1">{(display.volume / 10000).toFixed(1)}万手</span>
          </div>
        </div>
      ) : showSkeleton ? (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-3 animate-pulse">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="rounded-lg bg-accent/20 px-2.5 py-2">
              <div className="h-3 w-14 bg-accent/60 rounded" />
              <div className="h-3 w-16 bg-accent/60 rounded mt-2" />
            </div>
          ))}
        </div>
      ) : null}

      <div className="relative">
        {showSkeleton ? (
          <div className="w-full h-[300px] rounded-xl overflow-hidden border border-border/50 p-3 animate-pulse">
            <div className="h-full w-full rounded-lg bg-accent/20" />
          </div>
        ) : (
          <div ref={containerRef} className="w-full h-[300px] rounded-xl overflow-hidden border border-border/50" />
        )}
      </div>
      <div ref={volRef} className="w-full h-[80px] mt-1 rounded-xl overflow-hidden border border-border/50" />
    </div>
  )
}
