import { useEffect, useMemo, useRef, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { fetchAPI } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'

type BusinessDay = { year: number; month: number; day: number }

type KlineItem = {
  date: string
  open: number
  close: number
  high: number
  low: number
  volume: number
}

type KlinesResponse = {
  symbol: string
  market: string
  days: number
  interval?: string
  klines: KlineItem[]
}

type MinutePoint = {
  t: string
  price: number
  avg: number
  volume: number
}

type MinuteResponse = {
  symbol: string
  market: string
  points: MinutePoint[]
}

type HoverTipRow = {
  date: string
  open: number
  high: number
  low: number
  close: number
  ma5: number | null
  ma10: number | null
  ma20: number | null
  macd: number | null
  signal: number | null
  rsi6: number | null
}

type HoverTip = {
  visible: boolean
  x: number
  y: number
  row: HoverTipRow | null
}

function parseBusinessDay(dateStr: string): BusinessDay | null {
  const m = String(dateStr || '').trim().match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!m) return null
  return { year: Number(m[1]), month: Number(m[2]), day: Number(m[3]) }
}

function parseCrosshairDateKey(time: any): string | null {
  if (!time || typeof time !== 'object') return null
  const year = Number(time.year)
  const month = Number(time.month)
  const day = Number(time.day)
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) return null
  return `${year.toString().padStart(4, '0')}-${month.toString().padStart(2, '0')}-${day.toString().padStart(2, '0')}`
}

function sma(values: number[], period: number): Array<number | null> {
  if (period <= 1) return values.map(v => v)
  const out: Array<number | null> = new Array(values.length).fill(null)
  let sum = 0
  for (let i = 0; i < values.length; i++) {
    sum += values[i]
    if (i >= period) sum -= values[i - period]
    if (i >= period - 1) out[i] = sum / period
  }
  return out
}

function ema(values: number[], period: number): Array<number | null> {
  const out: Array<number | null> = new Array(values.length).fill(null)
  if (values.length === 0) return out
  const k = 2 / (period + 1)
  let prev: number | null = null
  for (let i = 0; i < values.length; i++) {
    const v = values[i]
    if (prev == null) {
      prev = v
      out[i] = v
      continue
    }
    prev = v * k + prev * (1 - k)
    out[i] = prev
  }
  return out
}

function computeMacd(closes: number[]) {
  const e12 = ema(closes, 12)
  const e26 = ema(closes, 26)
  const macd: Array<number | null> = closes.map((_, i) => {
    const a = e12[i]
    const b = e26[i]
    if (a == null || b == null) return null
    return a - b
  })
  const macdVals = macd.map(v => (v == null ? 0 : v))
  const signal = ema(macdVals, 9)
  const hist: Array<number | null> = macd.map((v, i) => {
    if (v == null || signal[i] == null) return null
    return v - (signal[i] as number)
  })
  return { macd, signal, hist }
}

function computeRsi(closes: number[], period = 6): Array<number | null> {
  const out: Array<number | null> = new Array(closes.length).fill(null)
  if (closes.length <= period) return out
  let gain = 0
  let loss = 0
  for (let i = 1; i <= period; i++) {
    const diff = closes[i] - closes[i - 1]
    if (diff >= 0) gain += diff
    else loss += -diff
  }
  let avgGain = gain / period
  let avgLoss = loss / period
  out[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss)
  for (let i = period + 1; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1]
    const g = diff > 0 ? diff : 0
    const l = diff < 0 ? -diff : 0
    avgGain = (avgGain * (period - 1) + g) / period
    avgLoss = (avgLoss * (period - 1) + l) / period
    out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss)
  }
  return out
}

function getLW() {
  return (window as any)?.LightweightCharts || null
}

function addCandles(chart: any, LW: any, options: any) {
  if (typeof chart?.addCandlestickSeries === 'function') return chart.addCandlestickSeries(options)
  if (typeof chart?.addSeries === 'function' && LW?.CandlestickSeries) return chart.addSeries(LW.CandlestickSeries, options)
  throw new Error('Candlestick series API not available')
}

function addLine(chart: any, LW: any, options: any) {
  if (typeof chart?.addLineSeries === 'function') return chart.addLineSeries(options)
  if (typeof chart?.addSeries === 'function' && LW?.LineSeries) return chart.addSeries(LW.LineSeries, options)
  throw new Error('Line series API not available')
}

function addHistogram(chart: any, LW: any, options: any) {
  if (typeof chart?.addHistogramSeries === 'function') return chart.addHistogramSeries(options)
  if (typeof chart?.addSeries === 'function' && LW?.HistogramSeries) return chart.addSeries(LW.HistogramSeries, options)
  throw new Error('Histogram series API not available')
}

export default function InteractiveKline(props: {
  symbol: string
  market: string
  initialInterval?: '1d' | '1w' | '1m'
  initialDays?: '60' | '120' | '250'
}) {
  const [lwReady, setLwReady] = useState(!!getLW())
  const [libError, setLibError] = useState(false)
  const [interval, setIntervalValue] = useState<'1d' | '1w' | '1m'>(props.initialInterval || '1d')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>('')
  const [data, setData] = useState<KlineItem[]>([])
  const [showRsi, setShowRsi] = useState(true)
  const [hoverTip, setHoverTip] = useState<HoverTip>({ visible: false, x: 0, y: 0, row: null })
  // 分时模式(内嵌进K线图组件, 与日/周/月同一组切换器)
  const [mode, setMode] = useState<'minute' | 'kline'>('kline')
  const [minutePoints, setMinutePoints] = useState<MinutePoint[]>([])
  const [minuteLoading, setMinuteLoading] = useState(false)
  const [minuteError, setMinuteError] = useState<string>('')

  const loadMinute = async () => {
    if (!props.symbol) return
    setMinuteLoading(true)
    setMinuteError('')
    try {
      const res = await fetchAPI<MinuteResponse>(
        `/quotes/minute?symbol=${encodeURIComponent(props.symbol)}&market=${encodeURIComponent(props.market)}`
      )
      setMinutePoints(res.points || [])
    } catch (e) {
      setMinuteError(e instanceof Error ? e.message : '加载分时失败')
      setMinutePoints([])
    } finally {
      setMinuteLoading(false)
    }
  }

  useEffect(() => {
    if (mode !== 'minute') return
    void loadMinute()
    const timer = window.setInterval(() => void loadMinute(), 30000) // 盘中30秒自动刷新
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, props.symbol, props.market])

  const fixedDays = useMemo(() => {
    const customDays = Number(props.initialDays)
    if (Number.isFinite(customDays) && customDays > 0) {
      return Math.floor(customDays)
    }
    if (interval === '1m') return 360
    if (interval === '1w') return 180
    return 120
  }, [props.initialDays, interval])

  const containerRef = useRef<HTMLDivElement | null>(null)
  const macdRef = useRef<HTMLDivElement | null>(null)

  const load = async () => {
    if (!props.symbol) return
    setLoading(true)
    setError('')
    setHoverTip(prev => (prev.visible ? { visible: false, x: 0, y: 0, row: null } : prev))
    try {
      const query = (days: number) =>
        `/klines/${encodeURIComponent(props.symbol)}?market=${encodeURIComponent(props.market)}&days=${encodeURIComponent(String(days))}&interval=${encodeURIComponent(interval)}`
      const attempts = Array.from(new Set([fixedDays, Math.max(90, Math.floor(fixedDays * 0.75))]))
      let best: KlineItem[] = []
      let lastError: unknown = null
      for (const d of attempts) {
        try {
          const res = await fetchAPI<KlinesResponse>(query(d))
          const kl = res.klines || []
          if (kl.length > best.length) best = kl
          if (d === fixedDays && kl.length > 0) break
        } catch (e) {
          lastError = e
        }
      }
      if (!best.length && lastError) throw lastError
      setData(best)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载K线失败')
      setData([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.symbol, props.market, interval, fixedDays])

  useEffect(() => {
    if (props.initialInterval) setIntervalValue(props.initialInterval)
  }, [props.initialInterval, props.symbol, props.market])

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

  const series = useMemo(() => {
    const klines = (data || []).slice().filter(k => !!parseBusinessDay(k.date))
    const candles = klines.map(k => ({
      time: parseBusinessDay(k.date) as BusinessDay,
      open: k.open,
      high: k.high,
      low: k.low,
      close: k.close,
    }))
    const volumes = klines.map(k => ({
      time: parseBusinessDay(k.date) as BusinessDay,
      value: k.volume,
      color: k.close >= k.open ? 'rgba(239, 68, 68, 0.35)' : 'rgba(16, 185, 129, 0.35)',
    }))
    const closes = klines.map(k => k.close)
    const ma5 = sma(closes, 5)
    const ma10 = sma(closes, 10)
    const ma20 = sma(closes, 20)
    const volRaw = klines.map(k => k.volume)
    const volMa5 = sma(volRaw, 5)
    const volMa10 = sma(volRaw, 10)
    const macd = computeMacd(closes)
    const rsi6 = computeRsi(closes, 6)
    return { klines, candles, volumes, ma5, ma10, ma20, volMa5, volMa10, macd, rsi6 }
  }, [data])

  const latestMetrics = useMemo(() => {
    if (!series.klines.length) return null
    const last = series.klines[series.klines.length - 1]
    const prev = series.klines.length > 1 ? series.klines[series.klines.length - 2] : null
    const maxHigh = Math.max(...series.klines.map(k => k.high))
    const minLow = Math.min(...series.klines.map(k => k.low))
    const avgVol = series.klines.reduce((acc, k) => acc + (k.volume || 0), 0) / series.klines.length
    const changePct = prev && prev.close ? ((last.close - prev.close) / prev.close) * 100 : 0
    const ampPct = last.close ? ((last.high - last.low) / last.close) * 100 : 0
    return { last, changePct, ampPct, maxHigh, minLow, avgVol }
  }, [series.klines])

  const indexByDate = useMemo(() => {
    const m = new Map<string, number>()
    for (let i = 0; i < series.klines.length; i++) {
      m.set(series.klines[i].date, i)
    }
    return m
  }, [series.klines])
  const showSkeleton = loading && !series.klines.length

  useEffect(() => {
    const LW = getLW()
    if (!LW || !lwReady) return
    if (!containerRef.current) return
    if (!series.candles.length) return

    const container = containerRef.current
    const macdEl = macdRef.current

    container.innerHTML = ''
    if (macdEl) macdEl.innerHTML = ''

    const rootStyle = getComputedStyle(document.documentElement)
    const bg = rootStyle.getPropertyValue('--card').trim()
    const fg = rootStyle.getPropertyValue('--foreground').trim()

    const defaultBars = interval === '1d' ? 100 : interval === '1w' ? 78 : 72
    const defaultSpacing = interval === '1d' ? 8.5 : interval === '1w' ? 10 : 10
    const chart = LW.createChart(container, {
      width: container.clientWidth,
      height: 380,
      layout: {
        background: { color: `hsl(${bg})` },
        textColor: `hsl(${fg} / 0.85)`,
      },
      rightPriceScale: { borderVisible: false },
      timeScale: {
        borderVisible: false,
        fixRightEdge: true,
        rightOffset: 1,
        barSpacing: defaultSpacing,
        minBarSpacing: 1,
        lockVisibleTimeRangeOnResize: true,
      },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      grid: {
        vertLines: { color: 'rgba(148, 163, 184, 0.08)' },
        horzLines: { color: 'rgba(148, 163, 184, 0.08)' },
      },
      crosshair: { mode: 1 },
    })

    const candleSeries = addCandles(chart, LW, {
      upColor: '#ef4444',
      downColor: '#10b981',
      borderUpColor: '#ef4444',
      borderDownColor: '#10b981',
      wickUpColor: '#ef4444',
      wickDownColor: '#10b981',
    })
    candleSeries.setData(series.candles)

    const volSeries = addHistogram(chart, LW, {
      priceScaleId: 'vol',
      priceFormat: { type: 'volume' },
    })
    volSeries.setData(series.volumes)
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    const volMa5Series = addLine(chart, LW, { priceScaleId: 'vol', color: 'rgba(245, 158, 11, 0.9)', lineWidth: 1 })
    const volMa10Series = addLine(chart, LW, { priceScaleId: 'vol', color: 'rgba(14, 165, 233, 0.9)', lineWidth: 1 })

    const ma5Series = addLine(chart, LW, { color: 'rgba(99, 102, 241, 0.85)', lineWidth: 2 })
    const ma10Series = addLine(chart, LW, { color: 'rgba(245, 158, 11, 0.85)', lineWidth: 2 })
    const ma20Series = addLine(chart, LW, { color: 'rgba(14, 165, 233, 0.85)', lineWidth: 2 })

    const mapLine = (arr: Array<number | null>) =>
      series.klines
        .map((k, i) => {
          const v = arr[i]
          return v == null ? null : { time: parseBusinessDay(k.date) as BusinessDay, value: v }
        })
        .filter(Boolean)

    ma5Series.setData(mapLine(series.ma5) as any)
    ma10Series.setData(mapLine(series.ma10) as any)
    ma20Series.setData(mapLine(series.ma20) as any)
    volMa5Series.setData(mapLine(series.volMa5) as any)
    volMa10Series.setData(mapLine(series.volMa10) as any)

    // MACD chart
    let macdChart: any = null
    let rsiChart: any = null
    if (macdEl) {
      macdChart = LW.createChart(macdEl, {
        width: macdEl.clientWidth,
        height: 150,
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
        crosshair: { mode: 0 },
      })
      const macdLine = addLine(macdChart, LW, { color: 'rgba(99, 102, 241, 0.85)', lineWidth: 2 })
      const sigLine = addLine(macdChart, LW, { color: 'rgba(14, 165, 233, 0.85)', lineWidth: 2 })
      const hist = addHistogram(macdChart, LW, {
        priceFormat: { type: 'price', precision: 3, minMove: 0.001 },
      })

      const macdLineData = series.klines
        .map((k, i) => {
          const v = series.macd.macd[i]
          return v == null ? null : { time: parseBusinessDay(k.date) as BusinessDay, value: v }
        })
        .filter(Boolean)
      const sigLineData = series.klines
        .map((k, i) => {
          const v = series.macd.signal[i]
          return v == null ? null : { time: parseBusinessDay(k.date) as BusinessDay, value: v }
        })
        .filter(Boolean)
      const histData = series.klines
        .map((k, i) => {
          const v = series.macd.hist[i]
          if (v == null) return null
          return {
            time: parseBusinessDay(k.date) as BusinessDay,
            value: v,
            color: v >= 0 ? 'rgba(239, 68, 68, 0.35)' : 'rgba(16, 185, 129, 0.35)',
          }
        })
        .filter(Boolean)

      macdLine.setData(macdLineData as any)
      sigLine.setData(sigLineData as any)
      hist.setData(histData as any)
    }

    // RSI chart
    if (showRsi && macdEl) {
      const rsiRoot = document.createElement('div')
      rsiRoot.className = 'mt-2'
      macdEl.parentElement?.appendChild(rsiRoot)
      rsiChart = LW.createChart(rsiRoot, {
        width: macdEl.clientWidth,
        height: 110,
        layout: {
          background: { color: `hsl(${bg})` },
          textColor: `hsl(${fg} / 0.75)`,
        },
        rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.15, bottom: 0.1 } },
        timeScale: { borderVisible: false, visible: false },
        grid: {
          vertLines: { color: 'rgba(148, 163, 184, 0.06)' },
          horzLines: { color: 'rgba(148, 163, 184, 0.06)' },
        },
      })
      const rsiLine = addLine(rsiChart, LW, { color: 'rgba(234, 88, 12, 0.9)', lineWidth: 2 })
      const rsiData = series.klines
        .map((k, i) => {
          const v = series.rsi6[i]
          return v == null ? null : { time: parseBusinessDay(k.date) as BusinessDay, value: v }
        })
        .filter(Boolean)
      rsiLine.setData(rsiData as any)
      rsiLine.createPriceLine?.({ price: 70, color: 'rgba(239,68,68,0.45)', lineWidth: 1, lineStyle: 2, title: '70' })
      rsiLine.createPriceLine?.({ price: 30, color: 'rgba(16,185,129,0.45)', lineWidth: 1, lineStyle: 2, title: '30' })
    }

    const sync = (range: any) => {
      try {
        macdChart?.timeScale().setVisibleRange(range)
        rsiChart?.timeScale().setVisibleRange(range)
      } catch {
        // ignore
      }
    }
    chart.timeScale().subscribeVisibleTimeRangeChange(sync)
    chart.subscribeCrosshairMove?.((param: any) => {
      const point = param?.point
      const dateKey = parseCrosshairDateKey(param?.time)
      if (!point || !dateKey || !series.klines.length) {
        setHoverTip(prev => (prev.visible ? { visible: false, x: 0, y: 0, row: null } : prev))
        return
      }
      const inBounds =
        point.x >= 0 &&
        point.y >= 0 &&
        point.x <= container.clientWidth &&
        point.y <= container.clientHeight
      if (!inBounds) {
        setHoverTip(prev => (prev.visible ? { visible: false, x: 0, y: 0, row: null } : prev))
        return
      }
      const idx = indexByDate.get(dateKey)
      if (idx == null || idx < 0 || idx >= series.klines.length) {
        setHoverTip(prev => (prev.visible ? { visible: false, x: 0, y: 0, row: null } : prev))
        return
      }

      const k = series.klines[idx]
      const tooltipWidth = 280
      const tooltipHeight = 152
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
        row: {
          date: k.date,
          open: k.open,
          high: k.high,
          low: k.low,
          close: k.close,
          ma5: series.ma5[idx],
          ma10: series.ma10[idx],
          ma20: series.ma20[idx],
          macd: series.macd.macd[idx],
          signal: series.macd.signal[idx],
          rsi6: series.rsi6[idx],
        },
      })
    })

    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: container.clientWidth })
      if (macdEl) macdChart?.applyOptions({ width: macdEl.clientWidth })
      if (macdEl && rsiChart) rsiChart?.applyOptions({ width: macdEl.clientWidth })
    })
    ro.observe(container)
    if (macdEl) ro.observe(macdEl)

    const total = series.candles.length
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
      try {
        macdChart?.remove()
      } catch {
        // ignore
      }
      try {
        rsiChart?.remove()
      } catch {
        // ignore
      }
    }
  }, [series, lwReady, showRsi, indexByDate, interval, mode])

  return (
    <div className="card p-4 md:p-5">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-2 mb-3">
        <div className="text-[13px] font-semibold text-foreground">{mode === 'minute' ? '分时走势' : 'K线图'}</div>
        <div className="flex items-center gap-2 flex-wrap">
          {mode === 'kline' ? (
            <Button variant={showRsi ? 'default' : 'secondary'} size="sm" className="h-8 px-2.5" onClick={() => setShowRsi(v => !v)}>
              强弱线
            </Button>
          ) : null}
          <div className="inline-flex rounded-lg border border-border/60 bg-accent/20 p-0.5">
            {([
              { value: 'minute', label: '分时' },
              { value: '1d', label: '日K' },
              { value: '1w', label: '周K' },
              { value: '1m', label: '月K' },
            ] as const).map(item => {
              const active = item.value === 'minute' ? mode === 'minute' : mode === 'kline' && interval === item.value
              return (
                <button
                  key={item.value}
                  type="button"
                  className={`h-7 min-w-[44px] rounded-md px-2.5 text-[12px] transition-colors ${
                    active
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                  }`}
                  onClick={() => {
                    if (item.value === 'minute') {
                      setMode('minute')
                    } else {
                      setMode('kline')
                      setIntervalValue(item.value)
                    }
                  }}
                >
                  {item.label}
                </button>
              )
            })}
          </div>
          <Button
            variant="secondary"
            size="sm"
            className="h-8"
            onClick={() => (mode === 'minute' ? void loadMinute() : void load())}
            disabled={mode === 'minute' ? minuteLoading : loading}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${(mode === 'minute' ? minuteLoading : loading) ? 'animate-spin' : ''}`} />
            <span className="hidden sm:inline">刷新</span>
          </Button>
        </div>
      </div>

      {mode === 'minute' ? (
        (() => {
          const W = 900
          const H = 380
          const PAD = 36
          const pts = minutePoints
          let pricePath = ''
          let avgPath = ''
          let lo = 0
          let hi = 0
          if (pts.length > 1) {
            const vals = pts.flatMap(p => [p.price, p.avg])
            lo = Math.min(...vals)
            hi = Math.max(...vals)
            const range = hi - lo || 1
            const stepX = (W - PAD * 2) / (pts.length - 1)
            const build = (get: (p: MinutePoint) => number) =>
              pts
                .map((p, i) => {
                  const x = PAD + i * stepX
                  const y = PAD + (1 - (get(p) - lo) / range) * (H - PAD * 2)
                  return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
                })
                .join(' ')
            pricePath = build(p => p.price)
            avgPath = build(p => p.avg)
          }
          const first = pts[0]
          const last = pts[pts.length - 1]
          const up = !!last && !!first && last.price >= first.price
          const changePct = last && first && first.price ? ((last.price - first.price) / first.price) * 100 : 0
          const totalVol = pts.reduce((s, p) => s + (p.volume || 0), 0)
          return (
            <div>
              {minuteError ? (
                <div className="text-[12px] text-rose-600 bg-rose-500/10 border border-rose-500/20 rounded-lg px-3 py-2 mb-3">
                  {minuteError}
                </div>
              ) : null}
              {minuteLoading && !pts.length ? (
                <div className="w-full h-[380px] rounded-xl border border-border/50 animate-pulse bg-accent/20" />
              ) : !pts.length ? (
                <div className="w-full h-[380px] rounded-xl border border-border/50 flex items-center justify-center text-[12px] text-muted-foreground">
                  暂无分时数据（非交易日 / 停牌 / 数据源不可用）
                </div>
              ) : (
                <>
                  <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-3">
                    <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]"><span className="text-muted-foreground">现价</span> <span className={`font-mono ml-1 ${up ? 'text-rose-500' : 'text-emerald-500'}`}>{last?.price.toFixed(2)}</span></div>
                    <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]"><span className="text-muted-foreground">较开盘</span> <span className={`font-mono ml-1 ${changePct >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>{changePct >= 0 ? '+' : ''}{changePct.toFixed(2)}%</span></div>
                    <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]"><span className="text-muted-foreground">均价</span> <span className="font-mono ml-1">{last?.avg.toFixed(2)}</span></div>
                    <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]"><span className="text-muted-foreground">区间</span> <span className="font-mono ml-1">{hi.toFixed(2)}/{lo.toFixed(2)}</span></div>
                    <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]"><span className="text-muted-foreground">成交量</span> <span className="font-mono ml-1">{(totalVol / 10000).toFixed(1)}万手</span></div>
                  </div>
                  <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-[380px] rounded-xl border border-border/50 bg-card">
                    {[0, 0.25, 0.5, 0.75, 1].map(f => (
                      <line key={f} x1={PAD} y1={PAD + f * (H - PAD * 2)} x2={W - PAD} y2={PAD + f * (H - PAD * 2)} stroke="currentColor" className="text-border" strokeWidth={0.5} opacity={0.5} />
                    ))}
                    <text x={PAD} y={PAD - 8} className="fill-muted-foreground" fontSize="11">{hi.toFixed(2)}</text>
                    <text x={PAD} y={H - PAD + 16} className="fill-muted-foreground" fontSize="11">{lo.toFixed(2)}</text>
                    <text x={W - PAD - 30} y={H - PAD + 16} className="fill-muted-foreground" fontSize="11">{last?.t}</text>
                    <path d={avgPath} fill="none" stroke="#f59e0b" strokeWidth={1.2} opacity={0.85} />
                    <path d={pricePath} fill="none" stroke={up ? '#f43f5e' : '#10b981'} strokeWidth={1.6} />
                  </svg>
                  <div className="mt-2 flex gap-4 text-[11px] text-muted-foreground">
                    <span className="flex items-center gap-1"><span className="inline-block w-3 h-0.5 bg-rose-400" /> 价格</span>
                    <span className="flex items-center gap-1"><span className="inline-block w-3 h-0.5 bg-amber-400" /> 均价(成交额/成交股数)</span>
                    <span>腾讯实时分时 · 每30秒自动刷新 · {pts.length} 个点</span>
                  </div>
                </>
              )}
            </div>
          )
        })()
      ) : (
      <>
      {error ? (
        <div className="text-[12px] text-rose-600 bg-rose-500/10 border border-rose-500/20 rounded-lg px-3 py-2 mb-3">
          {error}
        </div>
      ) : null}

      {!lwReady && libError ? (
        <div className="text-[12px] text-amber-700 dark:text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2 mb-3">
          图表库加载失败（网络受限时可能发生）。可稍后重试或检查网络/代理。
        </div>
      ) : null}

      {showSkeleton ? (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-3 animate-pulse">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="rounded-lg bg-accent/20 px-2.5 py-2">
              <div className="h-3 w-14 bg-accent/60 rounded" />
              <div className="h-3 w-16 bg-accent/60 rounded mt-2" />
            </div>
          ))}
        </div>
      ) : latestMetrics ? (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-3">
          <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]"><span className="text-muted-foreground">最新价</span> <span className="font-mono ml-1">{latestMetrics.last.close.toFixed(2)}</span></div>
          <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]"><span className="text-muted-foreground">涨跌</span> <span className={`font-mono ml-1 ${latestMetrics.changePct >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>{latestMetrics.changePct >= 0 ? '+' : ''}{latestMetrics.changePct.toFixed(2)}%</span></div>
          <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]"><span className="text-muted-foreground">振幅</span> <span className="font-mono ml-1">{latestMetrics.ampPct.toFixed(2)}%</span></div>
          <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]"><span className="text-muted-foreground">区间高低</span> <span className="font-mono ml-1">{latestMetrics.maxHigh.toFixed(2)}/{latestMetrics.minLow.toFixed(2)}</span></div>
          <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]"><span className="text-muted-foreground">均量</span> <span className="font-mono ml-1">{(latestMetrics.avgVol / 10000).toFixed(1)}万</span></div>
        </div>
      ) : null}
      <div className="relative">
        {showSkeleton ? (
          <div className="w-full h-[380px] rounded-xl overflow-hidden border border-border/50 p-3 animate-pulse">
            <div className="h-full w-full rounded-lg bg-accent/20" />
          </div>
        ) : (
          <div ref={containerRef} className="w-full h-[380px] rounded-xl overflow-hidden border border-border/50" />
        )}
        {hoverTip.visible && hoverTip.row ? (
          <div
            className="pointer-events-none absolute z-10 w-[280px] rounded-lg border border-border/60 bg-card/95 px-3 py-2 shadow-lg backdrop-blur-[2px]"
            style={{ left: `${hoverTip.x}px`, top: `${hoverTip.y}px` }}
          >
            <div className="text-[11px] text-foreground font-medium mb-1.5">{hoverTip.row.date}</div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
              <span>开盘价 <span className="font-mono text-foreground">{hoverTip.row.open.toFixed(2)}</span></span>
              <span>收盘价 <span className="font-mono text-foreground">{hoverTip.row.close.toFixed(2)}</span></span>
              <span>最高价 <span className="font-mono text-foreground">{hoverTip.row.high.toFixed(2)}</span></span>
              <span>最低价 <span className="font-mono text-foreground">{hoverTip.row.low.toFixed(2)}</span></span>
              <span>5日均线 <span className="font-mono text-foreground">{hoverTip.row.ma5 != null ? hoverTip.row.ma5.toFixed(2) : '--'}</span></span>
              <span>10日均线 <span className="font-mono text-foreground">{hoverTip.row.ma10 != null ? hoverTip.row.ma10.toFixed(2) : '--'}</span></span>
              <span>20日均线 <span className="font-mono text-foreground">{hoverTip.row.ma20 != null ? hoverTip.row.ma20.toFixed(2) : '--'}</span></span>
              <span>MACD线 <span className="font-mono text-foreground">{hoverTip.row.macd != null ? hoverTip.row.macd.toFixed(3) : '--'}</span></span>
              <span>信号线 <span className="font-mono text-foreground">{hoverTip.row.signal != null ? hoverTip.row.signal.toFixed(3) : '--'}</span></span>
              <span>RSI强弱 <span className="font-mono text-foreground">{hoverTip.row.rsi6 != null ? hoverTip.row.rsi6.toFixed(1) : '--'}</span></span>
            </div>
          </div>
        ) : null}
      </div>
      <div className="mt-3 grid grid-cols-1 gap-3">
        <div>
          <div className="text-[11px] text-muted-foreground mb-1">动能指标（MACD{showRsi ? ' + RSI强弱线' : ''}）</div>
          <div className="text-[11px] text-muted-foreground mb-2 rounded-lg bg-accent/15 border border-border/40 px-2.5 py-1.5">
            MACD 用来看趋势动能和拐点；RSI 用来看是否偏热/偏弱（一般 70 以上偏热，30 以下偏弱）。
          </div>
          {showSkeleton ? (
            <div className="w-full h-[150px] rounded-xl overflow-hidden border border-border/50 animate-pulse">
              <div className="h-full w-full bg-accent/20" />
            </div>
          ) : (
            <div ref={macdRef} className="w-full h-[150px] rounded-xl overflow-hidden border border-border/50" />
          )}
        </div>
      </div>
      </>
      )}
    </div>
  )
}
