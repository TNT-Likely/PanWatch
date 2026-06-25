import { useCallback, useEffect, useMemo, useState } from 'react'
import { Activity, AlertTriangle, CheckCircle2, Eye, RefreshCw, ShieldAlert, Target, TrendingUp } from 'lucide-react'
import { fetchAPI, stocksApi, watchlistApi, type WatchlistSignalResult } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Badge } from '@panwatch/base-ui/components/ui/badge'
import { Skeleton } from '@panwatch/base-ui/components/ui/skeleton'

interface StockItem {
  id: number
  symbol: string
  name: string
  market: string
}

interface PositionItem {
  id: number
  stock_id: number
  symbol: string
  name: string
  market: string
  cost_price: number
  quantity: number
  trading_style: string
  current_price: number | null
  change_pct: number | null
  pnl_pct: number | null
}

interface AccountSummary {
  id: number
  name: string
  positions: PositionItem[]
}

interface PortfolioSummary {
  accounts: AccountSummary[]
}

interface QuoteResponse {
  symbol: string
  market: string
  current_price: number | null
  change_pct: number | null
}

interface KlineSummaryResponse {
  symbol: string
  market: string
  summary: Record<string, any>
}

interface WatchRow {
  key: string
  symbol: string
  name: string
  market: string
  source: 'position' | 'watch'
  accountName?: string
  quantity?: number
  costPrice?: number
  quote: Record<string, any>
  technical: Record<string, any>
  decision: WatchlistSignalResult
}

interface WatchTarget {
  key: string
  symbol: string
  name: string
  market: string
  source: 'position' | 'watch'
  accountName?: string
  position: PositionItem | null
}

const marketLabel = (market: string) => market === 'HK' ? '港股' : market === 'US' ? '美股' : 'A股'

const labelTone: Record<string, string> = {
  买入候选: 'bg-rose-500/15 text-rose-500 border-rose-500/30',
  加仓观察: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
  继续持有: 'bg-blue-500/15 text-blue-500 border-blue-500/30',
  减仓警告: 'bg-amber-500/15 text-amber-500 border-amber-500/30',
  止损触发: 'bg-red-500/15 text-red-500 border-red-500/30',
  禁止追高: 'bg-orange-500/15 text-orange-500 border-orange-500/30',
  观察: 'bg-accent text-muted-foreground border-border/60',
}

const labelPriority: Record<string, number> = {
  止损触发: 6,
  减仓警告: 5,
  禁止追高: 4,
  买入候选: 3,
  加仓观察: 2,
  继续持有: 1,
  观察: 0,
}

const toNumber = (value: unknown): number | null => {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim()) {
    const n = Number(value)
    if (Number.isFinite(n)) return n
  }
  return null
}

const formatPrice = (value: unknown) => {
  const n = toNumber(value)
  if (n == null) return '--'
  return n >= 100 ? n.toFixed(2) : n.toFixed(3).replace(/0+$/, '').replace(/\.$/, '')
}

const formatPct = (value: unknown) => {
  const n = toNumber(value)
  if (n == null) return '--'
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

const makeQuotePayload = (quote?: QuoteResponse, position?: PositionItem) => ({
  current_price: quote?.current_price ?? position?.current_price ?? null,
  change_pct: quote?.change_pct ?? position?.change_pct ?? null,
})

const buildPositionPayload = (position: PositionItem | null, technical: Record<string, any>) => {
  const support = toNumber(technical.support ?? technical.support_m ?? technical.support_s)
  const resistance = toNumber(technical.resistance ?? technical.resistance_m ?? technical.resistance_s)
  if (position) {
    const cost = Number(position.cost_price || 0)
    const supportStop = support && support > 0 ? support * 0.985 : null
    const costStop = cost > 0 ? cost * 0.94 : null
    const stopLoss = supportStop && costStop ? Math.max(supportStop, costStop) : supportStop ?? costStop
    const targetPrice = resistance && resistance > 0 ? Math.max(resistance, cost * 1.1) : cost > 0 ? cost * 1.12 : null
    return {
      has_position: true,
      avg_cost: cost,
      quantity: position.quantity,
      stop_loss: stopLoss,
      target_price: targetPrice,
      trading_style: position.trading_style || 'swing',
    }
  }
  return {
    has_position: false,
    stop_loss: support && support > 0 ? support * 0.99 : null,
    target_price: resistance && resistance > 0 ? resistance : null,
  }
}

const getDecisionIcon = (label: string) => {
  if (label === '止损触发') return ShieldAlert
  if (label === '减仓警告' || label === '禁止追高') return AlertTriangle
  if (label === '买入候选' || label === '加仓观察') return TrendingUp
  if (label === '继续持有') return CheckCircle2
  return Eye
}

export default function WatchlistPage() {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [rows, setRows] = useState<WatchRow[]>([])
  const [lastRunAt, setLastRunAt] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [stocks, portfolio] = await Promise.all([
        stocksApi.list() as Promise<StockItem[]>,
        fetchAPI<PortfolioSummary>('/portfolio/summary?include_quotes=false'),
      ])

      const positions = (portfolio.accounts || []).flatMap((account) =>
        (account.positions || []).map((position) => ({ ...position, accountName: account.name })),
      )
      const positionKeys = new Set(positions.map((p) => `${p.market}:${p.symbol}`))
      const targets: WatchTarget[] = [
        ...positions.map((p) => ({
          key: `position:${p.market}:${p.symbol}:${p.id}`,
          symbol: p.symbol,
          name: p.name,
          market: p.market,
          source: 'position' as const,
          accountName: p.accountName,
          position: p,
        })),
        ...stocks
          .filter((stock) => !positionKeys.has(`${stock.market}:${stock.symbol}`))
          .map((stock) => ({
            key: `watch:${stock.market}:${stock.symbol}:${stock.id}`,
            symbol: stock.symbol,
            name: stock.name,
            market: stock.market,
            source: 'watch' as const,
            position: null,
          })),
      ]

      if (!targets.length) {
        setRows([])
        setLastRunAt(new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }))
        return
      }

      const quoteItems = targets.map((target) => ({ symbol: target.symbol, market: target.market }))
      const [quoteRows, klineRows] = await Promise.all([
        fetchAPI<QuoteResponse[]>('/quotes/batch', {
          method: 'POST',
          body: JSON.stringify({ items: quoteItems }),
          timeoutMs: 25_000,
        }).catch(() => []),
        Promise.all(
          targets.map((target) =>
            fetchAPI<KlineSummaryResponse>(
              `/klines/${encodeURIComponent(target.symbol)}/summary?market=${encodeURIComponent(target.market)}`,
              { timeoutMs: 25_000 },
            ).catch(() => ({
              symbol: target.symbol,
              market: target.market,
              summary: {},
            })),
          ),
        ),
      ])

      const quoteMap = new Map(quoteRows.map((quote) => [`${quote.market}:${quote.symbol}`, quote]))
      const klineMap = new Map(klineRows.map((row) => [`${row.market}:${row.symbol}`, row.summary || {}]))

      const decisions = await Promise.all(
        targets.map(async (target) => {
          const quote = makeQuotePayload(quoteMap.get(`${target.market}:${target.symbol}`), target.position || undefined)
          const technical = klineMap.get(`${target.market}:${target.symbol}`) || {}
          const decision = await watchlistApi.evaluateSignal({
            symbol: target.symbol,
            name: target.name,
            market: target.market,
            quote,
            technical,
            position: buildPositionPayload(target.position, technical),
          })
          return {
            key: target.key,
            symbol: target.symbol,
            name: target.name,
            market: target.market,
            source: target.source,
            accountName: target.accountName,
            quantity: target.position?.quantity,
            costPrice: target.position?.cost_price,
            quote,
            technical,
            decision,
          }
        }),
      )

      decisions.sort((a, b) => {
        const priorityDelta = (labelPriority[b.decision.label] || 0) - (labelPriority[a.decision.label] || 0)
        if (priorityDelta !== 0) return priorityDelta
        return Number(b.decision.score || 0) - Number(a.decision.score || 0)
      })
      setRows(decisions)
      setLastRunAt(new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }))
    } catch (e) {
      setError(e instanceof Error ? e.message : '盯盘评估失败')
      setRows([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const summary = useMemo(() => {
    const urgent = rows.filter((row) => ['止损触发', '减仓警告', '禁止追高'].includes(row.decision.label)).length
    const candidates = rows.filter((row) => ['买入候选', '加仓观察'].includes(row.decision.label)).length
    const holdings = rows.filter((row) => row.source === 'position').length
    return { urgent, candidates, holdings, total: rows.length }
  }, [rows])

  return (
    <div className="max-w-[1500px] mx-auto space-y-5">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <div className="w-9 h-9 rounded-xl bg-primary/12 text-primary flex items-center justify-center">
              <Activity className="w-5 h-5" />
            </div>
            <div>
              <h1 className="text-[24px] md:text-[28px] font-bold tracking-normal">今日盯盘</h1>
              <p className="text-[12px] text-muted-foreground mt-0.5">规则信号先行，AI 只负责解释；所有动作保留人工确认。</p>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {lastRunAt && <span className="text-[11px] text-muted-foreground">最近评估 {lastRunAt}</span>}
          <Button variant="secondary" onClick={load} disabled={loading}>
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            刷新评估
          </Button>
        </div>
      </div>

      {error && (
        <div className="card border-red-500/25 bg-red-500/8 px-4 py-3 text-[13px] text-red-500">
          {error}
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricCard icon={Target} label="盯盘标的" value={summary.total} />
        <MetricCard icon={ShieldAlert} label="高优先级" value={summary.urgent} tone="risk" />
        <MetricCard icon={TrendingUp} label="候选动作" value={summary.candidates} tone="positive" />
        <MetricCard icon={CheckCircle2} label="持仓覆盖" value={summary.holdings} />
      </div>

      {loading && rows.length === 0 ? (
        <div className="grid gap-3">
          {[0, 1, 2, 3].map((item) => (
            <div key={item} className="card p-4">
              <Skeleton className="h-5 w-40 mb-3" />
              <Skeleton className="h-4 w-full mb-2" />
              <Skeleton className="h-4 w-2/3" />
            </div>
          ))}
        </div>
      ) : rows.length === 0 ? (
        <div className="card p-8 text-center">
          <div className="mx-auto w-10 h-10 rounded-xl bg-accent flex items-center justify-center text-muted-foreground mb-3">
            <Eye className="w-5 h-5" />
          </div>
          <div className="font-semibold">暂无可评估标的</div>
          <div className="text-[12px] text-muted-foreground mt-1">先在持仓页添加自选股或持仓。</div>
        </div>
      ) : (
        <div className="grid gap-3">
          {rows.map((row) => (
            <DecisionCard key={row.key} row={row} />
          ))}
        </div>
      )}
    </div>
  )
}

function MetricCard({
  icon: Icon,
  label,
  value,
  tone = 'neutral',
}: {
  icon: typeof Activity
  label: string
  value: number
  tone?: 'neutral' | 'risk' | 'positive'
}) {
  const toneClass = tone === 'risk' ? 'text-red-500' : tone === 'positive' ? 'text-emerald-500' : 'text-primary'
  return (
    <div className="card p-4">
      <div className="flex items-center gap-2 text-muted-foreground mb-1">
        <Icon className={`w-4 h-4 ${toneClass}`} />
        <span className="text-[12px]">{label}</span>
      </div>
      <div className="text-[24px] font-bold font-mono">{value}</div>
    </div>
  )
}

function DecisionCard({ row }: { row: WatchRow }) {
  const Icon = getDecisionIcon(row.decision.label)
  const pct = toNumber(row.quote.change_pct)
  const price = row.quote.current_price
  const support = row.technical.support ?? row.technical.support_m ?? row.technical.support_s
  const resistance = row.technical.resistance ?? row.technical.resistance_m ?? row.technical.resistance_s

  return (
    <div className="card p-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold text-[16px]">{row.name || row.symbol}</span>
            <span className="font-mono text-[12px] text-muted-foreground">{row.symbol}</span>
            <Badge variant="secondary">{marketLabel(row.market)}</Badge>
            <Badge variant="outline">{row.source === 'position' ? row.accountName || '持仓' : '自选'}</Badge>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-3 text-[12px] text-muted-foreground">
            <span>现价 <b className="text-foreground font-mono">{formatPrice(price)}</b></span>
            <span className={pct != null && pct >= 0 ? 'text-rose-500' : 'text-emerald-500'}>{formatPct(pct)}</span>
            {row.costPrice != null && <span>成本 <b className="text-foreground font-mono">{formatPrice(row.costPrice)}</b></span>}
            {row.quantity != null && <span>数量 <b className="text-foreground font-mono">{row.quantity}</b></span>}
            <span>支撑 <b className="text-foreground font-mono">{formatPrice(support)}</b></span>
            <span>压力 <b className="text-foreground font-mono">{formatPrice(resistance)}</b></span>
          </div>
        </div>

        <div className="flex items-center gap-2 lg:justify-end">
          <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[12px] border ${labelTone[row.decision.label] || labelTone['观察']}`}>
            <Icon className="w-3.5 h-3.5" />
            {row.decision.label}
          </span>
          <span className="font-mono text-[20px] font-bold">{row.decision.score}</span>
        </div>
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-3">
        <InfoBlock title="触发原因" items={row.decision.reasons} />
        <InfoBlock title="当前风险" items={row.decision.risks.length ? row.decision.risks : ['暂无明显结构化风险']} mutedEmpty />
        <InfoBlock title="确认/失效条件" items={[...row.decision.confirm_conditions, ...row.decision.invalidation_conditions]} />
      </div>
    </div>
  )
}

function InfoBlock({ title, items, mutedEmpty = false }: { title: string; items: string[]; mutedEmpty?: boolean }) {
  return (
    <div className="rounded-lg border border-border/45 bg-accent/15 p-3">
      <div className="text-[12px] font-semibold mb-2">{title}</div>
      <div className="space-y-1.5">
        {items.slice(0, 4).map((item, index) => (
          <div key={`${item}-${index}`} className={`text-[12px] leading-5 ${mutedEmpty ? 'text-muted-foreground' : 'text-foreground'}`}>
            {item}
          </div>
        ))}
      </div>
    </div>
  )
}
