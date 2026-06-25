import { useEffect, useRef } from 'react'
import {
  AreaSeries,
  createChart,
  type IChartApi,
  type Time,
} from 'lightweight-charts'

export interface EtfNavChartProps {
  /** [{date:'YYYY-MM-DD', unit_nav, cum_nav}] 升序 */
  data: Array<{ date: string; unit_nav: number | null; cum_nav: number | null }>
  /** 画哪条曲线,默认单位净值 */
  field?: 'unit_nav' | 'cum_nav'
  height?: number
}

/**
 * ETF 净值曲线 —— 基于 lightweight-charts AreaSeries。
 * 无外部交互态,数据驱动重绘;容器为空时跳过。
 */
export function EtfNavChart({ data, field = 'unit_nav', height = 240 }: EtfNavChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      height,
      width: containerRef.current.clientWidth,
      layout: {
        background: { color: 'transparent' },
        textColor: '#94a3b8',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: 'rgba(148,163,184,0.08)' },
        horzLines: { color: 'rgba(148,163,184,0.08)' },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: false },
      crosshair: { mode: 0 },
    })
    chartRef.current = chart

    const series = chart.addSeries(AreaSeries, {
      lineColor: '#3b82f6',
      topColor: 'rgba(59,130,246,0.25)',
      bottomColor: 'rgba(59,130,246,0.02)',
      lineWidth: 2,
      priceLineVisible: false,
    })

    const points = data
      .filter((p) => p[field] != null && p.date)
      .map((p) => ({ time: p.date as Time, value: p[field] as number }))
    series.setData(points)
    chart.timeScale().fitContent()

    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: containerRef.current?.clientWidth ?? 0 })
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [data, field, height])

  return <div ref={containerRef} style={{ height }} className="w-full" />
}
