import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, TrendingUp, RefreshCw, Activity, BarChart3 } from 'lucide-react'
import { fetchAPI } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'

interface Kline {
  date: string
  open: number
  close: number
  high: number
  low: number
  volume: number
}

interface IndexDetail {
  symbol: string
  name: string
  market: string
  quote: {
    current_price: number
    change_pct: number
    change_amount: number
    prev_close: number
    open?: number | null
    high?: number | null
    low?: number | null
    volume?: number | null
    amount?: number | null
  } | null
  klines: Kline[]
  amount_trend: { date: string; amount: number }[]
  note?: string
}

// 简易 K 线图(SVG,无重依赖)
function KlineChart({ klines }: { klines: Kline[] }) {
  const [range, setRange] = useState(60) // 显示最近 N 根
  const data = klines.slice(-range)
  if (data.length === 0) return <div className="text-sm text-muted-foreground py-8 text-center">暂无K线数据</div>

  const W = 720, H = 260, PAD = 12
  const maxP = Math.max(...data.map(k => k.high)) * 1.02
  const minP = Math.min(...data.map(k => k.low)) * 0.98
  const maxV = Math.max(...data.map(k => k.volume))
  const bw = (W - PAD * 2) / data.length
  const y = (p: number) => PAD + (maxP - p) / (maxP - minP) * (H * 0.72)
  const vy = (v: number) => H * 0.78 + (1 - v / maxV) * (H * 0.2)

  return (
    <div>
      <div className="flex items-center gap-1 mb-2">
        {[30, 60, 120].map(n => (
          <button
            key={n}
            onClick={() => setRange(n)}
            className={`px-2 py-0.5 text-[11px] rounded ${range === n ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground'}`}
          >
            {n}日
          </button>
        ))}
        <span className="ml-auto text-[11px] text-muted-foreground">
          {data[0].date} ~ {data[data.length - 1].date}
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 300 }}>
        {data.map((k, i) => {
          const x = PAD + i * bw + bw / 2
          const up = k.close >= k.open
          const color = up ? '#f85149' : '#3fb950'
          return (
            <g key={k.date}>
              {/* 影线 */}
              <line x1={x} y1={y(k.high)} x2={x} y2={y(k.low)} stroke={color} strokeWidth={1} />
              {/* 实体 */}
              <rect
                x={x - bw * 0.32}
                y={Math.min(y(k.open), y(k.close))}
                width={bw * 0.64}
                height={Math.max(2, Math.abs(y(k.open) - y(k.close)))}
                fill={color}
              />
              {/* 成交量 */}
              <rect x={x - bw * 0.3} y={vy(k.volume)} width={bw * 0.6} height={H * 0.2 - (vy(k.volume) - H * 0.78)} fill={color} opacity={0.25} />
            </g>
          )
        })}
      </svg>
    </div>
  )
}

// 成交额柱状图
function AmountChart({ trend }: { trend: { date: string; amount: number }[] }) {
  if (trend.length === 0) return null
  const maxA = Math.max(...trend.map(t => t.amount))
  const W = 720, H = 90
  const bw = W / trend.length
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 100 }}>
      {trend.map((t, i) => {
        const h = (t.amount / maxA) * (H - 16)
        return (
          <g key={t.date}>
            <rect x={i * bw + bw * 0.2} y={H - 8 - h} width={bw * 0.6} height={h} fill="#58a6ff" opacity={0.7} rx={1} />
            {i % 5 === 0 && (
              <text x={i * bw + bw / 2} y={H - 2} fontSize={8} fill="#8b949e" textAnchor="middle">
                {t.date.slice(5)}
              </text>
            )}
          </g>
        )
      })}
    </svg>
  )
}

export default function IndexDetailPage() {
  const { symbol } = useParams<{ symbol: string }>()
  const navigate = useNavigate()
  const [data, setData] = useState<IndexDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const d = await fetchAPI<IndexDetail>(`/market/indices/${symbol}`)
      if (d?.error) setError(d.error)
      else setData(d)
    } catch (e: any) {
      setError(e?.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [symbol])

  const q = data?.quote
  const up = (q?.change_pct || 0) >= 0

  return (
    <div className="space-y-5 p-4 md:p-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" className="h-8" onClick={() => navigate(-1)}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <TrendingUp className="h-6 w-6" /> {data?.name || '大盘指数'}
          </h1>
          <div className="text-xs text-muted-foreground font-mono">{symbol}</div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="outline" size="sm" className="h-8" onClick={load} disabled={loading}>
            <RefreshCw className={`h-3.5 w-3.5 mr-1 ${loading ? 'animate-spin' : ''}`} /> 刷新
          </Button>
        </div>
      </div>

      {loading ? (
        <div className="text-center text-muted-foreground py-12">加载中...</div>
      ) : error ? (
        <div className="text-center text-red-500 py-12">{error}</div>
      ) : data ? (
        <>
          {/* 实时行情卡片 */}
          <div className="card p-4">
            <div className="flex items-end gap-4 flex-wrap">
              <div>
                <div className="text-3xl font-mono font-bold">{q?.current_price?.toFixed(2) ?? '--'}</div>
                <div className={`text-sm font-mono ${up ? 'text-red-500' : 'text-green-500'}`}>
                  {q?.change_amount != null && q.change_amount > 0 ? '+' : ''}{q?.change_amount?.toFixed(2)} ({q?.change_pct?.toFixed(2)}%)
                </div>
              </div>
              <div className="flex gap-6 text-sm text-muted-foreground">
                <div><span className="block text-[10px]">昨收</span><span className="font-mono text-foreground">{q?.prev_close?.toFixed(2) ?? '--'}</span></div>
                <div><span className="block text-[10px]">今开</span><span className="font-mono text-foreground">{q?.open?.toFixed(2) ?? '--'}</span></div>
                <div><span className="block text-[10px]">最高</span><span className="font-mono text-foreground">{q?.high?.toFixed(2) ?? '--'}</span></div>
                <div><span className="block text-[10px]">最低</span><span className="font-mono text-foreground">{q?.low?.toFixed(2) ?? '--'}</span></div>
                <div><span className="block text-[10px]">成交量</span><span className="font-mono text-foreground">{q?.volume != null ? (q.volume / 1e8).toFixed(2) + '亿' : '--'}</span></div>
              </div>
            </div>
          </div>

          {/* K线图 */}
          <div className="card p-4">
            <div className="flex items-center gap-2 mb-2">
              <Activity className="h-4 w-4" />
              <span className="font-bold">K线走势</span>
            </div>
            <KlineChart klines={data.klines} />
          </div>

          {/* 成交额趋势(大盘资金流替代) */}
          <div className="card p-4">
            <div className="flex items-center gap-2 mb-2">
              <BarChart3 className="h-4 w-4" />
              <span className="font-bold">成交额趋势(近20日)</span>
              <span className="text-[10px] text-muted-foreground">单位:亿元</span>
            </div>
            <AmountChart trend={data.amount_trend} />
            {data.note && <div className="text-[10px] text-amber-500 mt-2">{data.note}</div>}
          </div>
        </>
      ) : null}
    </div>
  )
}
