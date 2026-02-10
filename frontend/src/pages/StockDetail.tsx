import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import { ArrowLeft, BarChart3, ExternalLink, Newspaper, RefreshCw, Sparkles } from 'lucide-react'
import { fetchAPI } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select'
import { useToast } from '@/components/ui/toast'
import { SuggestionBadge, type SuggestionInfo } from '@/components/suggestion-badge'
import { KlineSummaryDialog } from '@/components/kline-summary-dialog'
import { buildKlineSuggestion } from '@/lib/kline-scorer'
import type { KlineSummaryData } from '@/components/kline-summary-dialog'
import KlineModal from '@/components/KlineModal'

interface Stock {
  id: number
  symbol: string
  name: string
  market: string
  enabled: boolean
  agents?: Array<{
    agent_name: string
    schedule?: string
    ai_model_id?: number | null
    notify_channel_ids?: number[]
  }>
}

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
}

interface KlineSummaryResponse {
  symbol: string
  market: string
  summary: KlineSummaryData
}

interface PortfolioPosition {
  id: number
  stock_id: number
  symbol: string
  name: string
  market: string
  cost_price: number
  quantity: number
  invested_amount: number | null
  trading_style: string | null
  current_price: number | null
  current_price_cny: number | null
  change_pct: number | null
  market_value: number | null
  market_value_cny: number | null
  pnl: number | null
  pnl_pct: number | null
  exchange_rate: number | null
  account_name: string
}

interface PortfolioSummaryResponse {
  accounts: Array<{
    id: number
    name: string
    available_funds: number
    total_market_value: number
    total_cost: number
    total_pnl: number
    total_pnl_pct: number
    total_assets: number
    positions: Array<Omit<PortfolioPosition, 'account_name'>>
  }>
}

interface NewsItem {
  source: string
  source_label: string
  external_id: string
  title: string
  content: string
  publish_time: string
  symbols: string[]
  importance: number
  url: string
}

interface HistoryRecord {
  id: number
  agent_name: string
  stock_symbol: string
  analysis_date: string
  title: string
  content: string
  suggestions?: Record<string, any> | null
  created_at: string
  updated_at: string
}

interface AgentResult {
  title: string
  content: string
  should_alert: boolean
  notified: boolean
}

const AGENT_LABELS: Record<string, string> = {
  daily_report: '盘后日报',
  premarket_outlook: '盘前分析',
  intraday_monitor: '盘中监测',
  news_digest: '新闻速递',
  chart_analyst: '技术分析',
}

const SOURCE_COLORS: Record<string, string> = {
  xueqiu: 'bg-amber-500/10 text-amber-600 border-amber-500/20',
  eastmoney_news: 'bg-cyan-500/10 text-cyan-600 border-cyan-500/20',
  eastmoney: 'bg-blue-500/10 text-blue-600 border-blue-500/20',
}

const TIME_OPTIONS = [
  { value: '6', label: '近 6 小时' },
  { value: '12', label: '近 12 小时' },
  { value: '24', label: '近 24 小时' },
  { value: '48', label: '近 48 小时' },
  { value: '72', label: '近 72 小时' },
]

function marketBadge(market: string) {
  if (market === 'HK') return { style: 'bg-orange-500/10 text-orange-600', label: '港股' }
  if (market === 'US') return { style: 'bg-green-500/10 text-green-600', label: '美股' }
  return { style: 'bg-blue-500/10 text-blue-600', label: 'A股' }
}

function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value == null) return '--'
  return value.toFixed(digits)
}

function formatCompactNumber(value: number | null | undefined): string {
  if (value == null) return '--'
  const n = Number(value)
  if (Number.isNaN(n)) return '--'
  const abs = Math.abs(n)
  if (abs >= 1e8) return `${(n / 1e8).toFixed(2)}亿`
  if (abs >= 1e4) return `${(n / 1e4).toFixed(2)}万`
  return n.toFixed(0)
}

function formatMoneyWan(value: number | null | undefined): string {
  if (value == null) return '--'
  const v = Number(value)
  if (Number.isNaN(v)) return '--'
  if (Math.abs(v) >= 10000) return `${(v / 10000).toFixed(2)}万`
  return v.toFixed(2)
}

function formatTime(isoTime: string): string {
  if (!isoTime) return ''
  try {
    const d = new Date(isoTime)
    if (isNaN(d.getTime())) return ''
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
  } catch {
    return ''
  }
}

function parseDateToMs(input?: string | null): number | null {
  if (!input) return null
  // ISO or RFC
  const direct = new Date(input)
  if (!isNaN(direct.getTime())) return direct.getTime()

  // "YYYY-MM-DD HH:mm[:ss]" (treat as local time)
  const m = input.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$/)
  if (m) {
    const y = Number(m[1])
    const mo = Number(m[2]) - 1
    const d = Number(m[3])
    const hh = Number(m[4])
    const mm = Number(m[5])
    const ss = Number(m[6] || '0')
    const dt = new Date(y, mo, d, hh, mm, ss)
    if (!isNaN(dt.getTime())) return dt.getTime()
  }

  // "YYYY-MM-DD" (local midnight)
  const d0 = input.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (d0) {
    const y = Number(d0[1])
    const mo = Number(d0[2]) - 1
    const d = Number(d0[3])
    const dt = new Date(y, mo, d, 0, 0, 0)
    if (!isNaN(dt.getTime())) return dt.getTime()
  }

  return null
}

export default function StockDetailPage() {
  const { toast } = useToast()
  const navigate = useNavigate()
  const params = useParams()

  const symbol = (params.symbol || '').trim()
  const market = (params.market || 'CN').trim().toUpperCase()

  const [loading, setLoading] = useState(false)
  const [triggering, setTriggering] = useState(false)

  const [stock, setStock] = useState<Stock | null>(null)
  const [quote, setQuote] = useState<QuoteResponse | null>(null)

  const [klineSummary, setKlineSummary] = useState<KlineSummaryData | null>(null)
  const [klineSummaryLoading, setKlineSummaryLoading] = useState(false)

  const [positions, setPositions] = useState<PortfolioPosition[]>([])
  const [, setPositionsLoading] = useState(false)

  const [includeExpired, setIncludeExpired] = useState(false)
  const [suggestions, setSuggestions] = useState<SuggestionInfo[]>([])

  const [newsHours, setNewsHours] = useState('72')
  const [news, setNews] = useState<NewsItem[]>([])

  const [history, setHistory] = useState<HistoryRecord[]>([])
  const [detailRecord, setDetailRecord] = useState<HistoryRecord | null>(null)

  const [batchReports, setBatchReports] = useState<Record<string, HistoryRecord | null>>({
    premarket_outlook: null,
    daily_report: null,
    news_digest: null,
  })

  // Kline dialog
  const [klineOpen, setKlineOpen] = useState(false)

  const [tab, setTab] = useState<'overview' | 'suggestions' | 'news' | 'history'>('overview')

  const resolvedName = useMemo(() => {
    if (stock?.name) return stock.name
    if (quote?.name) return quote.name
    return symbol
  }, [stock?.name, quote?.name, symbol])

  const loadStockBase = useCallback(async () => {
    if (!symbol) return
    try {
      const stocks = await fetchAPI<Stock[]>('/stocks')
      const found = stocks.find(s => s.symbol === symbol && s.market === market)
      setStock(found || null)
    } catch {
      setStock(null)
    }
  }, [symbol, market])

  const loadQuote = useCallback(async () => {
    if (!symbol) return
    try {
      const data = await fetchAPI<QuoteResponse>(`/quotes/${encodeURIComponent(symbol)}?market=${encodeURIComponent(market)}`)
      setQuote(data || null)
    } catch (e) {
      setQuote(null)
      toast(e instanceof Error ? e.message : '行情加载失败', 'error')
    }
  }, [symbol, market, toast])

  const loadKlineSummary = useCallback(async () => {
    if (!symbol) return
    setKlineSummaryLoading(true)
    try {
      const data = await fetchAPI<KlineSummaryResponse>(`/klines/${encodeURIComponent(symbol)}/summary?market=${encodeURIComponent(market)}`)
      setKlineSummary(data?.summary || null)
    } catch (e) {
      setKlineSummary(null)
      toast(e instanceof Error ? e.message : 'K线摘要加载失败', 'error')
    } finally {
      setKlineSummaryLoading(false)
    }
  }, [symbol, market, toast])

  const loadPositions = useCallback(async () => {
    if (!symbol) return
    setPositionsLoading(true)
    try {
      const data = await fetchAPI<PortfolioSummaryResponse>(`/portfolio/summary?include_quotes=true`)
      const matched: PortfolioPosition[] = []
      for (const acc of data?.accounts || []) {
        for (const p of acc.positions || []) {
          if (p.symbol === symbol && p.market === market) {
            matched.push({ ...(p as any), account_name: acc.name })
          }
        }
      }
      setPositions(matched)
    } catch {
      setPositions([])
    } finally {
      setPositionsLoading(false)
    }
  }, [symbol, market])

  const loadSuggestions = useCallback(async () => {
    if (!symbol) return
    try {
      const params = new URLSearchParams()
      params.set('limit', '20')
      if (includeExpired) params.set('include_expired', 'true')
      const data = await fetchAPI<any[]>(`/suggestions/${encodeURIComponent(symbol)}?${params.toString()}`)
      const list = (data || []).map(item => ({
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
      })) as SuggestionInfo[]
      setSuggestions(list)
    } catch (e) {
      setSuggestions([])
      toast(e instanceof Error ? e.message : '建议加载失败', 'error')
    }
  }, [symbol, includeExpired, toast])

  const loadNews = useCallback(async () => {
    if (!symbol) return
    try {
      const params = new URLSearchParams()
      params.set('hours', newsHours)
      params.set('limit', '80')
      params.set('filter_related', 'true')

      // 优先用名称（更稳），否则退回用 symbol
      if (stock?.name) params.set('names', stock.name)
      else params.set('symbols', symbol)

      const data = await fetchAPI<NewsItem[]>(`/news?${params.toString()}`)
      setNews(data || [])
    } catch (e) {
      setNews([])
      toast(e instanceof Error ? e.message : '新闻加载失败', 'error')
    }
  }, [symbol, stock?.name, newsHours, toast])

  const loadHistory = useCallback(async () => {
    if (!symbol) return
    try {
      const params = new URLSearchParams()
      params.set('stock_symbol', symbol)
      params.set('limit', '50')
      const data = await fetchAPI<HistoryRecord[]>(`/history?${params.toString()}`)
      setHistory(data || [])
    } catch (e) {
      setHistory([])
      toast(e instanceof Error ? e.message : '历史加载失败', 'error')
    }
  }, [symbol, toast])

  const loadBatchReports = useCallback(async () => {
    const agentNames = ['premarket_outlook', 'daily_report', 'news_digest']
    const results = await Promise.allSettled(
      agentNames.map(a => fetchAPI<HistoryRecord[]>(`/history?agent_name=${encodeURIComponent(a)}&stock_symbol=*&limit=1`))
    )

    const next: Record<string, HistoryRecord | null> = {}
    agentNames.forEach((a, idx) => {
      const r = results[idx]
      if (r.status === 'fulfilled' && r.value && r.value.length > 0) next[a] = r.value[0]
      else next[a] = null
    })
    setBatchReports(next)
  }, [])

  const loadEssential = useCallback(async () => {
    if (!symbol) return
    setLoading(true)
    try {
      await loadStockBase()
      await Promise.allSettled([loadQuote(), loadKlineSummary(), loadPositions(), loadHistory(), loadBatchReports()])
    } finally {
      setLoading(false)
    }
  }, [symbol, loadStockBase, loadQuote, loadKlineSummary, loadPositions, loadHistory, loadBatchReports])

  const refreshAll = useCallback(async () => {
    if (!symbol) return
    setLoading(true)
    try {
      await loadStockBase()
      await Promise.allSettled([
        loadQuote(),
        loadKlineSummary(),
        loadPositions(),
        loadSuggestions(),
        loadHistory(),
        loadBatchReports(),
      ])
      // news 依赖 stockName（更稳），放在 base load 后
      await loadNews()
    } finally {
      setLoading(false)
    }
  }, [symbol, loadStockBase, loadQuote, loadKlineSummary, loadPositions, loadHistory, loadBatchReports])

  useEffect(() => {
    loadEssential()
  }, [loadEssential])

  // includeExpired / hours 改变时刷新对应区域
  useEffect(() => {
    loadSuggestions()
  }, [loadSuggestions])
  useEffect(() => {
    loadNews()
  }, [loadNews])

  const triggerChartAnalyst = async () => {
    if (!stock?.id) {
      toast('该股票未在自选中，无法触发 Agent（请先添加到自选）', 'info')
      return
    }
    if (!(stock.agents || []).some(a => a.agent_name === 'chart_analyst')) {
      toast('该股未启用「技术分析」Agent，请先在持仓页为该股开启', 'info')
      return
    }
    setTriggering(true)
    try {
      const resp = await fetchAPI<{ result: AgentResult }>(`/stocks/${stock.id}/agents/chart_analyst/trigger?bypass_throttle=true`, { method: 'POST' })
      const r = resp?.result
      if (r) toast(r.should_alert ? 'AI 建议关注' : 'AI 判断无需关注', r.should_alert ? 'success' : 'info')
      await Promise.allSettled([loadSuggestions(), loadHistory()])
    } catch (e) {
      toast(e instanceof Error ? e.message : '触发失败', 'error')
    } finally {
      setTriggering(false)
    }
  }

  const badge = marketBadge(market)
  const changeColor = quote?.change_pct != null
    ? (quote.change_pct > 0 ? 'text-rose-500' : quote.change_pct < 0 ? 'text-emerald-500' : 'text-muted-foreground')
    : 'text-muted-foreground'

  const hasPosition = positions.length > 0

  const holdingAgg = useMemo(() => {
    if (positions.length === 0) return null
    let qty = 0
    let costCny = 0
    let mvCny = 0
    let costNative = 0
    for (const p of positions) {
      const rate = p.exchange_rate ?? 1
      qty += p.quantity || 0
      costCny += (p.cost_price || 0) * (p.quantity || 0) * rate
      costNative += (p.cost_price || 0) * (p.quantity || 0)
      mvCny += p.market_value_cny ?? ((p.current_price_cny ?? 0) * (p.quantity || 0))
    }
    const pnlCny = mvCny - costCny
    const pnlPct = costCny > 0 ? (pnlCny / costCny * 100) : 0
    const avgCost = qty > 0 ? (costNative / qty) : 0
    return {
      quantity: qty,
      cost_cny: costCny,
      market_value_cny: mvCny,
      pnl_cny: pnlCny,
      pnl_pct: pnlPct,
      avg_cost: avgCost,
    }
  }, [positions])

  const holdingToday = useMemo(() => {
    if (positions.length === 0) return null
    if (!quote || quote.current_price == null || quote.prev_close == null) return null
    let dayPnlCny = 0
    let prevMvCny = 0
    for (const p of positions) {
      const rate = p.exchange_rate ?? 1
      const qty = p.quantity || 0
      dayPnlCny += (quote.current_price - quote.prev_close) * qty * rate
      prevMvCny += quote.prev_close * qty * rate
    }
    const pct = prevMvCny > 0 ? (dayPnlCny / prevMvCny * 100) : 0
    return {
      day_pnl_cny: dayPnlCny,
      day_pnl_pct: pct,
    }
  }, [positions, quote])

  // (removed) quick preview module

  const tech = useMemo(() => {
    if (!klineSummary) return null
    return buildKlineSuggestion(klineSummary, hasPosition)
  }, [klineSummary, hasPosition])

  const techHighlights = useMemo(() => {
    const ev = tech?.evidence || []
    const impactful = ev.filter(e => e.delta !== 0)
    if (impactful.length === 0) return null
    const best = impactful.reduce((a, b) => (b.delta > a.delta ? b : a), impactful[0])
    const worst = impactful.reduce((a, b) => (b.delta < a.delta ? b : a), impactful[0])
    return { best, worst }
  }, [tech])

  const techEvidenceGroups = useMemo(() => {
    const ev = (tech?.evidence || []).filter(e => e.delta !== 0)
    if (ev.length === 0) return [] as Array<{ key: string; label: string; items: typeof ev }>

    const groupKey = (e: { text: string; tag?: string }) => {
      const t = `${e.tag || ''} ${e.text}`
      if (t.includes('均线') || t.includes('多头') || t.includes('空头') || t.includes('趋势')) return 'trend'
      if (t.includes('MACD')) return 'macd'
      if (t.includes('RSI')) return 'rsi'
      if (t.includes('KDJ')) return 'kdj'
      if (t.includes('布林') || t.includes('BOLL') || t.includes('上轨') || t.includes('下轨')) return 'boll'
      if (t.includes('量') || t.includes('放量') || t.includes('缩量')) return 'volume'
      if (t.includes('支撑') || t.includes('压力')) return 'levels'
      return 'other'
    }

    const labels: Record<string, string> = {
      trend: '趋势',
      macd: 'MACD',
      rsi: 'RSI',
      kdj: 'KDJ',
      boll: '布林',
      volume: '量能',
      levels: '关键位',
      other: '其他',
    }

    const order = ['trend', 'macd', 'rsi', 'kdj', 'boll', 'volume', 'levels', 'other']
    const buckets: Record<string, typeof ev> = {}
    for (const e of ev) {
      const k = groupKey(e)
      if (!buckets[k]) buckets[k] = []
      buckets[k].push(e)
    }

    // Keep total readable: up to 12 items, up to 3 per group
    const groups: Array<{ key: string; label: string; items: typeof ev }> = []
    let remaining = 12
    for (const k of order) {
      const arr = buckets[k] || []
      if (arr.length === 0 || remaining <= 0) continue
      const take = Math.min(3, remaining, arr.length)
      groups.push({ key: k, label: labels[k] || k, items: arr.slice(0, take) })
      remaining -= take
    }

    return groups
  }, [tech])

  const keyLevels = useMemo(() => {
    if (!klineSummary) return null
    const last = klineSummary.last_close
    const support = (klineSummary as any).support ?? null
    const resistance = (klineSummary as any).resistance ?? null
    const distPct = (a?: number | null, b?: number | null): number | null => {
      if (a == null || b == null || b === 0) return null
      return (a - b) / b * 100
    }
    return {
      last,
      support,
      resistance,
      toSupportPct: distPct(last, support),
      toResistancePct: distPct(last, resistance),
    }
  }, [klineSummary])

  const nearTriggers = useMemo(() => {
    if (!klineSummary) return [] as Array<{
      key: string
      label: string
      detail: string
      tone: 'bull' | 'bear' | 'neutral'
    }>

    const last = klineSummary.last_close ?? null
    const rsi6 = (klineSummary as any).rsi6 as number | null | undefined
    const volumeRatio = (klineSummary as any).volume_ratio as number | null | undefined
    const macdHist = (klineSummary as any).macd_hist as number | null | undefined
    const support = (klineSummary as any).support as number | null | undefined
    const resistance = (klineSummary as any).resistance as number | null | undefined
    const bollWidth = (klineSummary as any).boll_width as number | null | undefined

    const out: Array<{ key: string; label: string; detail: string; tone: 'bull' | 'bear' | 'neutral' }> = []

    const push = (item: typeof out[number]) => {
      if (out.find(x => x.key === item.key)) return
      out.push(item)
    }

    // RSI proximity
    if (rsi6 != null) {
      if (rsi6 >= 65 && rsi6 < 70) {
        push({ key: 'rsi-70', label: 'RSI 接近偏强', detail: `RSI6 ${rsi6.toFixed(1)} / 70`, tone: 'bull' })
      }
      if (rsi6 > 70 && rsi6 <= 80) {
        push({ key: 'rsi-80', label: 'RSI 接近超买', detail: `RSI6 ${rsi6.toFixed(1)} / 80`, tone: 'bear' })
      }
      if (rsi6 <= 35 && rsi6 > 30) {
        push({ key: 'rsi-30', label: 'RSI 接近偏弱', detail: `RSI6 ${rsi6.toFixed(1)} / 30`, tone: 'bear' })
      }
      if (rsi6 < 30 && rsi6 >= 20) {
        push({ key: 'rsi-20', label: 'RSI 接近超卖', detail: `RSI6 ${rsi6.toFixed(1)} / 20`, tone: 'bull' })
      }
    }

    // Key level proximity (wider than “触发”阈值，用于提前预警)
    const pct = (a?: number | null, b?: number | null) => {
      if (a == null || b == null || b === 0) return null
      return (a - b) / b * 100
    }
    const toSup = pct(last, support)
    const toRes = pct(last, resistance)
    if (toSup != null && toSup > 2 && toSup <= 4) {
      push({ key: 'near-support', label: '接近支撑', detail: `距离支撑 +${toSup.toFixed(1)}%`, tone: 'bull' })
    }
    if (toRes != null && toRes < -2 && toRes >= -4) {
      push({ key: 'near-resistance', label: '接近压力', detail: `距离压力 ${toRes.toFixed(1)}%`, tone: 'bear' })
    }

    // Volume ratio proximity
    if (volumeRatio != null) {
      if (volumeRatio >= 1.3 && volumeRatio < 1.5) {
        push({ key: 'vol-boost', label: '量能接近放量', detail: `量比 ${volumeRatio.toFixed(1)}x / 1.5x`, tone: 'bull' })
      }
      if (volumeRatio > 0.7 && volumeRatio <= 0.85) {
        push({ key: 'vol-dry', label: '量能接近缩量', detail: `量比 ${volumeRatio.toFixed(1)}x / 0.7x`, tone: 'bear' })
      }
    }

    // MACD hist proximity to 0 (possible flip)
    if (macdHist != null) {
      const abs = Math.abs(macdHist)
      if (abs > 0 && abs <= 0.05) {
        push({ key: 'macd-0', label: 'MACD 接近翻转', detail: `hist ${macdHist.toFixed(3)} ≈ 0`, tone: 'neutral' })
      }
    }

    // BOLL width proximity (squeeze/expand)
    if (bollWidth != null) {
      if (bollWidth >= 5 && bollWidth < 6) {
        push({ key: 'boll-squeeze', label: '布林接近收口', detail: `带宽 ${bollWidth.toFixed(1)}% / 5%`, tone: 'neutral' })
      }
      if (bollWidth > 14 && bollWidth <= 15) {
        push({ key: 'boll-expand', label: '布林接近开口', detail: `带宽 ${bollWidth.toFixed(1)}% / 15%`, tone: 'neutral' })
      }
    }

    // Keep it short
    const toneRank: Record<string, number> = { bear: 0, neutral: 1, bull: 2 }
    return out
      .sort((a, b) => toneRank[a.tone] - toneRank[b.tone])
      .slice(0, 6)
  }, [klineSummary])

  const timelineItems = useMemo(() => {
    type Item = {
      id: string
      kind: 'suggestion' | 'news' | 'history' | 'technical'
      ts: number
      timeText: string
      title: string
      desc?: string
      badge?: { text: string; className: string }
      onClick?: () => void
    }

    const items: Item[] = []

    // Technical refresh point
    if (klineSummary?.computed_at) {
      const ms = parseDateToMs(klineSummary.computed_at)
      if (ms != null) {
        items.push({
          id: `tech:${klineSummary.computed_at}`,
          kind: 'technical',
          ts: ms,
          timeText: formatTime(klineSummary.computed_at),
          title: '技术面已更新',
          desc: `${klineSummary.timeframe || '1d'}${klineSummary.asof ? ` · 数据截至 ${klineSummary.asof}` : ''}`,
          badge: { text: '技术', className: 'bg-slate-500/10 text-slate-700 border border-slate-500/20' },
          onClick: () => setKlineOpen(true),
        })
      }
    }

    // Suggestions
    for (const s of suggestions || []) {
      const ms = parseDateToMs(s.created_at || '')
      if (ms == null) continue
      const label = s.agent_label || (s.agent_name ? AGENT_LABELS[s.agent_name] : '') || '建议'
      const action = s.action_label || '建议'
      const signal = s.signal || ''
      const reason = s.reason || ''
      items.push({
        id: `sug:${s.agent_name || 'x'}:${s.created_at}`,
        kind: 'suggestion',
        ts: ms,
        timeText: formatTime(s.created_at || ''),
        title: `${action}${signal ? ` · ${signal}` : ''}`,
        desc: reason,
        badge: { text: label, className: s.is_expired ? 'bg-amber-500/10 text-amber-700 border border-amber-500/20' : 'bg-primary/10 text-primary border border-primary/20' },
        onClick: () => setTab('suggestions'),
      })
    }

    // History (agent runs)
    for (const r of history || []) {
      const ms = parseDateToMs(r.created_at) || parseDateToMs(r.updated_at) || parseDateToMs(r.analysis_date)
      if (ms == null) continue
      const label = AGENT_LABELS[r.agent_name] || r.agent_name
      items.push({
        id: `his:${r.id}`,
        kind: 'history',
        ts: ms,
        timeText: formatTime(r.created_at || r.updated_at || ''),
        title: r.title || '分析报告',
        desc: r.analysis_date ? `分析日 ${r.analysis_date}` : undefined,
        badge: { text: label, className: 'bg-accent/50 text-muted-foreground border border-border/40' },
        onClick: () => setDetailRecord(r),
      })
    }

    // News
    for (const n of news || []) {
      const ms = parseDateToMs(n.publish_time)
      if (ms == null) continue
      items.push({
        id: `news:${n.source}:${n.external_id}`,
        kind: 'news',
        ts: ms,
        timeText: n.publish_time.includes('T') ? formatTime(n.publish_time) : n.publish_time.slice(-5),
        title: n.title,
        desc: n.source_label,
        badge: { text: n.source_label, className: `border ${SOURCE_COLORS[n.source] || 'bg-accent/30 text-muted-foreground border-border/40'}` },
        onClick: () => setTab('news'),
      })
    }

    // Sort desc by ts
    items.sort((a, b) => b.ts - a.ts)
    return items
  }, [klineSummary, suggestions, history, news])

  const techActionStyle = (action?: string) => {
    if (action === 'buy' || action === 'add') return 'bg-rose-500 text-white'
    if (action === 'reduce' || action === 'sell') return 'bg-emerald-600 text-white'
    if (action === 'hold') return 'bg-amber-500 text-white'
    if (action === 'avoid') return 'bg-red-600 text-white'
    return 'bg-slate-500 text-white'
  }

  if (!symbol) {
    return (
      <div className="card p-6">
        <div className="text-[13px] text-muted-foreground">无效的股票参数</div>
      </div>
    )
  }

  return (
    <div className="w-full space-y-4 md:space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
        <div className="flex items-center gap-2 md:gap-3">
          <Button variant="ghost" size="icon" className="h-9 w-9" onClick={() => navigate(-1)} title="返回">
            <ArrowLeft className="w-4 h-4" />
          </Button>
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <span className={`text-[10px] px-2 py-0.5 rounded ${badge.style}`}>{badge.label}</span>
              <h1 className="text-xl md:text-2xl font-bold">{resolvedName}</h1>
              <span className="font-mono text-[12px] text-muted-foreground">({symbol})</span>
            </div>
            {/* Holding summary moved into Quote card to avoid duplication */}
          </div>
        </div>

        <div className="flex items-center gap-2 flex-wrap justify-start sm:justify-end">
            <Button variant="outline" size="sm" className="h-9 gap-1.5" onClick={() => setKlineOpen(true)}>
              <BarChart3 className="w-3.5 h-3.5" />
              K线/指标
            </Button>
            <Button variant="secondary" size="sm" className="h-9 gap-1.5" onClick={triggerChartAnalyst} disabled={triggering}>
              <Sparkles className={`w-3.5 h-3.5 ${triggering ? 'animate-pulse' : ''}`} />
              技术分析
            </Button>
            <Button variant="outline" size="sm" className="h-9 w-9 p-0" onClick={refreshAll} disabled={loading}>
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </Button>
        </div>
      </div>

      <KlineSummaryDialog
        open={klineOpen}
        onOpenChange={setKlineOpen}
        symbol={symbol}
        market={market}
        stockName={stock?.name || quote?.name || symbol}
        hasPosition={hasPosition}
        initialSummary={klineSummary as any}
      />

      {/* Tabs */}
      <div className="flex items-center gap-1 flex-wrap">
        {[
          { value: 'overview' as const, label: '概览' },
          { value: 'suggestions' as const, label: `建议 (${suggestions.length})` },
          { value: 'news' as const, label: `新闻 (${news.length})` },
          { value: 'history' as const, label: `历史 (${history.length})` },
        ].map(t => (
          <button
            key={t.value}
            onClick={() => setTab(t.value)}
            className={`text-[11px] px-2.5 py-1 rounded transition-colors ${
              tab === t.value
                ? 'bg-primary text-primary-foreground'
                : 'bg-accent/50 text-muted-foreground hover:bg-accent'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Overview */}
      {tab === 'overview' && (
        <div className="space-y-4">
          <div className="card p-4 md:p-5 flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[13px] font-semibold text-foreground">交互K线</div>
              <div className="text-[11px] text-muted-foreground">日K/周K/月K切换，含 MA/成交量/MACD</div>
            </div>
            <Button variant="secondary" size="sm" className="h-8" onClick={() => setKlineOpen(true)}>
              打开
            </Button>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
            {/* Quote */}
            <div className="card p-5 lg:col-span-4">
              <div className="flex items-center justify-between mb-2">
                <div className="text-[13px] font-semibold text-foreground">行情</div>
                <span className="text-[11px] text-muted-foreground">{quote ? '实时' : '暂无数据'}</span>
              </div>
              {!quote ? (
                <div className="text-[12px] text-muted-foreground py-6 text-center">暂无行情</div>
              ) : (
                <div className="space-y-3">
                  {hasPosition && holdingAgg && (
                    <div className="rounded-xl border border-border/40 bg-gradient-to-br from-accent/30 to-background p-3">
                      <div className="flex items-end justify-between gap-3">
                        <div>
                          <div className="text-[10px] text-muted-foreground">总盈亏</div>
                          <div className={`text-[16px] font-semibold font-mono ${holdingAgg.pnl_cny >= 0 ? 'text-rose-600' : 'text-emerald-600'}`}>
                            {holdingAgg.pnl_cny >= 0 ? '+' : ''}{formatMoneyWan(holdingAgg.pnl_cny)}
                            <span className="ml-2 text-[11px] opacity-80">({holdingAgg.pnl_pct >= 0 ? '+' : ''}{holdingAgg.pnl_pct.toFixed(2)}%)</span>
                          </div>
                        </div>
                        {holdingToday && (
                          <div className="text-right">
                            <div className="text-[10px] text-muted-foreground">今日</div>
                            <div className={`text-[14px] font-semibold font-mono ${holdingToday.day_pnl_cny >= 0 ? 'text-rose-600' : 'text-emerald-600'}`}>
                              {holdingToday.day_pnl_cny >= 0 ? '+' : ''}{formatMoneyWan(holdingToday.day_pnl_cny)}
                              <span className="ml-2 text-[11px] opacity-80">({holdingToday.day_pnl_pct >= 0 ? '+' : ''}{holdingToday.day_pnl_pct.toFixed(2)}%)</span>
                            </div>
                          </div>
                        )}
                      </div>
                      <div className="mt-2 grid grid-cols-2 gap-2 text-[12px]">
                        <div className="flex items-center justify-between">
                          <span className="text-muted-foreground">持仓</span>
                          <span className="font-mono">{holdingAgg.quantity}</span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-muted-foreground">均价</span>
                          <span className="font-mono">{holdingAgg.avg_cost.toFixed(2)}</span>
                        </div>
                      </div>
                    </div>
                  )}

                  <div className="flex items-end justify-between gap-3">
                    <div className="text-[28px] font-bold font-mono text-foreground">
                      {quote.current_price != null ? formatNumber(quote.current_price) : '--'}
                    </div>
                    <div className={`text-[14px] font-mono ${changeColor}`}>
                      {quote.change_pct != null ? `${quote.change_pct >= 0 ? '+' : ''}${quote.change_pct.toFixed(2)}%` : '--'}
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-2 text-[12px]">
                    <div className="flex items-center justify-between">
                      <span className="text-muted-foreground">昨收</span>
                      <span className="font-mono">{formatNumber(quote.prev_close)}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-muted-foreground">今开</span>
                      <span className="font-mono">{formatNumber(quote.open_price)}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-muted-foreground">最高</span>
                      <span className="font-mono">{formatNumber(quote.high_price)}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-muted-foreground">最低</span>
                      <span className="font-mono">{formatNumber(quote.low_price)}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-muted-foreground">成交量</span>
                      <span className="font-mono">{formatCompactNumber(quote.volume)}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-muted-foreground">成交额</span>
                      <span className="font-mono">{formatCompactNumber(quote.turnover)}</span>
                    </div>
                  </div>

                  {quote.change_amount != null && (
                    <div className="text-[11px] text-muted-foreground">
                      涨跌额：<span className={`font-mono ${changeColor}`}>{quote.change_amount >= 0 ? '+' : ''}{formatNumber(quote.change_amount)}</span>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Technical */}
            <div className="card p-5 lg:col-span-4">
              <div className="flex items-center justify-between mb-2">
                <div className="text-[13px] font-semibold text-foreground">技术面</div>
                <span className="text-[11px] text-muted-foreground">{klineSummaryLoading ? '加载中' : klineSummary ? '日K' : '暂无数据'}</span>
              </div>
              {!klineSummary ? (
                <div className="text-[12px] text-muted-foreground py-6 text-center">暂无 K 线摘要</div>
              ) : (
                <div className="space-y-3">
                  {tech && (
                    <div className="flex items-center justify-between gap-2">
                      <span className={`text-[11px] px-2 py-1 rounded font-medium ${techActionStyle(tech.action)}`}>
                        {tech.action_label}
                      </span>
                      <span className="text-[10px] text-muted-foreground">score {tech.score}</span>
                    </div>
                  )}
                  {tech && (
                    <div className="text-[12px] font-medium text-foreground">
                      {tech.signal}
                    </div>
                  )}

                  {/* Compact snapshot (avoid duplicating full indicator block) */}
                  <div className="flex flex-wrap gap-2 text-[11px]">
                    {(klineSummary as any).trend && (
                      <span className="px-2 py-0.5 rounded bg-accent/50 text-muted-foreground">{(klineSummary as any).trend}</span>
                    )}
                    {(klineSummary as any).macd_status && (
                      <span className="px-2 py-0.5 rounded bg-accent/50 text-muted-foreground">MACD {(klineSummary as any).macd_status}</span>
                    )}
                    {(klineSummary as any).rsi_status && (
                      <span className="px-2 py-0.5 rounded bg-accent/50 text-muted-foreground">RSI {(klineSummary as any).rsi_status}</span>
                    )}
                    {(klineSummary as any).volume_trend && (
                      <span className="px-2 py-0.5 rounded bg-accent/50 text-muted-foreground">
                        {(klineSummary as any).volume_trend}{(klineSummary as any).volume_ratio != null ? ` (${Number((klineSummary as any).volume_ratio).toFixed(1)}x)` : ''}
                      </span>
                    )}
                    {(klineSummary as any).kline_pattern && (
                      <span className="px-2 py-0.5 rounded bg-amber-500/10 text-amber-600">{(klineSummary as any).kline_pattern}</span>
                    )}
                  </div>

                  {techHighlights && (
                    <div className="rounded-lg bg-accent/20 p-2 text-[12px]">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="text-[10px] text-muted-foreground">最强利好</div>
                          <div className="text-foreground line-clamp-2">{techHighlights.best.text}</div>
                        </div>
                        <span className="shrink-0 font-mono text-[11px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-600">
                          +{techHighlights.best.delta}
                        </span>
                      </div>
                      <div className="mt-2 flex items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="text-[10px] text-muted-foreground">最大拖累</div>
                          <div className="text-foreground line-clamp-2">{techHighlights.worst.text}</div>
                        </div>
                        <span className="shrink-0 font-mono text-[11px] px-1.5 py-0.5 rounded bg-rose-500/10 text-rose-600">
                          {techHighlights.worst.delta}
                        </span>
                      </div>
                    </div>
                  )}

                  {keyLevels && (keyLevels.support != null || keyLevels.resistance != null) && (
                    <div className="grid grid-cols-2 gap-2 text-[12px]">
                      {keyLevels.support != null && (
                        <div className="flex items-center justify-between">
                          <span className="text-muted-foreground">支撑</span>
                          <span className="font-mono text-emerald-600">
                            {Number(keyLevels.support).toFixed(2)}
                            {keyLevels.toSupportPct != null && (
                              <span className="ml-1 text-[10px] text-muted-foreground">
                                ({keyLevels.toSupportPct >= 0 ? '+' : ''}{keyLevels.toSupportPct.toFixed(1)}%)
                              </span>
                            )}
                          </span>
                        </div>
                      )}
                      {keyLevels.resistance != null && (
                        <div className="flex items-center justify-between">
                          <span className="text-muted-foreground">压力</span>
                          <span className="font-mono text-rose-600">
                            {Number(keyLevels.resistance).toFixed(2)}
                            {keyLevels.toResistancePct != null && (
                              <span className="ml-1 text-[10px] text-muted-foreground">
                                ({keyLevels.toResistancePct >= 0 ? '+' : ''}{keyLevels.toResistancePct.toFixed(1)}%)
                              </span>
                            )}
                          </span>
                        </div>
                      )}
                    </div>
                  )}

                  {nearTriggers.length > 0 && (
                    <div className="rounded-lg bg-accent/20 p-2">
                      <div className="text-[10px] font-medium text-muted-foreground mb-1">接近触发</div>
                      <div className="flex flex-wrap gap-2">
                        {nearTriggers.map(t => {
                          const cls = t.tone === 'bull'
                            ? 'bg-emerald-500/10 text-emerald-700'
                            : t.tone === 'bear'
                              ? 'bg-rose-500/10 text-rose-700'
                              : 'bg-slate-500/10 text-slate-700'
                          return (
                            <span key={t.key} className={`px-2 py-1 rounded text-[11px] ${cls}`} title={t.detail}>
                              <span className="font-medium">{t.label}</span>
                              <span className="ml-1 opacity-70">{t.detail}</span>
                            </span>
                          )
                        })}
                      </div>
                    </div>
                  )}

                  {techEvidenceGroups.length ? (
                    <details className="group">
                      <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground">
                        证据链 <span className="text-[10px]">(点击展开)</span>
                      </summary>
                      <div className="mt-2 space-y-3">
                        {techEvidenceGroups.map(g => (
                          <div key={g.key}>
                            <div className="text-[10px] font-medium text-muted-foreground mb-1">{g.label}</div>
                            <div className="space-y-1">
                              {g.items.map((e, idx) => (
                                <div key={`${e.tag || g.key}-${idx}`} className="flex items-start justify-between gap-3 text-[12px]">
                                  <div className="min-w-0 flex-1">
                                    <div className="text-foreground">{e.text}</div>
                                    {e.details && (
                                      <div className="mt-0.5 text-[10px] text-muted-foreground/70">{e.details}</div>
                                    )}
                                  </div>
                                  <span className={`shrink-0 font-mono text-[11px] px-1.5 py-0.5 rounded ${
                                    e.delta > 0 ? 'bg-emerald-500/10 text-emerald-600' : 'bg-rose-500/10 text-rose-600'
                                  }`}>
                                    {e.delta > 0 ? '+' : ''}{e.delta}
                                  </span>
                                </div>
                              ))}
                            </div>
                          </div>
                        ))}
                        {(tech?.evidence || []).filter(e => e.delta !== 0).length > 12 && (
                          <div className="pt-1 text-[10px] text-muted-foreground/70">仅展示每类前 3 条（最多 12 条）</div>
                        )}
                      </div>
                    </details>
                  ) : null}

                  <div className="flex items-center justify-between">
                    <Button variant="ghost" size="sm" className="h-7" onClick={() => setKlineOpen(true)}>
                      查看指标详情
                    </Button>
                    {klineSummary?.computed_at && (
                      <span className="text-[10px] text-muted-foreground/60">{formatTime(klineSummary.computed_at)}</span>
                    )}
                  </div>
                </div>
              )}
            </div>

            {/* Key suggestion */}
            <div className="card p-5 lg:col-span-4">
              <div className="flex items-center justify-between mb-2">
                <div className="text-[13px] font-semibold text-foreground">关键观点</div>
                <Button variant="ghost" size="sm" className="h-7" onClick={() => setTab('suggestions')}>查看全部</Button>
              </div>

              {(() => {
                const latest = (suggestions || []).find(s => !s.is_expired) || (suggestions || [])[0]
                if (!latest) {
                  return <div className="text-[12px] text-muted-foreground py-6">暂无建议</div>
                }
                return (
                  <button
                    onClick={() => setTab('suggestions')}
                    className="w-full text-left rounded-xl bg-accent/20 p-4 hover:bg-accent/30 transition-colors"
                    title="点击查看全部建议"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <SuggestionBadge suggestion={latest} stockName={resolvedName} stockSymbol={symbol} />
                      <span className="text-[10px] text-muted-foreground">{latest.created_at ? formatTime(latest.created_at) : ''}</span>
                    </div>
                    {(latest.reason || latest.signal) && (
                      <div className="mt-3 text-[13px] text-foreground leading-relaxed line-clamp-6">
                        {latest.signal ? `${latest.signal}${latest.reason ? `：${latest.reason}` : ''}` : latest.reason}
                      </div>
                    )}
                  </button>
                )
              })()}
            </div>
          </div>

          {/* Second row: reports + news */}
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
            <div className="card p-5 lg:col-span-6">
              <div className="flex items-center justify-between mb-3">
                <div className="text-[13px] font-semibold text-foreground">最新报告</div>
                <Button variant="ghost" size="sm" className="h-7" onClick={() => setTab('history')}>查看更多</Button>
              </div>
              <div className="grid grid-cols-1 gap-2">
                {(['premarket_outlook', 'daily_report', 'news_digest'] as const).map(agent => {
                  const rec = batchReports[agent]
                  const sug = rec?.suggestions ? (rec.suggestions as any)[symbol] : null
                  return (
                    <button
                      key={agent}
                      onClick={() => rec && setDetailRecord(rec)}
                      disabled={!rec}
                      className={`flex items-center justify-between gap-3 px-4 py-3 rounded-xl border text-left transition-colors ${
                        rec ? 'bg-background/40 border-border/30 hover:bg-accent/20' : 'bg-background/20 border-border/20 opacity-60 cursor-not-allowed'
                      }`}
                      title={rec ? '点击查看报告全文' : '暂无记录'}
                    >
                      <span className="text-[11px] text-muted-foreground shrink-0">{AGENT_LABELS[agent]}</span>
                      <span className="text-[12px] text-foreground truncate">
                        {sug ? `${sug.action_label}${sug.reason ? `：${String(sug.reason).slice(0, 24)}…` : ''}` : (rec ? '未给出该股建议' : '--')}
                      </span>
                      <span className="text-[10px] text-muted-foreground shrink-0">{rec?.analysis_date || '--'}</span>
                    </button>
                  )
                })}
              </div>
            </div>

            <div className="card p-5 lg:col-span-6">
              <div className="flex items-center justify-between mb-3">
                <div className="text-[13px] font-semibold text-foreground">最新新闻</div>
                <Button variant="ghost" size="sm" className="h-7" onClick={() => setTab('news')}>查看全部</Button>
              </div>
              {news.length === 0 ? (
                <div className="text-[12px] text-muted-foreground py-6">暂无相关新闻</div>
              ) : (
                <div className="space-y-2">
                  {news.slice(0, 4).map((n, idx) => (
                    <div key={`${n.source}-${n.external_id}-${idx}`} className="rounded-xl bg-accent/10 p-3">
                      <div className="flex items-start gap-2">
                        <Badge variant="outline" className={`text-[10px] flex-shrink-0 ${SOURCE_COLORS[n.source] || ''}`}>{n.source_label}</Badge>
                        <div className="min-w-0 flex-1">
                          {n.url ? (
                            <a
                              href={n.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-[12px] font-medium leading-snug line-clamp-2 hover:text-primary hover:underline inline-flex items-center gap-1"
                            >
                              {n.title}
                              <ExternalLink className="w-3 h-3 opacity-70" />
                            </a>
                          ) : (
                            <div className="text-[12px] font-medium leading-snug line-clamp-2">{n.title}</div>
                          )}
                          <div className="mt-1 text-[10px] text-muted-foreground">{n.publish_time}</div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      <KlineModal
        open={klineOpen}
        onOpenChange={setKlineOpen}
        symbol={symbol}
        market={market}
        title={resolvedName ? `K线：${resolvedName}` : `K线：${symbol}`}
      />

      {/* Suggestions tab */}
      {tab === 'suggestions' && (
        <div className="card p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="text-[13px] font-semibold text-foreground">建议列表</div>
            <button
              onClick={() => setIncludeExpired(v => !v)}
              className={`text-[11px] px-2 py-0.5 rounded transition-colors ${
                includeExpired
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-accent/50 text-muted-foreground hover:bg-accent'
              }`}
            >
              {includeExpired ? '包含过期' : '不含过期'}
            </button>
          </div>
          {suggestions.length === 0 ? (
            <div className="text-[12px] text-muted-foreground py-10 text-center">暂无建议</div>
          ) : (
            <div className="space-y-3">
              {suggestions.map((s, idx) => (
                <div key={`${s.agent_name || 's'}-${idx}`} className="p-3 rounded-lg bg-accent/20">
                  <SuggestionBadge suggestion={s} stockName={resolvedName} stockSymbol={symbol} showFullInline />
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* News tab */}
      {tab === 'news' && (
        <div className="card p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Newspaper className="w-4 h-4 text-muted-foreground" />
              <div className="text-[13px] font-semibold text-foreground">新闻</div>
            </div>
            <div className="flex items-center gap-2">
              <Select value={newsHours} onValueChange={setNewsHours}>
                <SelectTrigger className="h-8 w-[120px] text-[12px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TIME_OPTIONS.map(opt => (
                    <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <span className="text-[11px] text-muted-foreground">共 {news.length} 条</span>
            </div>
          </div>

          {news.length === 0 ? (
            <div className="text-[12px] text-muted-foreground py-10 text-center">暂无相关新闻</div>
          ) : (
            <div className="divide-y divide-border/50">
              {news.map((item, idx) => (
                <div key={`${item.source}-${item.external_id}-${idx}`} className="py-3">
                  <div className="flex items-start gap-3">
                    <Badge variant="outline" className={`text-[10px] flex-shrink-0 ${SOURCE_COLORS[item.source] || ''}`}>
                      {item.source_label}
                    </Badge>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start justify-between gap-2">
                        {item.url ? (
                          <a
                            href={item.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-[13px] font-medium leading-snug hover:text-primary hover:underline"
                          >
                            {item.title}
                          </a>
                        ) : (
                          <div className="text-[13px] font-medium leading-snug">{item.title}</div>
                        )}
                        <span className="text-[11px] text-muted-foreground flex-shrink-0">{item.publish_time}</span>
                      </div>
                      {item.content && (
                        <div className="mt-1 text-[12px] text-muted-foreground line-clamp-3">
                          {item.content}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* History tab */}
      {tab === 'history' && (
        <div className="space-y-4">
          <div className="card p-4">
            <div className="text-[13px] font-semibold text-foreground mb-3">盘前 / 盘后 / 新闻报告（最新）</div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {(['premarket_outlook', 'daily_report', 'news_digest'] as const).map(agent => {
                const rec = batchReports[agent]
                const sug = rec?.suggestions ? (rec.suggestions as any)[symbol] : null
                return (
                  <button
                    key={agent}
                    onClick={() => rec && setDetailRecord(rec)}
                    disabled={!rec}
                    className={`text-left p-3 rounded-lg border transition-colors ${
                      rec ? 'bg-accent/20 border-border/30 hover:bg-accent/30' : 'bg-accent/10 border-border/20 opacity-60 cursor-not-allowed'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <Badge variant="outline" className="text-[10px]">{AGENT_LABELS[agent]}</Badge>
                      <span className="text-[10px] text-muted-foreground">{rec?.analysis_date || '--'}</span>
                    </div>
                    {sug ? (
                      <div className="mt-2 text-[12px]">
                        <SuggestionBadge
                          suggestion={{
                            action: sug.action,
                            action_label: sug.action_label,
                            signal: '',
                            reason: sug.reason || '',
                            should_alert: !!sug.should_alert,
                            agent_name: agent,
                            agent_label: AGENT_LABELS[agent] || agent,
                          }}
                          stockName={resolvedName}
                          stockSymbol={symbol}
                          showFullInline
                        />
                      </div>
                    ) : (
                      <div className="mt-2 text-[12px] text-muted-foreground">
                        {rec ? '本次报告未给出该股建议' : '暂无记录'}
                      </div>
                    )}
                  </button>
                )
              })}
            </div>
          </div>

          <div className="card p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="text-[13px] font-semibold text-foreground">个股历史记录</div>
              <span className="text-[11px] text-muted-foreground">共 {history.length} 条</span>
            </div>
            {history.length === 0 ? (
              <div className="text-[12px] text-muted-foreground py-10 text-center">
                暂无个股历史记录（该区只展示 stock_symbol={symbol} 的 Agent 输出）
              </div>
            ) : (
              <div className="space-y-2">
                {history.map(r => (
                  <button
                    key={r.id}
                    onClick={() => setDetailRecord(r)}
                    className="w-full text-left p-3 rounded-lg bg-accent/20 hover:bg-accent/30 transition-colors"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <Badge variant="outline" className="text-[10px]">{AGENT_LABELS[r.agent_name] || r.agent_name}</Badge>
                      <span className="text-[10px] text-muted-foreground">{r.analysis_date}</span>
                    </div>
                    <div className="mt-1 text-[12px] text-foreground line-clamp-2">{r.title || '分析报告'}</div>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="card p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="text-[13px] font-semibold text-foreground">动态</div>
              <span className="text-[11px] text-muted-foreground">最新 {Math.min(12, timelineItems.length)} 条</span>
            </div>
            {timelineItems.length === 0 ? (
              <div className="text-[12px] text-muted-foreground py-10 text-center">暂无动态</div>
            ) : (
              <div className="space-y-1">
                {timelineItems.slice(0, 12).map(it => (
                  <button
                    key={it.id}
                    onClick={it.onClick}
                    className="w-full text-left p-2 rounded-lg hover:bg-accent/30 transition-colors"
                    title={it.desc || it.title}
                  >
                    <div className="flex items-start gap-2">
                      <span className="shrink-0 font-mono text-[10px] text-muted-foreground w-10 pt-[2px]">{it.timeText || '--:--'}</span>
                      {it.badge ? (
                        <span className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded ${it.badge.className}`}>{it.badge.text}</span>
                      ) : null}
                      <div className="min-w-0 flex-1">
                        <div className="text-[12px] text-foreground line-clamp-1">{it.title}</div>
                        {it.desc && <div className="text-[10px] text-muted-foreground line-clamp-1">{it.desc}</div>}
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Detail Dialog */}
      <Dialog open={!!detailRecord} onOpenChange={open => !open && setDetailRecord(null)}>
        <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{detailRecord?.title || '分析详情'}</DialogTitle>
            <DialogDescription>
              {detailRecord && (
                <div className="flex items-center gap-2 flex-wrap">
                  <Badge variant="outline">{AGENT_LABELS[detailRecord.agent_name] || detailRecord.agent_name}</Badge>
                  <span className="text-[11px] text-muted-foreground">{detailRecord.analysis_date}</span>
                  {detailRecord.created_at && (
                    <span className="text-[11px] text-muted-foreground">· {formatTime(detailRecord.created_at)}</span>
                  )}
                </div>
              )}
            </DialogDescription>
          </DialogHeader>
          <div className="mt-4 p-4 bg-accent/20 rounded-lg prose prose-sm dark:prose-invert max-w-none">
            {detailRecord && <ReactMarkdown>{detailRecord.content}</ReactMarkdown>}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
