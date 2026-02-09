import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { ExternalLink, RefreshCw } from 'lucide-react'
import { fetchAPI } from '@/lib/utils'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { SuggestionBadge, type KlineSummary, type SuggestionInfo } from '@/components/suggestion-badge'
import { useToast } from '@/components/ui/toast'
import KlineModal from '@/components/KlineModal'
import { buildKlineSuggestion } from '@/lib/kline-scorer'

interface QuoteResponse {
  symbol: string
  market: string
  name: string | null
  current_price: number | null
  change_pct: number | null
  change_amount: number | null
  prev_close: number | null
  open_price: number | null
  high_price: number | null
  low_price: number | null
  volume: number | null
  turnover: number | null
  total_market_value?: number | null
  circulating_market_value?: number | null
}

interface KlineSummaryResponse {
  symbol: string
  market: string
  summary: KlineSummary
}

interface MiniKlineResponse {
  symbol: string
  market: string
  klines: Array<{
    date: string
    open: number
    close: number
    high: number
    low: number
    volume: number
  }>
}

interface NewsItem {
  source: string
  source_label: string
  title: string
  publish_time: string
  url: string
}

interface HistoryRecord {
  id: number
  agent_name: string
  stock_symbol: string
  analysis_date: string
  title: string
  content: string
  created_at: string
}

interface PortfolioPosition {
  symbol: string
  market: string
  quantity: number
  cost_price: number
  market_value_cny: number | null
  pnl: number | null
}

interface PortfolioSummaryResponse {
  accounts: Array<{
    positions: PortfolioPosition[]
  }>
}

const AGENT_LABELS: Record<string, string> = {
  daily_report: '盘后日报',
  premarket_outlook: '盘前分析',
  intraday_monitor: '盘中监测',
  news_digest: '新闻速递',
  chart_analyst: '技术分析',
}

type InsightTab = 'overview' | 'kline' | 'suggestions' | 'news' | 'history'
type ReplayEvent = {
  id: string
  ts: number
  timeText: string
  label: string
  desc: string
  tone: 'bull' | 'bear' | 'neutral'
  kind: 'technical' | 'suggestion' | 'history'
  isAlert: boolean
}

interface StockAgentInfo {
  agent_name: string
  schedule?: string
  ai_model_id?: number | null
  notify_channel_ids?: number[]
}

interface StockItem {
  id: number
  symbol: string
  name: string
  market: string
  enabled: boolean
  agents?: StockAgentInfo[]
}

function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value == null) return '--'
  return value.toFixed(digits)
}

function formatCompactNumber(value: number | null | undefined): string {
  if (value == null) return '--'
  const n = Number(value)
  if (!isFinite(n)) return '--'
  const abs = Math.abs(n)
  if (abs >= 1e8) return `${(n / 1e8).toFixed(2)}亿`
  if (abs >= 1e4) return `${(n / 1e4).toFixed(2)}万`
  return n.toFixed(0)
}

function formatTime(isoTime?: string): string {
  if (!isoTime) return ''
  const d = new Date(isoTime)
  if (isNaN(d.getTime())) return ''
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function parseToMs(input?: string): number | null {
  if (!input) return null
  const d = new Date(input)
  if (!isNaN(d.getTime())) return d.getTime()
  const m = input.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!m) return null
  const dt = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 0, 0, 0)
  return isNaN(dt.getTime()) ? null : dt.getTime()
}

function marketBadge(market: string) {
  if (market === 'HK') return { style: 'bg-orange-500/10 text-orange-600', label: '港股' }
  if (market === 'US') return { style: 'bg-green-500/10 text-green-600', label: '美股' }
  return { style: 'bg-blue-500/10 text-blue-600', label: 'A股' }
}

export default function StockInsightModal(props: {
  open: boolean
  onOpenChange: (open: boolean) => void
  symbol: string
  market: string
  stockName?: string
  hasPosition?: boolean
  onOpenFullDetail?: (market: string, symbol: string) => void
}) {
  const { toast } = useToast()
  const symbol = String(props.symbol || '').trim()
  const market = String(props.market || 'CN').trim().toUpperCase()
  const [loading, setLoading] = useState(false)
  const [tab, setTab] = useState<InsightTab>('overview')
  const [newsHours, setNewsHours] = useState('24')
  const [quote, setQuote] = useState<QuoteResponse | null>(null)
  const [klineSummary, setKlineSummary] = useState<KlineSummary | null>(null)
  const [miniKlines, setMiniKlines] = useState<MiniKlineResponse['klines']>([])
  const [miniKlineLoading, setMiniKlineLoading] = useState(false)
  const [miniHoverIdx, setMiniHoverIdx] = useState<number | null>(null)
  const [suggestions, setSuggestions] = useState<SuggestionInfo[]>([])
  const [news, setNews] = useState<NewsItem[]>([])
  const [history, setHistory] = useState<HistoryRecord[]>([])
  const [detailRecord, setDetailRecord] = useState<HistoryRecord | null>(null)
  const [klineOpen, setKlineOpen] = useState(false)
  const [klineInterval, setKlineInterval] = useState<'1d' | '1w' | '1m'>('1d')
  const [klineDays, setKlineDays] = useState<'60' | '120' | '250'>('120')
  const [replayFilter, setReplayFilter] = useState<'all' | 'trade' | 'alert'>('all')
  const [alerting, setAlerting] = useState(false)
  const [autoSuggesting, setAutoSuggesting] = useState(false)
  const [holdingAgg, setHoldingAgg] = useState<{
    quantity: number
    cost: number
    marketValue: number
    pnl: number
  } | null>(null)
  const autoTriggeredRef = useRef<Record<string, number>>({})
  const stockCacheRef = useRef<Record<string, StockItem>>({})

  const loadQuote = useCallback(async () => {
    if (!symbol) return
    const data = await fetchAPI<QuoteResponse>(`/quotes/${encodeURIComponent(symbol)}?market=${encodeURIComponent(market)}`)
    setQuote(data || null)
  }, [symbol, market])

  const loadKline = useCallback(async () => {
    if (!symbol) return
    const data = await fetchAPI<KlineSummaryResponse>(`/klines/${encodeURIComponent(symbol)}/summary?market=${encodeURIComponent(market)}`)
    setKlineSummary(data?.summary || null)
  }, [symbol, market])

  const loadMiniKline = useCallback(async () => {
    if (!symbol) return
    setMiniKlineLoading(true)
    try {
      const data = await fetchAPI<MiniKlineResponse>(
        `/klines/${encodeURIComponent(symbol)}?market=${encodeURIComponent(market)}&days=36&interval=1d`
      )
      setMiniKlines((data?.klines || []).slice(-30))
    } catch {
      setMiniKlines([])
    } finally {
      setMiniKlineLoading(false)
    }
  }, [symbol, market])

  const loadSuggestions = useCallback(async () => {
    if (!symbol) return
    const params = new URLSearchParams()
    params.set('limit', '20')
    const data = await fetchAPI<any[]>(`/suggestions/${encodeURIComponent(symbol)}?${params.toString()}`)
    const list = (data || []).map(item => ({
      id: item.id,
      action: item.action,
      action_label: item.action_label,
      signal: item.signal || '',
      reason: item.reason || '',
      should_alert: !!item.should_alert,
      agent_name: item.agent_name,
      agent_label: item.agent_label,
      created_at: item.created_at,
      is_expired: item.is_expired,
      prompt_context: item.prompt_context,
      ai_response: item.ai_response,
      raw: item.raw || '',
      meta: item.meta,
    })) as SuggestionInfo[]
    setSuggestions(list)
  }, [symbol])

  const loadNews = useCallback(async () => {
    if (!symbol) return
    const params = new URLSearchParams()
    params.set('hours', newsHours)
    params.set('limit', '40')
    params.set('filter_related', 'true')
    params.set('symbols', symbol)
    const data = await fetchAPI<NewsItem[]>(`/news?${params.toString()}`)
    setNews(data || [])
  }, [symbol, newsHours])

  const loadHistory = useCallback(async () => {
    if (!symbol) return
    const params = new URLSearchParams()
    params.set('stock_symbol', symbol)
    params.set('limit', '30')
    const data = await fetchAPI<HistoryRecord[]>(`/history?${params.toString()}`)
    setHistory(data || [])
  }, [symbol])

  const loadHoldingAgg = useCallback(async () => {
    if (!symbol) return
    try {
      const data = await fetchAPI<PortfolioSummaryResponse>('/portfolio/summary?include_quotes=true')
      let quantity = 0
      let cost = 0
      let marketValue = 0
      let pnl = 0
      for (const acc of data?.accounts || []) {
        for (const p of acc.positions || []) {
          if (p.symbol !== symbol || p.market !== market) continue
          quantity += Number(p.quantity || 0)
          cost += Number(p.cost_price || 0) * Number(p.quantity || 0)
          marketValue += Number(p.market_value_cny || 0)
          pnl += Number(p.pnl || 0)
        }
      }
      if (quantity > 0) setHoldingAgg({ quantity, cost, marketValue, pnl })
      else setHoldingAgg(null)
    } catch {
      setHoldingAgg(null)
    }
  }, [symbol, market])

  const loadAll = useCallback(async () => {
    if (!symbol) return
    setLoading(true)
    try {
      await Promise.allSettled([loadQuote(), loadKline(), loadMiniKline(), loadSuggestions(), loadNews(), loadHistory(), loadHoldingAgg()])
    } catch (e) {
      toast(e instanceof Error ? e.message : '加载失败', 'error')
    } finally {
      setLoading(false)
    }
  }, [symbol, loadQuote, loadKline, loadMiniKline, loadSuggestions, loadNews, loadHistory, loadHoldingAgg, toast])

  useEffect(() => {
    if (!props.open || !symbol) return
    setTab('overview')
    setSuggestions([])
    setMiniKlines([])
    loadAll()
  }, [props.open, symbol, market, loadAll])

  useEffect(() => {
    if (!props.open || !symbol) return
    loadNews().catch(() => setNews([]))
  }, [props.open, symbol, newsHours, loadNews])

  const resolvedName = useMemo(() => props.stockName || quote?.name || symbol, [props.stockName, quote?.name, symbol])
  const latestSuggestion = suggestions.find(s => !s.is_expired) || suggestions[0] || null
  const hasHolding = !!props.hasPosition || !!holdingAgg
  const technicalFallbackSuggestion = useMemo<SuggestionInfo | null>(() => {
    if (!klineSummary) return null
    const scored = buildKlineSuggestion(klineSummary as any, hasHolding)
    const topEvidence = (scored.evidence || []).filter(e => e.delta !== 0).slice(0, 3).map(e => e.text)
    return {
      action: scored.action,
      action_label: scored.action_label,
      signal: scored.signal || '技术面中性',
      reason: topEvidence.length > 0 ? topEvidence.join('；') : '基于K线技术指标自动生成的基础建议',
      should_alert: scored.action === 'buy' || scored.action === 'add' || scored.action === 'sell' || scored.action === 'reduce',
      agent_name: 'technical_fallback',
      agent_label: '技术指标',
      created_at: new Date().toISOString(),
      is_expired: false,
      meta: {
        fallback: true,
        score: scored.score,
        evidence_count: scored.evidence?.length || 0,
      },
    }
  }, [klineSummary, hasHolding])
  const displaySuggestion = latestSuggestion || technicalFallbackSuggestion
  const quoteUp = (quote?.change_pct || 0) > 0
  const quoteDown = (quote?.change_pct || 0) < 0
  const changeColor = quoteUp ? 'text-rose-500' : quoteDown ? 'text-emerald-500' : 'text-muted-foreground'
  const badge = marketBadge(market)
  const amplitudePct = useMemo(() => {
    const hi = quote?.high_price
    const lo = quote?.low_price
    const pre = quote?.prev_close
    if (hi == null || lo == null || pre == null || pre === 0) return null
    return ((hi - lo) / pre) * 100
  }, [quote?.high_price, quote?.low_price, quote?.prev_close])
  const keyLevels = useMemo(() => {
    if (!klineSummary) return null
    const last = Number((klineSummary as any).last_close)
    const support = klineSummary.support
    const resistance = klineSummary.resistance
    const distPct = (anchor: number | null | undefined) => {
      if (anchor == null || !isFinite(last) || !last) return null
      return ((anchor - last) / last) * 100
    }
    return {
      last: isFinite(last) ? last : null,
      support,
      resistance,
      toSupportPct: distPct(support),
      toResistancePct: distPct(resistance),
    }
  }, [klineSummary])

  const replayEvents = useMemo(() => {
    const out: ReplayEvent[] = []
    if ((klineSummary as any)?.computed_at) {
      const ms = parseToMs((klineSummary as any).computed_at)
      if (ms != null) {
        out.push({
          id: `tech-${ms}`,
          ts: ms,
          timeText: formatTime((klineSummary as any).computed_at),
          label: '技术面快照',
          desc: `${klineSummary?.trend || ''} ${klineSummary?.macd_status || ''}`.trim() || 'K线数据更新',
          tone: 'neutral',
          kind: 'technical',
          isAlert: false,
        })
      }
    }
    for (const s of suggestions.slice(0, 12)) {
      const ms = parseToMs(s.created_at)
      if (ms == null) continue
      const action = s.action_label || s.action || '建议'
      const desc = s.signal || s.reason || '无附加说明'
      const tone: ReplayEvent['tone'] = ['buy', 'add'].includes(s.action) ? 'bull' : ['sell', 'reduce', 'avoid'].includes(s.action) ? 'bear' : 'neutral'
      out.push({
        id: `s-${ms}-${action}`,
        ts: ms,
        timeText: formatTime(s.created_at),
        label: action,
        desc,
        tone,
        kind: 'suggestion',
        isAlert: !!s.should_alert || ['sell', 'reduce', 'avoid', 'alert'].includes(s.action),
      })
    }
    for (const h of history.slice(0, 12)) {
      const ms = parseToMs(h.created_at || h.analysis_date)
      if (ms == null) continue
      out.push({
        id: `h-${h.id}`,
        ts: ms,
        timeText: formatTime(h.created_at || h.analysis_date),
        label: AGENT_LABELS[h.agent_name] || h.agent_name,
        desc: h.title || '历史分析',
        tone: 'neutral',
        kind: 'history',
        isAlert: false,
      })
    }
    return out.sort((a, b) => b.ts - a.ts).slice(0, 8)
  }, [klineSummary, suggestions, history])

  const filteredReplayEvents = useMemo(() => {
    if (replayFilter === 'all') return replayEvents
    if (replayFilter === 'trade') return replayEvents.filter(evt => evt.kind === 'suggestion' && (evt.tone === 'bull' || evt.tone === 'bear'))
    return replayEvents.filter(evt => evt.isAlert)
  }, [replayEvents, replayFilter])

  const handleSetAlert = async () => {
    if (!symbol) return
    setAlerting(true)
    try {
      const stocks = await fetchAPI<StockItem[]>('/stocks')
      let stock = (stocks || []).find(s => s.symbol === symbol && s.market === market) || null
      if (!stock) {
        stock = await fetchAPI<StockItem>('/stocks', {
          method: 'POST',
          body: JSON.stringify({ symbol, name: resolvedName || symbol, market }),
        })
      }

      const existingAgents = (stock.agents || []).map(a => ({
        agent_name: a.agent_name,
        schedule: a.schedule || '',
        ai_model_id: a.ai_model_id ?? null,
        notify_channel_ids: a.notify_channel_ids || [],
      }))
      const hasIntraday = existingAgents.some(a => a.agent_name === 'intraday_monitor')
      const nextAgents = hasIntraday
        ? existingAgents
        : [...existingAgents, { agent_name: 'intraday_monitor', schedule: '', ai_model_id: null, notify_channel_ids: [] }]

      await fetchAPI(`/stocks/${stock.id}/agents`, {
        method: 'PUT',
        body: JSON.stringify({ agents: nextAgents }),
      })
      await fetchAPI(`/stocks/${stock.id}/agents/intraday_monitor/trigger?bypass_throttle=true`, { method: 'POST' })
      toast('已设置提醒并触发一次盘中监测', 'success')
      await Promise.allSettled([loadSuggestions(), loadHistory()])
    } catch (e) {
      toast(e instanceof Error ? e.message : '设置提醒失败', 'error')
    } finally {
      setAlerting(false)
    }
  }

  const ensureStockAndAgent = useCallback(async (
    agentName: 'intraday_monitor' | 'chart_analyst'
  ): Promise<StockItem | null> => {
    const key = `${market}:${symbol}`
    let stock: StockItem | null = stockCacheRef.current[key] ?? null

    if (!stock) {
      const stocks = await fetchAPI<StockItem[]>('/stocks')
      stock = (stocks || []).find(s => s.symbol === symbol && s.market === market) || null
    }
    if (!stock) {
      stock = await fetchAPI<StockItem>('/stocks', {
        method: 'POST',
        body: JSON.stringify({ symbol, name: resolvedName || symbol, market }),
      })
    }
    if (!stock) return null

    const existingAgents = (stock.agents || []).map(a => ({
      agent_name: a.agent_name,
      schedule: a.schedule || '',
      ai_model_id: a.ai_model_id ?? null,
      notify_channel_ids: a.notify_channel_ids || [],
    }))
    const hasAgent = existingAgents.some(a => a.agent_name === agentName)
    if (!hasAgent) {
      const nextAgents = [...existingAgents, { agent_name: agentName, schedule: '', ai_model_id: null, notify_channel_ids: [] }]
      stock = await fetchAPI<StockItem>(`/stocks/${stock.id}/agents`, {
        method: 'PUT',
        body: JSON.stringify({ agents: nextAgents }),
      })
    }

    stockCacheRef.current[key] = stock
    return stock
  }, [market, symbol, resolvedName])

  const triggerAutoAiSuggestion = useCallback(async () => {
    if (!symbol || !market || hasHolding || suggestions.length > 0 || autoSuggesting) return
    const key = `${market}:${symbol}`
    const lastTs = autoTriggeredRef.current[key] || 0
    if (Date.now() - lastTs < 5 * 60 * 1000) return
    autoTriggeredRef.current[key] = Date.now()
    setAutoSuggesting(true)
    try {
      const stock = await ensureStockAndAgent('intraday_monitor')
      if (!stock) return
      // intraday_monitor 较 chart_analyst 更轻量、稳定，不依赖截图链路
      await fetchAPI(`/stocks/${stock.id}/agents/intraday_monitor/trigger?bypass_throttle=true`, { method: 'POST' })
      await Promise.allSettled([loadSuggestions(), loadHistory()])
    } catch {
      // 自动触发失败时静默降级到技术指标建议，不打断用户
    } finally {
      setAutoSuggesting(false)
    }
  }, [symbol, market, hasHolding, suggestions.length, autoSuggesting, ensureStockAndAgent, loadSuggestions, loadHistory])

  useEffect(() => {
    if (!props.open || !symbol) return
    const timer = setTimeout(() => {
      triggerAutoAiSuggestion().catch(() => undefined)
    }, 700)
    return () => clearTimeout(timer)
  }, [props.open, symbol, market, triggerAutoAiSuggestion])

  const miniKlineExtrema = useMemo(() => {
    if (!miniKlines.length) return null
    let low = Number.POSITIVE_INFINITY
    let high = Number.NEGATIVE_INFINITY
    for (const k of miniKlines) {
      low = Math.min(low, Number(k.low))
      high = Math.max(high, Number(k.high))
    }
    if (!isFinite(low) || !isFinite(high) || high <= low) return null
    return { low, high }
  }, [miniKlines])

  const miniDisplayCandle = useMemo(() => {
    if (!miniKlines.length) return null
    const idx = miniHoverIdx == null ? miniKlines.length - 1 : Math.max(0, Math.min(miniHoverIdx, miniKlines.length - 1))
    const cur = miniKlines[idx]
    const prev = idx > 0 ? miniKlines[idx - 1] : null
    const changePct = prev && prev.close ? ((cur.close - prev.close) / prev.close) * 100 : null
    return { ...cur, changePct, idx }
  }, [miniKlines, miniHoverIdx])

  return (
    <>
      <Dialog open={props.open} onOpenChange={props.onOpenChange}>
        <DialogContent className="w-[92vw] max-w-6xl p-5 md:p-6">
          <DialogHeader className="mb-3">
            <div className="flex items-start justify-between gap-3 pr-8">
              <div>
                <DialogTitle className="flex items-center gap-2">
                  <span className={`text-[10px] px-2 py-0.5 rounded ${badge.style}`}>{badge.label}</span>
                  <span>{resolvedName}</span>
                  <span className="font-mono text-[12px] text-muted-foreground">({symbol})</span>
                </DialogTitle>
                <DialogDescription>概览、K线、AI建议、新闻、历史分析都在同一弹窗查看</DialogDescription>
              </div>
              <div className="flex items-center gap-2">
                <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={handleSetAlert} disabled={alerting}>
                  {alerting ? '设置中...' : '一键设提醒'}
                </Button>
                <Button variant="outline" size="sm" className="h-8 px-2.5" onClick={() => loadAll()} disabled={loading}>
                  <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
                </Button>
                {props.onOpenFullDetail && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8 px-2.5 gap-1.5"
                    onClick={() => props.onOpenFullDetail?.(market, symbol)}
                  >
                    完整页 <ExternalLink className="w-3.5 h-3.5" />
                  </Button>
                )}
              </div>
            </div>
          </DialogHeader>

          <div className="flex items-center gap-1 flex-wrap mb-3">
            {[
              { id: 'overview', label: '概览' },
              { id: 'kline', label: 'K线' },
              { id: 'suggestions', label: `建议 (${suggestions.length})` },
              { id: 'news', label: `新闻 (${news.length})` },
              { id: 'history', label: `历史 (${history.length})` },
            ].map(item => (
              <button
                key={item.id}
                onClick={() => setTab(item.id as InsightTab)}
                className={`text-[11px] px-2.5 py-1 rounded transition-colors ${
                  tab === item.id ? 'bg-primary text-primary-foreground' : 'bg-accent/50 text-muted-foreground hover:bg-accent'
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>

          <div className="max-h-[68vh] overflow-y-auto pr-1 scrollbar">
            {tab === 'overview' && (
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
                <div className="card p-4 lg:col-span-1">
                  <div className="text-[12px] text-muted-foreground mb-1">实时行情</div>
                  <div className="flex items-end justify-between gap-3">
                    <div className="text-[28px] font-bold font-mono text-foreground">
                      {quote?.current_price != null ? formatNumber(quote.current_price) : '--'}
                    </div>
                    <div className={`text-[14px] font-mono ${changeColor}`}>
                      {quote?.change_pct != null ? `${quote.change_pct >= 0 ? '+' : ''}${quote.change_pct.toFixed(2)}%` : '--'}
                    </div>
                  </div>
                  <div className="mt-3 space-y-1.5 text-[12px]">
                    <div className="flex items-center justify-between"><span className="text-muted-foreground">今开</span><span className="font-mono">{formatNumber(quote?.open_price)}</span></div>
                    <div className="flex items-center justify-between"><span className="text-muted-foreground">最高</span><span className="font-mono">{formatNumber(quote?.high_price)}</span></div>
                    <div className="flex items-center justify-between"><span className="text-muted-foreground">最低</span><span className="font-mono">{formatNumber(quote?.low_price)}</span></div>
                    <div className="flex items-center justify-between"><span className="text-muted-foreground">成交量</span><span className="font-mono">{formatCompactNumber(quote?.volume)}</span></div>
                    <div className="flex items-center justify-between"><span className="text-muted-foreground">成交额</span><span className="font-mono">{formatCompactNumber(quote?.turnover)}</span></div>
                    <div className="flex items-center justify-between"><span className="text-muted-foreground">昨收</span><span className="font-mono">{formatNumber(quote?.prev_close)}</span></div>
                    <div className="flex items-center justify-between"><span className="text-muted-foreground">涨跌额</span><span className={`font-mono ${changeColor}`}>{formatNumber(quote?.change_amount)}</span></div>
                    <div className="flex items-center justify-between"><span className="text-muted-foreground">振幅</span><span className="font-mono">{amplitudePct != null ? `${amplitudePct.toFixed(2)}%` : '--'}</span></div>
                    <div className="flex items-center justify-between"><span className="text-muted-foreground">总市值</span><span className="font-mono">{formatCompactNumber((quote as any)?.total_market_value)}</span></div>
                    <div className="flex items-center justify-between"><span className="text-muted-foreground">流通市值</span><span className="font-mono">{formatCompactNumber((quote as any)?.circulating_market_value)}</span></div>
                  </div>
                  {holdingAgg && (
                    <div className="mt-3 rounded-lg border border-border/40 bg-accent/20 p-2.5 space-y-1.5 text-[12px]">
                      <div className="text-[11px] text-muted-foreground">我的持仓</div>
                      <div className="flex items-center justify-between"><span className="text-muted-foreground">持仓数量</span><span className="font-mono">{holdingAgg.quantity}</span></div>
                      <div className="flex items-center justify-between"><span className="text-muted-foreground">持仓成本</span><span className="font-mono">{formatCompactNumber(holdingAgg.cost)}</span></div>
                      <div className="flex items-center justify-between"><span className="text-muted-foreground">持仓市值</span><span className="font-mono">{formatCompactNumber(holdingAgg.marketValue)}</span></div>
                      <div className="flex items-center justify-between"><span className="text-muted-foreground">浮动盈亏</span><span className={`font-mono ${holdingAgg.pnl >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>{holdingAgg.pnl >= 0 ? '+' : ''}{formatCompactNumber(holdingAgg.pnl)}</span></div>
                    </div>
                  )}
                  {!holdingAgg && (
                    <div className="mt-3 text-[11px] text-muted-foreground">未在持仓中</div>
                  )}
                </div>

                <div className="card p-4 lg:col-span-1">
                  <div className="text-[12px] text-muted-foreground mb-2">技术面快照</div>
                  {!klineSummary ? (
                    <div className="text-[12px] text-muted-foreground py-4">暂无K线摘要</div>
                  ) : (
                    <div className="space-y-3">
                      <div className="rounded-lg border border-border/40 bg-accent/10 p-2.5">
                        <div className="flex items-center justify-between gap-2 mb-1">
                          <div className="text-[10px] text-muted-foreground">迷你K线（近30日）</div>
                          {miniDisplayCandle && (
                            <div className="text-[10px] text-muted-foreground font-mono">
                              {miniDisplayCandle.date} · O {miniDisplayCandle.open.toFixed(2)} H {miniDisplayCandle.high.toFixed(2)} L {miniDisplayCandle.low.toFixed(2)} C {miniDisplayCandle.close.toFixed(2)}
                              {miniDisplayCandle.changePct != null && (
                                <span className={`ml-1 ${miniDisplayCandle.changePct >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                                  ({miniDisplayCandle.changePct >= 0 ? '+' : ''}{miniDisplayCandle.changePct.toFixed(2)}%)
                                </span>
                              )}
                            </div>
                          )}
                        </div>
                        {miniKlineLoading ? (
                          <div className="h-20 rounded bg-accent/30 animate-pulse" />
                        ) : miniKlines.length > 0 && miniKlineExtrema ? (
                          <svg
                            viewBox="0 0 300 80"
                            className="w-full h-20 cursor-pointer"
                            onClick={() => setTab('kline')}
                            onMouseLeave={() => setMiniHoverIdx(null)}
                            onMouseMove={(e) => {
                              const rect = e.currentTarget.getBoundingClientRect()
                              const x = e.clientX - rect.left
                              const ratio = rect.width > 0 ? x / rect.width : 0
                              const idx = Math.floor(ratio * miniKlines.length)
                              setMiniHoverIdx(Math.max(0, Math.min(miniKlines.length - 1, idx)))
                            }}
                          >
                            <title>点击进入 K 线详情</title>
                            {miniKlines.map((k, idx) => {
                              const xStep = 300 / miniKlines.length
                              const x = xStep * idx + xStep / 2
                              const bodyW = Math.max(2, xStep * 0.5)
                              const toY = (v: number) => 74 - ((v - miniKlineExtrema.low) / (miniKlineExtrema.high - miniKlineExtrema.low)) * 68
                              const yOpen = toY(Number(k.open))
                              const yClose = toY(Number(k.close))
                              const yHigh = toY(Number(k.high))
                              const yLow = toY(Number(k.low))
                              const up = Number(k.close) >= Number(k.open)
                              const color = up ? '#ef4444' : '#10b981'
                              const bodyTop = Math.min(yOpen, yClose)
                              const bodyH = Math.max(1.4, Math.abs(yOpen - yClose))
                              const active = miniHoverIdx === idx
                              return (
                                <g key={`${k.date}-${idx}`}>
                                  {active && (
                                    <rect x={x - xStep / 2} y={4} width={xStep} height={72} fill="rgba(59,130,246,0.10)" />
                                  )}
                                  <line x1={x} y1={yHigh} x2={x} y2={yLow} stroke={color} strokeWidth="1" />
                                  <rect x={x - bodyW / 2} y={bodyTop} width={bodyW} height={bodyH} fill={color} rx="0.6" />
                                </g>
                              )
                            })}
                          </svg>
                        ) : (
                          <div className="h-20 text-[11px] text-muted-foreground flex items-center justify-center">暂无迷你K线</div>
                        )}
                      </div>
                      <div className="flex flex-wrap gap-2 text-[11px]">
                        {klineSummary.trend && <span className="px-2 py-0.5 rounded bg-accent/50">{klineSummary.trend}</span>}
                        {klineSummary.macd_status && <span className="px-2 py-0.5 rounded bg-accent/50">MACD {klineSummary.macd_status}</span>}
                        {klineSummary.rsi_status && <span className="px-2 py-0.5 rounded bg-accent/50">RSI {klineSummary.rsi_status}</span>}
                        {klineSummary.kline_pattern && <span className="px-2 py-0.5 rounded bg-amber-500/10 text-amber-600">{klineSummary.kline_pattern}</span>}
                      </div>
                      <div className="grid grid-cols-2 gap-2 text-[12px]">
                        <div className="flex items-center justify-between"><span className="text-muted-foreground">支撑</span><span className="font-mono text-emerald-600">{formatNumber(klineSummary.support)}</span></div>
                        <div className="flex items-center justify-between"><span className="text-muted-foreground">压力</span><span className="font-mono text-rose-600">{formatNumber(klineSummary.resistance)}</span></div>
                      </div>
                      <Button variant="secondary" size="sm" className="h-8" onClick={() => setKlineOpen(true)}>
                        打开交互K线
                      </Button>
                    </div>
                  )}
                </div>

                <div className="card p-4 lg:col-span-1">
                  <div className="text-[12px] text-muted-foreground mb-2">最新建议</div>
                  {displaySuggestion ? (
                    <SuggestionBadge
                      suggestion={displaySuggestion}
                      stockName={resolvedName}
                      stockSymbol={symbol}
                      kline={klineSummary}
                      hasPosition={!!props.hasPosition}
                    />
                  ) : (
                    <div className="text-[12px] text-muted-foreground py-4">暂无建议</div>
                  )}
                  {!latestSuggestion && technicalFallbackSuggestion && (
                    <div className="mt-2 text-[10px] text-muted-foreground">当前为技术指标基础建议，AI 建议生成后将自动更新</div>
                  )}
                  {autoSuggesting && (
                    <div className="mt-2 text-[10px] text-primary">正在自动生成 AI 建议（通常 5-15 秒）...</div>
                  )}
                </div>
              </div>
            )}

            {tab === 'kline' && (
              <div className="card p-4">
                {!klineSummary ? (
                  <div className="text-[12px] text-muted-foreground py-8 text-center">暂无K线数据</div>
                ) : (
                  <div className="space-y-3">
                    <div className="text-[12px] text-muted-foreground">
                      数据周期 {klineSummary.timeframe || '1d'}{klineSummary.asof ? ` · 截至 ${klineSummary.asof}` : ''}
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <div className="text-[11px] text-muted-foreground mr-1">周期</div>
                      {[
                        { key: '1d', label: '日K' },
                        { key: '1w', label: '周K' },
                        { key: '1m', label: '月K' },
                      ].map(item => (
                        <button
                          key={item.key}
                          onClick={() => setKlineInterval(item.key as '1d' | '1w' | '1m')}
                          className={`text-[11px] px-2 py-1 rounded ${klineInterval === item.key ? 'bg-primary text-primary-foreground' : 'bg-accent/50 text-muted-foreground hover:bg-accent'}`}
                        >
                          {item.label}
                        </button>
                      ))}
                      <div className="text-[11px] text-muted-foreground ml-3 mr-1">范围</div>
                      {[
                        { key: '60', label: '60' },
                        { key: '120', label: '120' },
                        { key: '250', label: '250' },
                      ].map(item => (
                        <button
                          key={item.key}
                          onClick={() => setKlineDays(item.key as '60' | '120' | '250')}
                          className={`text-[11px] px-2 py-1 rounded ${klineDays === item.key ? 'bg-primary text-primary-foreground' : 'bg-accent/50 text-muted-foreground hover:bg-accent'}`}
                        >
                          {item.label}天
                        </button>
                      ))}
                    </div>
                    <div className="flex flex-wrap gap-2 text-[11px]">
                      {klineSummary.trend && <Badge variant="outline">{klineSummary.trend}</Badge>}
                      {klineSummary.macd_status && <Badge variant="outline">MACD {klineSummary.macd_status}</Badge>}
                      {klineSummary.rsi_status && <Badge variant="outline">RSI {klineSummary.rsi_status}</Badge>}
                      {klineSummary.volume_trend && <Badge variant="outline">{klineSummary.volume_trend}</Badge>}
                    </div>
                    {keyLevels && (keyLevels.support != null || keyLevels.resistance != null) && (
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[12px]">
                        {keyLevels.support != null && (
                          <div className="rounded-lg bg-emerald-500/10 p-2">
                            <div className="text-muted-foreground">支撑</div>
                            <div className="font-mono text-emerald-700">
                              {formatNumber(keyLevels.support)}
                              {keyLevels.toSupportPct != null && (
                                <span className="ml-1 text-[11px] text-muted-foreground">
                                  ({keyLevels.toSupportPct >= 0 ? '+' : ''}{keyLevels.toSupportPct.toFixed(1)}%)
                                </span>
                              )}
                            </div>
                          </div>
                        )}
                        {keyLevels.resistance != null && (
                          <div className="rounded-lg bg-rose-500/10 p-2">
                            <div className="text-muted-foreground">压力</div>
                            <div className="font-mono text-rose-700">
                              {formatNumber(keyLevels.resistance)}
                              {keyLevels.toResistancePct != null && (
                                <span className="ml-1 text-[11px] text-muted-foreground">
                                  ({keyLevels.toResistancePct >= 0 ? '+' : ''}{keyLevels.toResistancePct.toFixed(1)}%)
                                </span>
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[12px]">
                      <div className="rounded-lg bg-accent/30 p-2"><div className="text-muted-foreground">MA5</div><div className="font-mono">{formatNumber(klineSummary.ma5)}</div></div>
                      <div className="rounded-lg bg-accent/30 p-2"><div className="text-muted-foreground">MA20</div><div className="font-mono">{formatNumber(klineSummary.ma20)}</div></div>
                      <div className="rounded-lg bg-accent/30 p-2"><div className="text-muted-foreground">量比</div><div className="font-mono">{formatNumber(klineSummary.volume_ratio)}</div></div>
                      <div className="rounded-lg bg-accent/30 p-2"><div className="text-muted-foreground">振幅</div><div className="font-mono">{formatNumber(klineSummary.amplitude)}%</div></div>
                    </div>
                    <div className="rounded-lg bg-accent/20 p-3">
                      <div className="flex items-center justify-between gap-2 mb-2">
                        <div className="text-[11px] font-medium text-muted-foreground">最近信号回放</div>
                        <div className="flex items-center gap-1">
                          {[
                            { key: 'all', label: '全部' },
                            { key: 'trade', label: '交易动作' },
                            { key: 'alert', label: '预警' },
                          ].map(item => (
                            <button
                              key={item.key}
                              onClick={() => setReplayFilter(item.key as 'all' | 'trade' | 'alert')}
                              className={`text-[10px] px-2 py-0.5 rounded ${
                                replayFilter === item.key ? 'bg-primary text-primary-foreground' : 'bg-accent/60 text-muted-foreground hover:bg-accent'
                              }`}
                            >
                              {item.label}
                            </button>
                          ))}
                        </div>
                      </div>
                      {filteredReplayEvents.length === 0 ? (
                        <div className="text-[11px] text-muted-foreground">暂无回放数据</div>
                      ) : (
                        <div className="space-y-1.5">
                          {filteredReplayEvents.map(evt => (
                            <div key={evt.id} className="flex items-start justify-between gap-3 text-[12px]">
                              <div className="min-w-0">
                                <div className="text-foreground">
                                  <span className={`mr-1.5 px-1.5 py-0.5 rounded text-[10px] ${
                                    evt.tone === 'bull'
                                      ? 'bg-emerald-500/10 text-emerald-700'
                                      : evt.tone === 'bear'
                                        ? 'bg-rose-500/10 text-rose-700'
                                        : 'bg-slate-500/10 text-slate-700'
                                  }`}>{evt.label}</span>
                                  <span className="line-clamp-1">{evt.desc}</span>
                                </div>
                              </div>
                              <span className="shrink-0 text-[10px] text-muted-foreground">{evt.timeText}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                    <Button variant="secondary" size="sm" className="h-8" onClick={() => setKlineOpen(true)}>
                      查看交互K线
                    </Button>
                  </div>
                )}
              </div>
            )}

            {tab === 'suggestions' && (
              <div className="space-y-3">
                {suggestions.length === 0 ? (
                  technicalFallbackSuggestion ? (
                    <div className="card p-4">
                      <SuggestionBadge suggestion={technicalFallbackSuggestion} stockName={resolvedName} stockSymbol={symbol} kline={klineSummary} hasPosition={!!props.hasPosition} />
                      <div className="mt-2 text-[10px] text-muted-foreground">
                        {autoSuggesting ? '正在自动生成 AI 建议（通常 5-15 秒）...' : '当前显示技术指标基础建议'}
                      </div>
                    </div>
                  ) : (
                    <div className="card p-6 text-[12px] text-muted-foreground text-center">
                      {autoSuggesting ? '正在自动生成 AI 建议（通常 5-15 秒）...' : '暂无建议'}
                    </div>
                  )
                ) : (
                  suggestions.map((item, idx) => (
                    <div key={`${item.created_at || 's'}-${idx}`} className="card p-4">
                      <SuggestionBadge suggestion={item} stockName={resolvedName} stockSymbol={symbol} kline={klineSummary} hasPosition={!!props.hasPosition} />
                    </div>
                  ))
                )}
              </div>
            )}

            {tab === 'news' && (
              <div className="space-y-3">
                <div className="flex items-center justify-end">
                  <Select value={newsHours} onValueChange={setNewsHours}>
                    <SelectTrigger className="h-8 w-[110px] text-[12px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="6">近6小时</SelectItem>
                      <SelectItem value="12">近12小时</SelectItem>
                      <SelectItem value="24">近24小时</SelectItem>
                      <SelectItem value="48">近48小时</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {news.length === 0 ? (
                  <div className="card p-6 text-[12px] text-muted-foreground text-center">暂无相关新闻</div>
                ) : (
                  news.map((item, idx) => (
                    <a
                      key={`${item.publish_time || 'n'}-${idx}`}
                      href={item.url}
                      target="_blank"
                      rel="noreferrer"
                      className="card block p-4 hover:bg-accent/20 transition-colors"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="text-[13px] font-medium text-foreground line-clamp-2">{item.title}</div>
                        <ExternalLink className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                      </div>
                      <div className="mt-2 text-[11px] text-muted-foreground">{item.source_label || item.source} · {formatTime(item.publish_time)}</div>
                    </a>
                  ))
                )}
              </div>
            )}

            {tab === 'history' && (
              <div className="space-y-3">
                {history.length === 0 ? (
                  <div className="card p-6 text-[12px] text-muted-foreground text-center">暂无历史分析</div>
                ) : (
                  history.map(item => (
                    <div key={item.id} className="card p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <div className="text-[13px] font-medium text-foreground">{item.title || AGENT_LABELS[item.agent_name] || item.agent_name}</div>
                          <div className="mt-1 text-[11px] text-muted-foreground">
                            {AGENT_LABELS[item.agent_name] || item.agent_name} · {item.analysis_date} {item.created_at ? `· ${formatTime(item.created_at)}` : ''}
                          </div>
                        </div>
                        <Button variant="ghost" size="sm" className="h-7 px-2" onClick={() => setDetailRecord(item)}>
                          查看
                        </Button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>

      <KlineModal
        open={klineOpen}
        onOpenChange={setKlineOpen}
        symbol={symbol}
        market={market}
        title={resolvedName ? `K线：${resolvedName}` : `K线：${symbol}`}
        initialInterval={klineInterval}
        initialDays={klineDays}
      />

      <Dialog open={!!detailRecord} onOpenChange={open => !open && setDetailRecord(null)}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{detailRecord?.title || '分析详情'}</DialogTitle>
            <DialogDescription>
              {detailRecord ? `${AGENT_LABELS[detailRecord.agent_name] || detailRecord.agent_name} · ${detailRecord.analysis_date}` : ''}
            </DialogDescription>
          </DialogHeader>
          {detailRecord && (
            <div className="prose prose-sm dark:prose-invert max-w-none max-h-[60vh] overflow-y-auto">
              <ReactMarkdown>{detailRecord.content}</ReactMarkdown>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
