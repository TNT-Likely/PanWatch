import { useEffect, useState } from 'react'
import { fetchAPI } from '@panwatch/api'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'

interface MinutePoint {
  t: string
  price: number
  avg: number
  volume: number
}

interface MinuteResponse {
  symbol: string
  market: string
  points: MinutePoint[]
}

interface MinuteDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  symbol: string
  market: string
  stockName?: string
}

const W = 520
const H = 240
const PAD = 30

export function MinuteDialog({ open, onOpenChange, symbol, market, stockName }: MinuteDialogProps) {
  const [points, setPoints] = useState<MinutePoint[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    setError(null)
    fetchAPI<MinuteResponse>(`/quotes/minute?symbol=${symbol}&market=${market}`)
      .then((d) => setPoints(d.points || []))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [open, symbol, market])

  // 计算 SVG path
  let pricePath = ''
  let avgPath = ''
  let minPrice = 0
  let maxPrice = 0
  if (points.length > 1) {
    const prices = points.map((p) => p.price)
    minPrice = Math.min(...prices)
    maxPrice = Math.max(...prices)
    const range = maxPrice - minPrice || 1
    const stepX = (W - PAD * 2) / (points.length - 1)
    pricePath = points
      .map((p, i) => {
        const x = PAD + i * stepX
        const y = PAD + (1 - (p.price - minPrice) / range) * (H - PAD * 2)
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
      })
      .join(' ')
    avgPath = points
      .map((p, i) => {
        const x = PAD + i * stepX
        const y = PAD + (1 - (p.avg - minPrice) / range) * (H - PAD * 2)
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
      })
      .join(' ')
  }

  const last = points[points.length - 1]
  const up = last && last.price >= (points[0]?.price ?? last.price)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[600px]">
        <DialogHeader>
          <DialogTitle>
            分时 · {stockName || symbol}
            <span className="ml-2 text-sm text-muted-foreground">{symbol}</span>
          </DialogTitle>
        </DialogHeader>

        {loading && <div className="py-8 text-center text-muted-foreground">加载中…</div>}
        {error && <div className="py-8 text-center text-red-500">加载失败: {error}</div>}
        {!loading && !error && points.length === 0 && (
          <div className="py-8 text-center text-muted-foreground">暂无分时数据(非交易日或停牌)</div>
        )}

        {!loading && !error && points.length > 1 && (
          <div className="space-y-3">
            <div className="flex items-center justify-between text-sm">
              <span className={up ? 'text-rose-400' : 'text-emerald-400'}>
                现价 {last?.price?.toFixed(2)} {up ? '↑' : '↓'}
              </span>
              <span className="text-muted-foreground">均价 {last?.avg?.toFixed(2)}</span>
              <span className="text-muted-foreground">
                区间 {minPrice.toFixed(2)} ~ {maxPrice.toFixed(2)}
              </span>
              <span className="text-muted-foreground">点 {points.length}</span>
            </div>

            <svg width="100%" viewBox={`0 0 ${W} ${H}`} className="border rounded bg-card">
              {/* 网格线 */}
              <line x1={PAD} y1={PAD} x2={W - PAD} y2={PAD} stroke="#333" strokeWidth={0.5} />
              <line x1={PAD} y1={H / 2} x2={W - PAD} y2={H / 2} stroke="#333" strokeWidth={0.5} />
              <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} stroke="#333" strokeWidth={0.5} />
              {/* 均价线 */}
              <path d={avgPath} fill="none" stroke="#f59e0b" strokeWidth={1} opacity={0.8} />
              {/* 价格线 */}
              <path d={pricePath} fill="none" stroke={up ? '#f43f5e' : '#10b981'} strokeWidth={1.5} />
            </svg>

            <div className="flex gap-4 text-xs text-muted-foreground">
              <span className="flex items-center gap-1">
                <span className="inline-block w-3 h-0.5 bg-rose-400" /> 价格
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block w-3 h-0.5 bg-amber-400" /> 均价
              </span>
              <span>腾讯实时分时 · 每15秒刷新建议重开</span>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
