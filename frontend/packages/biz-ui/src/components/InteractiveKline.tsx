import { useEffect, useMemo, useRef, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { fetchAPI } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@panwatch/base-ui/components/ui/select'

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

function parseBusinessDay(dateStr: string): BusinessDay | null {
  const m = String(dateStr || '').trim().match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!m) return null
  return { year: Number(m[1]), month: Number(m[2]), day: Number(m[3]) }
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
  const [days, setDays] = useState<'60' | '120' | '250'>(props.initialDays || '120')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>('')
  const [data, setData] = useState<KlineItem[]>([])
  const [showRsi, setShowRsi] = useState(true)
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)

  const containerRef = useRef<HTMLDivElement | null>(null)
  const macdRef = useRef<HTMLDivElement | null>(null)

  const load = async () => {
    if (!props.symbol) return
    setLoading(true)
    setError('')
    try {
      const res = await fetchAPI<KlinesResponse>(
        `/klines/${encodeURIComponent(props.symbol)}?market=${encodeURIComponent(props.market)}&days=${encodeURIComponent(days)}&interval=${encodeURIComponent(interval)}`
      )
      setData(res.klines || [])
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载K线失败')
      setData([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.symbol, props.market, days, interval])

  useEffect(() => {
    if (props.initialInterval) setIntervalValue(props.initialInterval)
  }, [props.initialInterval, props.symbol, props.market])

  useEffect(() => {
    if (props.initialDays) setDays(props.initialDays)
  }, [props.initialDays, props.symbol, props.market])

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

  const hoverRow = useMemo(() => {
    if (hoverIdx == null || hoverIdx < 0 || hoverIdx >= series.klines.length) return null
    const k = series.klines[hoverIdx]
    return {
      k,
      ma5: series.ma5[hoverIdx],
      ma10: series.ma10[hoverIdx],
      ma20: series.ma20[hoverIdx],
      macd: series.macd.macd[hoverIdx],
      sig: series.macd.signal[hoverIdx],
      hist: series.macd.hist[hoverIdx],
      rsi6: series.rsi6[hoverIdx],
    }
  }, [hoverIdx, series])

  useEffect(() => {
    const LW = getLW()
    if (!LW || !lwReady) return
    if (!containerRef.current) return
    if (series.candles.length < 20) return

    const container = containerRef.current
    const macdEl = macdRef.current

    container.innerHTML = ''
    if (macdEl) macdEl.innerHTML = ''

    const rootStyle = getComputedStyle(document.documentElement)
    const bg = rootStyle.getPropertyValue('--card').trim()
    const fg = rootStyle.getPropertyValue('--foreground').trim()

    const chart = LW.createChart(container, {
      width: container.clientWidth,
      height: 380,
      layout: {
        background: { color: `hsl(${bg})` },
        textColor: `hsl(${fg} / 0.85)`,
      },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
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
      const t = param?.time
      if (!t || !series.klines.length) {
        setHoverIdx(null)
        return
      }
      const idx = series.klines.findIndex(k => {
        const d = parseBusinessDay(k.date)
        return d && d.year === t.year && d.month === t.month && d.day === t.day
      })
      setHoverIdx(idx >= 0 ? idx : null)
    })

    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: container.clientWidth })
      if (macdEl) macdChart?.applyOptions({ width: macdEl.clientWidth })
      if (macdEl && rsiChart) rsiChart?.applyOptions({ width: macdEl.clientWidth })
    })
    ro.observe(container)
    if (macdEl) ro.observe(macdEl)

    chart.timeScale().fitContent()
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
  }, [series, lwReady, showRsi])

  return (
    <div className="card p-4 md:p-5">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-2 mb-3">
        <div className="text-[13px] font-semibold text-foreground">K线图</div>
        <div className="flex items-center gap-2">
          <Button variant={showRsi ? 'default' : 'secondary'} size="sm" className="h-8 px-2.5" onClick={() => setShowRsi(v => !v)}>
            RSI
          </Button>
          <Select value={interval} onValueChange={(v) => setIntervalValue(v as any)}>
            <SelectTrigger className="h-8 w-[90px] text-[12px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="1d">日K</SelectItem>
              <SelectItem value="1w">周K</SelectItem>
              <SelectItem value="1m">月K</SelectItem>
            </SelectContent>
          </Select>
          <Select value={days} onValueChange={(v) => setDays(v as '60' | '120' | '250')}>
            <SelectTrigger className="h-8 w-[110px] text-[12px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="60">近 60 天</SelectItem>
              <SelectItem value="120">近 120 天</SelectItem>
              <SelectItem value="250">近 250 天</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="secondary" size="sm" className="h-8" onClick={load} disabled={loading}>
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

      {!lwReady && libError ? (
        <div className="text-[12px] text-amber-700 dark:text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2 mb-3">
          图表库加载失败（网络受限时可能发生）。可稍后重试或检查网络/代理。
        </div>
      ) : null}

      {latestMetrics && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-3">
          <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]"><span className="text-muted-foreground">最新价</span> <span className="font-mono ml-1">{latestMetrics.last.close.toFixed(2)}</span></div>
          <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]"><span className="text-muted-foreground">涨跌</span> <span className={`font-mono ml-1 ${latestMetrics.changePct >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>{latestMetrics.changePct >= 0 ? '+' : ''}{latestMetrics.changePct.toFixed(2)}%</span></div>
          <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]"><span className="text-muted-foreground">振幅</span> <span className="font-mono ml-1">{latestMetrics.ampPct.toFixed(2)}%</span></div>
          <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]"><span className="text-muted-foreground">区间高低</span> <span className="font-mono ml-1">{latestMetrics.maxHigh.toFixed(2)}/{latestMetrics.minLow.toFixed(2)}</span></div>
          <div className="rounded-lg bg-accent/20 px-2.5 py-2 text-[11px]"><span className="text-muted-foreground">均量</span> <span className="font-mono ml-1">{(latestMetrics.avgVol / 10000).toFixed(1)}万</span></div>
        </div>
      )}

      {hoverRow && (
        <div className="mb-2 rounded-lg bg-accent/15 border border-border/40 px-3 py-2 text-[11px] text-muted-foreground flex flex-wrap gap-x-3 gap-y-1">
          <span>{hoverRow.k.date}</span>
          <span>O {hoverRow.k.open.toFixed(2)}</span>
          <span>H {hoverRow.k.high.toFixed(2)}</span>
          <span>L {hoverRow.k.low.toFixed(2)}</span>
          <span>C {hoverRow.k.close.toFixed(2)}</span>
          <span>MA5 {hoverRow.ma5 != null ? hoverRow.ma5.toFixed(2) : '--'}</span>
          <span>MA10 {hoverRow.ma10 != null ? hoverRow.ma10.toFixed(2) : '--'}</span>
          <span>MACD {hoverRow.macd != null ? hoverRow.macd.toFixed(3) : '--'}</span>
          <span>Signal {hoverRow.sig != null ? hoverRow.sig.toFixed(3) : '--'}</span>
          <span>RSI6 {hoverRow.rsi6 != null ? hoverRow.rsi6.toFixed(1) : '--'}</span>
        </div>
      )}

      <div ref={containerRef} className="w-full rounded-xl overflow-hidden border border-border/50" />
      <div className="mt-3 grid grid-cols-1 gap-3">
        <div>
          <div className="text-[11px] text-muted-foreground mb-1">MACD{showRsi ? ' + RSI' : ''}</div>
          <div ref={macdRef} className="w-full rounded-xl overflow-hidden border border-border/50" />
        </div>
      </div>
    </div>
  )
}
