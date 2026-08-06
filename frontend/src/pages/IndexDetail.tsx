import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, TrendingUp, RefreshCw, Activity, BarChart3 } from 'lucide-react'
import { fetchAPI } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import InteractiveKline from '@panwatch/biz-ui/components/InteractiveKline'

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
  klines: { date: string; open: number; close: number; high: number; low: number; volume: number }[]
  amount_trend: { date: string; amount: number }[]
  note?: string
}

// 成交额柱状图(大盘资金流替代: 近20日成交额)
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
          {/* 实时行情卡片(同个股详情风格) */}
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

          {/* K线走势(复用个股同款 InteractiveKline: MA/成交量/MACD/RSI + 日周月) */}
          <div className="card p-4">
            <div className="flex items-center gap-2 mb-2">
              <Activity className="h-4 w-4" />
              <span className="font-bold">K线走势</span>
              <span className="text-[10px] text-muted-foreground">MA/成交量/MACD/RSI · 日K/周K/月K 切换</span>
            </div>
            <InteractiveKline symbol={symbol || ''} market={data.market || 'CN'} initialInterval="1d" initialDays="120" />
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
