import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { Copy, Download, ExternalLink, RefreshCw, Share2 } from 'lucide-react'
import { insightApi, stocksApi } from '@panwatch/api'
import { getMarketBadge } from '@panwatch/biz-ui'
import { useLocalStorage } from '@/lib/utils'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { Switch } from '@panwatch/base-ui/components/ui/switch'
import { SuggestionBadge, type KlineSummary, type SuggestionInfo } from '@panwatch/biz-ui/components/suggestion-badge'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import InteractiveKline from '@panwatch/biz-ui/components/InteractiveKline'
import { KlineIndicators } from '@panwatch/biz-ui/components/kline-indicators'
import { buildKlineSuggestion } from '@/lib/kline-scorer'
import StockPriceAlertPanel from '@panwatch/biz-ui/components/stock-price-alert-panel'
import { TechnicalBadge } from '@panwatch/biz-ui/components/technical-badge'

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
  turnover_rate?: number | null
  pe_ratio?: number | null
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
  content?: string
  publish_time: string
  url: string
  symbols?: string[]
}

interface HistoryRecord {
  id: number
  agent_name: string
  stock_symbol: string
  analysis_date: string
  title: string
  content: string
  suggestions?: Record<string, any> | null
  news?: Array<{
    source?: string
    title?: string
    publish_time?: string
    url?: string
  }> | null
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

type InsightTab = 'overview' | 'kline' | 'suggestions' | 'news' | 'announcements' | 'reports'

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
  agents?: StockAgentInfo[]
}

const AGENT_LABELS: Record<string, string> = {
  daily_report: '盘后日报',
  premarket_outlook: '盘前分析',
  news_digest: '新闻速递',
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

function formatMarketCap(value: number | null | undefined, market?: string): string {
  if (value == null) return '--'
  const n = Number(value)
  if (!isFinite(n)) return '--'
  const m = String(market || '').toUpperCase()
  const abs = Math.abs(n)

  // 腾讯 A 股字段常见为“亿元”口径（如 808 表示 808 亿元）
  if (m === 'CN' && abs > 0 && abs < 100000) {
    return `${n.toFixed(2)}亿元`
  }

  if (abs >= 1e8) return `${(n / 1e8).toFixed(2)}亿元`
  if (abs >= 1e4) return `${(n / 1e4).toFixed(2)}万元`
  return `${n.toFixed(0)}元`
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

function parseSuggestionJson(raw: unknown): Record<string, any> | null {
  if (typeof raw !== 'string') return null
  const s = raw.trim()
  if (!s) return null
  const candidates: string[] = [s]
  const fence = s.match(/```(?:json)?\s*([\s\S]*?)```/i)
  if (fence?.[1]) candidates.unshift(fence[1].trim())
  if (/^json\s*[\r\n]/i.test(s)) candidates.unshift(s.replace(/^json\s*[\r\n]/i, '').trim())
  for (const c of candidates) {
    if (!c) continue
    const direct = c
    const sliceStart = c.indexOf('{')
    const sliceEnd = c.lastIndexOf('}')
    const sliced = sliceStart >= 0 && sliceEnd > sliceStart ? c.slice(sliceStart, sliceEnd + 1) : ''
    for (const text of [direct, sliced]) {
      if (!text || !text.startsWith('{') || !text.endsWith('}')) continue
      try {
        const obj = JSON.parse(text)
        if (obj && typeof obj === 'object') return obj as Record<string, any>
      } catch {
        // try next candidate
      }
    }
  }
  return null
}

function normalizeSuggestionAction(action?: string, actionLabel?: string): string {
  const a = String(action || '').trim().toLowerCase()
  const l = String(actionLabel || '').trim()
  if (a === 'buy/add' || a === 'add/buy') return /加仓|增持|补仓/.test(l) ? 'add' : 'buy'
  if (a === 'sell/reduce' || a === 'reduce/sell') return /减仓|减持/.test(l) ? 'reduce' : 'sell'
  return a || 'watch'
}

function pickSuggestionText(raw: unknown, field: 'signal' | 'reason'): string {
  const plain = String(raw || '').trim()
  const obj = parseSuggestionJson(plain)
  if (obj) {
    const v = String(obj[field] || '').trim()
    if (v) return v
    if (field === 'reason') {
      const rv = String(obj['raw'] || '').trim()
      if (rv) return rv
    }
    return ''
  }
  return plain
}

function normalizeTextList(raw: unknown): string[] {
  if (Array.isArray(raw)) return raw.map(x => String(x || '').trim()).filter(Boolean)
  const s = String(raw || '').trim()
  if (!s) return []
  const bySep = s.split(/[；;、|]/).map(x => x.trim()).filter(Boolean)
  return bySep.length > 1 ? bySep : [s]
}

function markdownToPlainText(input?: string): string {
  const raw = String(input || '').trim()
  if (!raw) return ''
  return raw
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)]\([^)]*\)/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\s*>\s?/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+\.\s+/gm, '')
    .replace(/\*\*|__|\*|_/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function firstNonEmptyText(...vals: unknown[]): string {
  for (const v of vals) {
    const s = String(v || '').trim()
    if (s) return s
  }
  return ''
}

function buildShareTechnicalRisks(kline: KlineSummary | null): string[] {
  if (!kline) return []
  const out: string[] = []
  const rsi = String(kline.rsi_status || '')
  const macd = `${kline.macd_cross || ''} ${kline.macd_status || ''}`
  const vol = String(kline.volume_trend || '')
  if (rsi.includes('超买')) out.push('短线过热回撤风险')
  if (rsi.includes('超卖')) out.push('弱势延续风险')
  if (macd.includes('死叉')) out.push('趋势转弱风险')
  if (macd.includes('顶背离')) out.push('动能背离风险')
  if (vol.includes('放量')) out.push('波动放大风险')
  return out.slice(0, 3)
}

function TechnicalIndicatorStrip(props: {
  klineSummary: KlineSummary | null
  technicalSuggestion: SuggestionInfo | null
  stockName: string
  stockSymbol: string
  market: string
  hasPosition: boolean
  score?: number
  evidence?: Array<{ text: string; delta: number }>
}) {
  const { klineSummary, technicalSuggestion, stockName, stockSymbol, market, hasPosition, score, evidence = [] } = props
  if (!klineSummary) {
    return <div className="text-[12px] text-muted-foreground py-3">暂无技术指标</div>
  }
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[12px] text-muted-foreground">技术指标建议</span>
        <SuggestionBadge
          suggestion={technicalSuggestion}
          stockName={stockName}
          stockSymbol={stockSymbol}
          market={market}
          kline={klineSummary}
          hasPosition={hasPosition}
        />
        <TechnicalBadge label={`评分 ${Number(score ?? 0).toFixed(1)}`} tone="neutral" size="xs" className="text-foreground" />
      </div>
      {evidence.length > 0 && (
        <div className="flex flex-wrap gap-1.5 text-[10px]">
          {evidence.slice(0, 6).map((item, idx) => (
            <TechnicalBadge
              key={`${item.text}-${idx}`}
              label={`${item.text} ${item.delta > 0 ? `+${item.delta}` : item.delta}`}
              tone={item.delta > 0 ? 'bullish' : item.delta < 0 ? 'bearish' : 'neutral'}
              size="xs"
            />
          ))}
        </div>
      )}
      <KlineIndicators summary={klineSummary as any} />
    </div>
  )
}

export default function StockInsightModal(props: {
  open: boolean
  onOpenChange: (open: boolean) => void
  symbol: string
  market: string
  stockName?: string
  hasPosition?: boolean
}) {
  const { toast } = useToast()
  const symbol = String(props.symbol || '').trim()
  const market = String(props.market || 'CN').trim().toUpperCase()
  const [loading, setLoading] = useState(false)
  const [tab, setTab] = useState<InsightTab>('overview')
  const [newsHours, setNewsHours] = useLocalStorage<string>('stock_insight_news_hours', '168')
  const [announcementHours, setAnnouncementHours] = useLocalStorage<string>('stock_insight_announcement_hours', '168')
  const [includeExpiredSuggestions, setIncludeExpiredSuggestions] = useLocalStorage<boolean>(
    'stock_insight_include_expired_suggestions',
    true
  )
  const [autoRefreshEnabled, setAutoRefreshEnabled] = useLocalStorage<boolean>(
    'stock_insight_auto_refresh_enabled',
    true
  )
  const [autoRefreshSec, setAutoRefreshSec] = useLocalStorage<number>(
    'stock_insight_auto_refresh_sec',
    20
  )
  const [quote, setQuote] = useState<QuoteResponse | null>(null)
  const [klineSummary, setKlineSummary] = useState<KlineSummary | null>(null)
  const [miniKlines, setMiniKlines] = useState<MiniKlineResponse['klines']>([])
  const [miniKlineLoading, setMiniKlineLoading] = useState(false)
  const [miniHoverIdx, setMiniHoverIdx] = useState<number | null>(null)
  const [suggestions, setSuggestions] = useState<SuggestionInfo[]>([])
  const [news, setNews] = useState<NewsItem[]>([])
  const [announcements, setAnnouncements] = useState<NewsItem[]>([])
  const [reports, setReports] = useState<HistoryRecord[]>([])
  const [reportTab, setReportTab] = useState<'premarket_outlook' | 'daily_report' | 'news_digest'>('premarket_outlook')
  const [klineInterval] = useState<'1d' | '1w' | '1m'>('1d')
  const [klineDays] = useState<'60' | '120' | '250'>('120')
  const [alerting, setAlerting] = useState(false)
  const [watchingStock, setWatchingStock] = useState<StockItem | null>(null)
  const [watchToggleLoading, setWatchToggleLoading] = useState(false)
  const [autoSuggesting, setAutoSuggesting] = useState(false)
  const [imageExporting, setImageExporting] = useState(false)
  const [holdingAgg, setHoldingAgg] = useState<{
    quantity: number
    cost: number
    unitCost: number
    marketValue: number
    pnl: number
  } | null>(null)
  const [holdingLoaded, setHoldingLoaded] = useState(false)
  const [holdingLoadError, setHoldingLoadError] = useState(false)
  const autoTriggeredRef = useRef<Record<string, number>>({})
  const stockCacheRef = useRef<Record<string, StockItem>>({})
  const resolvedName = useMemo(() => props.stockName || quote?.name || symbol, [props.stockName, quote?.name, symbol])

  const loadQuote = useCallback(async () => {
    if (!symbol) return
    const data = await insightApi.quote<QuoteResponse>(symbol, market)
    setQuote(data || null)
  }, [symbol, market])

  const loadKline = useCallback(async () => {
    if (!symbol) return
    const data = await insightApi.klineSummary<KlineSummaryResponse>(symbol, market)
    setKlineSummary(data?.summary || null)
  }, [symbol, market])

  const loadMiniKline = useCallback(async (opts?: { silent?: boolean }) => {
    if (!symbol) return
    const silent = !!opts?.silent
    if (!silent) setMiniKlineLoading(true)
    try {
      const data = await insightApi.klines<MiniKlineResponse>(symbol, {
        market,
        days: 36,
        interval: '1d',
      })
      setMiniKlines((data?.klines || []).slice(-30))
    } catch {
      setMiniKlines([])
    } finally {
      if (!silent) setMiniKlineLoading(false)
    }
  }, [symbol, market])

  const loadSuggestions = useCallback(async () => {
    if (!symbol) return
    const data = await insightApi.suggestions<any[]>(symbol, {
      limit: 20,
      include_expired: includeExpiredSuggestions,
    })
    const list = (data || []).map(item => ({
      id: item.id,
      action: normalizeSuggestionAction(item.action, item.action_label),
      action_label: item.action_label || '',
      signal: pickSuggestionText(item.signal, 'signal'),
      reason: pickSuggestionText(item.reason, 'reason'),
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
  }, [symbol, includeExpiredSuggestions])

  const loadNews = useCallback(async () => {
    if (!symbol) return
    const runQuery = async (opts: { useName: boolean; filterRelated: boolean }) => {
      const params = new URLSearchParams()
      params.set('hours', newsHours)
      params.set('limit', '50')
      if (!opts.filterRelated) params.set('filter_related', 'false')
      if (opts.useName && resolvedName && resolvedName !== symbol) params.set('names', resolvedName)
      else params.set('symbols', symbol)
      return insightApi.news<NewsItem[]>(Object.fromEntries(params.entries()))
    }

    try {
      let data: NewsItem[] = await runQuery({ useName: true, filterRelated: true })
      if ((data || []).length === 0 && resolvedName && resolvedName !== symbol) {
        data = await runQuery({ useName: false, filterRelated: true })
      }
      if ((data || []).length === 0) {
        data = await runQuery({ useName: true, filterRelated: false })
      }
      if ((data || []).length === 0) {
        data = await runQuery({ useName: false, filterRelated: false })
      }
      if ((data || []).length === 0) {
        const global = await insightApi.news<NewsItem[]>({
          hours: newsHours,
          limit: 80,
        }).catch(() => [])
        const upperSymbol = symbol.toUpperCase()
        const name = (resolvedName || '').trim()
        data = (global || []).filter((n) => {
          const text = `${n.title || ''} ${n.content || ''}`.toUpperCase()
          if (upperSymbol && text.includes(upperSymbol)) return true
          if (name && `${n.title || ''} ${n.content || ''}`.includes(name)) return true
          return (n.symbols || []).map(x => String(x).toUpperCase()).includes(upperSymbol)
        })
      }
      // 兜底：实时新闻为空时，回退到 news_digest 历史快照中的新闻列表
      if ((data || []).length === 0) {
        const bySymbol = await insightApi.history<HistoryRecord[]>({
          agent_name: 'news_digest',
          stock_symbol: symbol,
          limit: 1,
        }).catch(() => [])
        let rec: HistoryRecord | null = (bySymbol || [])[0] || null
        if (!rec) {
          const globals = await insightApi.history<HistoryRecord[]>({
            agent_name: 'news_digest',
            stock_symbol: '*',
            limit: 20,
          }).catch(() => [])
          const upperSymbol = symbol.toUpperCase()
          const name = (resolvedName || '').trim()
          rec = (globals || []).find((r) => {
            const sug = r?.suggestions || {}
            const keys = Object.keys(sug || {})
            if (keys.includes(symbol) || keys.map(k => k.toUpperCase()).includes(upperSymbol)) return true
            const text = `${r?.title || ''}\n${r?.content || ''}`.toUpperCase()
            if (upperSymbol && text.includes(upperSymbol)) return true
            if (name && `${r?.title || ''}\n${r?.content || ''}`.includes(name)) return true
            return false
          }) || null
        }
        if (rec?.news && Array.isArray(rec.news)) {
          data = rec.news
            .map((n) => ({
              source: n.source || 'news_digest',
              source_label: n.source || 'news_digest',
              title: n.title || '',
              publish_time: n.publish_time || rec?.analysis_date || '',
              url: n.url || '',
            }))
            .filter((n) => !!n.title)
        }
      }
      setNews(data || [])
    } catch {
      setNews([])
    }
  }, [symbol, newsHours, resolvedName])

  const loadAnnouncements = useCallback(async () => {
    if (!symbol) return
    try {
      const runQuery = async (opts: { useName: boolean; filterRelated: boolean }) => {
        const params = new URLSearchParams()
        params.set('hours', announcementHours)
        params.set('limit', '50')
        if (!opts.filterRelated) params.set('filter_related', 'false')
        params.set('source', 'eastmoney')
        if (opts.useName && resolvedName && resolvedName !== symbol) params.set('names', resolvedName)
        else params.set('symbols', symbol)
        return insightApi.news<NewsItem[]>(Object.fromEntries(params.entries()))
      }
      let data: NewsItem[] = await runQuery({ useName: true, filterRelated: true })
      if ((data || []).length === 0 && resolvedName && resolvedName !== symbol) {
        data = await runQuery({ useName: false, filterRelated: true })
      }
      if ((data || []).length === 0) {
        data = await runQuery({ useName: true, filterRelated: false })
      }
      if ((data || []).length === 0) {
        data = await runQuery({ useName: false, filterRelated: false })
      }
      if ((data || []).length === 0) {
        const global = await insightApi.news<NewsItem[]>({
          hours: announcementHours,
          limit: 80,
          source: 'eastmoney',
        }).catch(() => [])
        const upperSymbol = symbol.toUpperCase()
        const name = (resolvedName || '').trim()
        data = (global || []).filter((n) => {
          const text = `${n.title || ''} ${n.content || ''}`.toUpperCase()
          if (upperSymbol && text.includes(upperSymbol)) return true
          if (name && `${n.title || ''} ${n.content || ''}`.includes(name)) return true
          return (n.symbols || []).map(x => String(x).toUpperCase()).includes(upperSymbol)
        })
      }
      setAnnouncements(data || [])
    } catch {
      setAnnouncements([])
    }
  }, [symbol, announcementHours, resolvedName])

  const loadHoldingAgg = useCallback(async () => {
    if (!symbol) return
    setHoldingLoaded(false)
    setHoldingLoadError(false)
    try {
      const data = await insightApi.portfolioSummary<PortfolioSummaryResponse>({ include_quotes: true })
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
      if (quantity > 0) setHoldingAgg({ quantity, cost, unitCost: cost / quantity, marketValue, pnl })
      else setHoldingAgg(null)
    } catch {
      setHoldingAgg(null)
      setHoldingLoadError(true)
    } finally {
      setHoldingLoaded(true)
    }
  }, [symbol, market])

  const loadReports = useCallback(async () => {
    if (!symbol) return
    try {
      const agents = ['premarket_outlook', 'daily_report', 'news_digest']
      const bySymbolResults = await Promise.all(
        agents.map(agent =>
          insightApi.history<HistoryRecord[]>({
            agent_name: agent,
            stock_symbol: symbol,
            limit: 1,
          }).catch(() => [])
        )
      )
      let merged = bySymbolResults
        .flatMap(items => items || [])
        .filter(Boolean)
      // 兼容全局记录（stock_symbol="*"）场景：从最近全局记录中筛选与当前股票相关的报告。
      if (merged.length === 0) {
        const globalResults = await Promise.all(
          agents.map(agent =>
            insightApi.history<HistoryRecord[]>({
              agent_name: agent,
              stock_symbol: '*',
              limit: 20,
            }).catch(() => [])
          )
        )
        const upperSymbol = symbol.toUpperCase()
        const name = (resolvedName || '').trim()
        merged = globalResults
          .map(items => {
            const rows = (items || []).filter(Boolean)
            const hit = rows.find((r) => {
              const sug = r?.suggestions || {}
              const keys = Object.keys(sug || {})
              if (keys.includes(symbol) || keys.map(k => k.toUpperCase()).includes(upperSymbol)) return true
              const text = `${r?.title || ''}\n${r?.content || ''}`.toUpperCase()
              if (upperSymbol && text.includes(upperSymbol)) return true
              if (name && `${r?.title || ''}\n${r?.content || ''}`.includes(name)) return true
              return false
            })
            return hit || null
          })
          .filter(Boolean) as HistoryRecord[]
      }
      merged = merged.sort((a, b) => {
        const am = parseToMs(a.created_at || a.analysis_date) || 0
        const bm = parseToMs(b.created_at || b.analysis_date) || 0
        return bm - am
      })
      setReports(merged)
    } catch {
      setReports([])
    }
  }, [symbol, resolvedName])

  const loadCore = useCallback(async () => {
    if (!symbol) return
    setLoading(true)
    try {
      await Promise.allSettled([loadQuote(), loadKline(), loadMiniKline(), loadHoldingAgg()])
    } catch (e) {
      toast(e instanceof Error ? e.message : '加载失败', 'error')
    } finally {
      setLoading(false)
    }
  }, [symbol, loadQuote, loadKline, loadMiniKline, loadHoldingAgg, toast])

  const handleRefreshAll = useCallback(async () => {
    if (!symbol) return
    setLoading(true)
    try {
      await Promise.allSettled([loadQuote(), loadKline(), loadMiniKline(), loadSuggestions(), loadNews(), loadAnnouncements(), loadHoldingAgg(), loadReports()])
    } catch (e) {
      toast(e instanceof Error ? e.message : '加载失败', 'error')
    } finally {
      setLoading(false)
    }
  }, [symbol, loadQuote, loadKline, loadMiniKline, loadSuggestions, loadNews, loadAnnouncements, loadHoldingAgg, loadReports, toast])

  const refreshForAuto = useCallback(async () => {
    if (!symbol) return
    const tasks: Promise<any>[] = [loadQuote(), loadHoldingAgg()]
    if (tab === 'overview' || tab === 'kline') {
      tasks.push(loadKline(), loadMiniKline({ silent: true }))
    }
    if (tab === 'overview' || tab === 'suggestions') {
      tasks.push(loadSuggestions())
    }
    if (tab === 'overview' || tab === 'news') {
      tasks.push(loadNews())
    }
    if (tab === 'overview' || tab === 'announcements') {
      tasks.push(loadAnnouncements())
    }
    if (tab === 'overview' || tab === 'reports') {
      tasks.push(loadReports())
    }
    await Promise.allSettled(tasks)
  }, [symbol, tab, loadQuote, loadHoldingAgg, loadKline, loadMiniKline, loadSuggestions, loadNews, loadAnnouncements, loadReports])

  useEffect(() => {
    if (!props.open || !symbol) return
    setTab('overview')
    setSuggestions([])
    setNews([])
    setAnnouncements([])
    setReports([])
    setMiniKlines([])
    setWatchingStock(null)
    loadCore()
  }, [props.open, symbol, market, loadCore])

  useEffect(() => {
    if (!props.open || !symbol) return
    let cancelled = false
    ;(async () => {
      try {
        const key = `${market}:${symbol}`
        const stocks = await stocksApi.list()
        if (cancelled) return
        const found = (stocks || []).find(s => s.symbol === symbol && s.market === market) || null
        if (found) {
          stockCacheRef.current[key] = found
        } else {
          delete stockCacheRef.current[key]
        }
        setWatchingStock(found)
      } catch {
        if (!cancelled) setWatchingStock(null)
      }
    })()
    return () => { cancelled = true }
  }, [props.open, symbol, market])

  useEffect(() => {
    if (!props.open || !symbol) return
    loadNews().catch(() => setNews([]))
  }, [props.open, symbol, newsHours, loadNews])

  useEffect(() => {
    if (!props.open || !symbol) return
    loadAnnouncements().catch(() => setAnnouncements([]))
  }, [props.open, symbol, announcementHours, loadAnnouncements])

  useEffect(() => {
    if (!props.open || !symbol) return
    loadSuggestions().catch(() => setSuggestions([]))
  }, [props.open, symbol, includeExpiredSuggestions, loadSuggestions])

  useEffect(() => {
    if (!props.open || !symbol) return
    loadReports().catch(() => setReports([]))
  }, [props.open, symbol, loadReports])

  useEffect(() => {
    if (!props.open || !symbol || !autoRefreshEnabled) return
    const sec = Number(autoRefreshSec) > 0 ? Number(autoRefreshSec) : 20
    const ms = Math.max(10, sec) * 1000
    const timer = setInterval(() => {
      refreshForAuto().catch(() => undefined)
    }, ms)
    return () => clearInterval(timer)
  }, [props.open, symbol, autoRefreshEnabled, autoRefreshSec, refreshForAuto])

  const hasHolding = !!props.hasPosition || !!holdingAgg
  const technicalScored = useMemo(() => {
    if (!klineSummary) return null
    return buildKlineSuggestion(klineSummary as any, hasHolding)
  }, [klineSummary, hasHolding])
  const technicalFallbackSuggestion = useMemo<SuggestionInfo | null>(() => {
    if (!klineSummary || !technicalScored) return null
    const topEvidence = (technicalScored.evidence || []).filter(e => e.delta !== 0).slice(0, 3).map(e => e.text)
    return {
      action: technicalScored.action,
      action_label: technicalScored.action_label,
      signal: technicalScored.signal || '技术面中性',
      reason: topEvidence.length > 0 ? topEvidence.join('；') : '基于K线技术指标自动生成的基础建议',
      should_alert: technicalScored.action === 'buy' || technicalScored.action === 'add' || technicalScored.action === 'sell' || technicalScored.action === 'reduce',
      agent_name: 'technical_fallback',
      agent_label: '技术指标',
      created_at: new Date().toISOString(),
      is_expired: false,
      meta: {
        fallback: true,
        score: technicalScored.score,
        evidence_count: technicalScored.evidence?.length || 0,
      },
    }
  }, [klineSummary, technicalScored])
  const quoteUp = (quote?.change_pct || 0) > 0
  const quoteDown = (quote?.change_pct || 0) < 0
  const changeColor = quoteUp ? 'text-rose-500' : quoteDown ? 'text-emerald-500' : 'text-foreground'
  const priceColor = quoteUp ? 'text-rose-500' : quoteDown ? 'text-emerald-500' : 'text-foreground'
  const levelColor = (value: number | null | undefined) => {
    if (value == null || quote?.prev_close == null) return 'text-foreground'
    if (value > quote.prev_close) return 'text-rose-500'
    if (value < quote.prev_close) return 'text-emerald-500'
    return 'text-foreground'
  }
  const badge = getMarketBadge(market)
  const amplitudePct = useMemo(() => {
    const hi = quote?.high_price
    const lo = quote?.low_price
    const pre = quote?.prev_close
    if (hi == null || lo == null || pre == null || pre === 0) return null
    return ((hi - lo) / pre) * 100
  }, [quote?.high_price, quote?.low_price, quote?.prev_close])

  const reportMap = useMemo(() => {
    const out: Record<string, HistoryRecord | null> = {
      premarket_outlook: null,
      daily_report: null,
      news_digest: null,
    }
    for (const r of reports) {
      if (!out[r.agent_name]) out[r.agent_name] = r
    }
    return out
  }, [reports])
  const activeReport = reportMap[reportTab]
  const latestReport = reports[0] || null
  const latestShareSuggestion = suggestions[0] || technicalFallbackSuggestion
  const shareCardPayload = useMemo(() => {
    const jsonSources = [
      parseSuggestionJson((latestShareSuggestion as any)?.signal),
      parseSuggestionJson((latestShareSuggestion as any)?.reason),
      parseSuggestionJson((latestShareSuggestion as any)?.raw),
      parseSuggestionJson((latestShareSuggestion as any)?.ai_response),
      parseSuggestionJson((latestShareSuggestion as any)?.prompt_context),
      (latestShareSuggestion as any)?.meta && typeof (latestShareSuggestion as any).meta === 'object'
        ? ((latestShareSuggestion as any).meta as Record<string, any>)
        : null,
    ].filter(Boolean) as Array<Record<string, any>>
    const pickFromJson = (...keys: string[]) => {
      for (const obj of jsonSources) {
        for (const key of keys) {
          const s = String(obj?.[key] || '').trim()
          if (s) return s
        }
      }
      return ''
    }
    const pickListFromJson = (...keys: string[]) => {
      for (const obj of jsonSources) {
        for (const key of keys) {
          const list = normalizeTextList(obj?.[key])
          if (list.length > 0) return list
        }
      }
      return [] as string[]
    }
    const marketLabel = badge.label
    const price = quote?.current_price != null ? formatNumber(quote.current_price) : '--'
    const chg = quote?.change_pct != null ? `${quote.change_pct >= 0 ? '+' : ''}${quote.change_pct.toFixed(2)}%` : '--'
    const action = latestShareSuggestion?.action_label || latestShareSuggestion?.action || '暂无'
    const signal = firstNonEmptyText(
      latestShareSuggestion?.signal,
      pickFromJson('signal', 'summary', 'core_view'),
      technicalScored?.signal,
      '技术面中性'
    ) || '--'
    const reason = firstNonEmptyText(
      latestShareSuggestion?.reason,
      pickFromJson('reason', 'thesis', 'core_judgement', 'core_judgment', 'analysis'),
      technicalFallbackSuggestion?.reason,
      '暂无'
    ) || '--'
    const risksList = [
      ...normalizeTextList((latestShareSuggestion as any)?.meta?.risks),
      ...pickListFromJson('risks', 'risk', 'risk_points'),
      ...buildShareTechnicalRisks(klineSummary),
    ].filter(Boolean)
    const dedupRisks = Array.from(new Set(risksList))
    const risks = dedupRisks.length > 0 ? dedupRisks.slice(0, 2).join('；') : '市场波动风险'
    const triggerList = pickListFromJson('triggers', 'trigger', 'signals')
    const invalidList = pickListFromJson('invalidations', 'invalidation', 'stop_conditions')
    const trigger = triggerList.length > 0 ? triggerList.slice(0, 2).join('；') : '--'
    const invalidation = invalidList.length > 0 ? invalidList.slice(0, 2).join('；') : '--'
    const technicalBrief = firstNonEmptyText(
      [klineSummary?.trend, klineSummary?.macd_status, klineSummary?.rsi_status].filter(Boolean).join(' / '),
      technicalScored?.signal
    ) || '--'
    const levelsBrief = (klineSummary?.support != null && klineSummary?.resistance != null)
      ? `支撑 ${formatNumber(klineSummary.support)} / 压力 ${formatNumber(klineSummary.resistance)}`
      : '--'
    const source = latestShareSuggestion?.agent_label || latestShareSuggestion?.agent_name || '技术指标'
    const ts = new Date().toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
    return { marketLabel, price, chg, action, signal, reason, risks, trigger, invalidation, technicalBrief, levelsBrief, source, ts }
  }, [badge.label, klineSummary, latestShareSuggestion, quote?.change_pct, quote?.current_price, technicalFallbackSuggestion?.reason, technicalScored?.signal])

  const shareText = useMemo(() => {
    const { marketLabel, price, chg, action, signal, reason, risks, trigger, invalidation, technicalBrief, levelsBrief, source, ts } = shareCardPayload
    const lines = [
      `【PanWatch 洞察】${resolvedName}（${symbol} · ${marketLabel}）`,
      `时间：${ts}`,
      `现价：${price}（${chg}）`,
      `建议：${action}`,
      `信号：${signal}`,
      `理由：${reason}`,
      `风险：${risks}`,
      `技术：${technicalBrief}`,
      `关键位：${levelsBrief}`,
      `来源：${source}`,
    ]
    if (trigger !== '--') lines.splice(7, 0, `触发：${trigger}`)
    if (invalidation !== '--') lines.splice(8, 0, `失效：${invalidation}`)
    return lines.join('\n')
  }, [shareCardPayload, resolvedName, symbol])

  const handleExportShareImage = useCallback(async () => {
    const esc = (s: string) => String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&apos;')
    const trim = (s: string, n = 42) => {
      const x = String(s || '')
      return x.length > n ? `${x.slice(0, n - 1)}…` : x
    }

    setImageExporting(true)
    try {
      const { marketLabel, price, chg, action, signal, reason, risks, technicalBrief, levelsBrief, source, ts } = shareCardPayload
      const up = (quote?.change_pct || 0) >= 0
      const changeColor = up ? '#ef4444' : '#10b981'
      const svg = `
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0b1220"/>
      <stop offset="100%" stop-color="#111827"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="1200" height="630" fill="url(#bg)"/>
  <rect x="40" y="30" width="1120" height="570" rx="22" fill="#0f172a" stroke="#1f2937"/>
  <text x="76" y="104" fill="#93c5fd" font-size="26" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">PanWatch 洞察</text>
  <text x="76" y="150" fill="#f8fafc" font-size="42" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(`${resolvedName}（${symbol} · ${marketLabel}）`, 28))}</text>
  <text x="76" y="198" fill="#94a3b8" font-size="22" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(ts)}</text>

  <text x="76" y="284" fill="#94a3b8" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">现价</text>
  <text x="180" y="284" fill="#f8fafc" font-size="52" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(price)}</text>
  <text x="380" y="284" fill="${changeColor}" font-size="36" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(chg)}</text>

  <text x="76" y="352" fill="#94a3b8" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">建议</text>
  <text x="180" y="352" fill="#22d3ee" font-size="34" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(action, 20))}</text>

  <text x="76" y="412" fill="#94a3b8" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">信号</text>
  <text x="180" y="412" fill="#e2e8f0" font-size="26" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(signal, 46))}</text>

  <text x="76" y="466" fill="#94a3b8" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">理由</text>
  <text x="180" y="466" fill="#cbd5e1" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(reason, 52))}</text>

  <text x="76" y="520" fill="#94a3b8" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">风险</text>
  <text x="180" y="520" fill="#cbd5e1" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(risks, 52))}</text>

  <text x="76" y="560" fill="#94a3b8" font-size="22" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">技术</text>
  <text x="180" y="560" fill="#cbd5e1" font-size="21" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(technicalBrief, 58))}</text>
  <text x="76" y="590" fill="#94a3b8" font-size="22" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">关键位</text>
  <text x="180" y="590" fill="#cbd5e1" font-size="21" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(levelsBrief, 58))}</text>
  <text x="76" y="618" fill="#64748b" font-size="18" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">来源：${esc(source)} · 仅供参考，不构成投资建议</text>
</svg>`

      const blob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const img = await new Promise<HTMLImageElement>((resolve, reject) => {
        const el = new Image()
        el.onload = () => resolve(el)
        el.onerror = reject
        el.src = url
      })
      const canvas = document.createElement('canvas')
      canvas.width = 1200
      canvas.height = 630
      const ctx = canvas.getContext('2d')
      if (!ctx) throw new Error('无法创建画布')
      ctx.drawImage(img, 0, 0)
      URL.revokeObjectURL(url)
      const png = canvas.toDataURL('image/png')
      const a = document.createElement('a')
      a.href = png
      a.download = `panwatch-${symbol}-${Date.now()}.png`
      a.click()
      toast('分享图片已生成并下载', 'success')
    } catch {
      toast('图片生成失败，请稍后重试', 'error')
    } finally {
      setImageExporting(false)
    }
  }, [quote?.change_pct, resolvedName, shareCardPayload, symbol, toast])

  const copyTextWithFallback = useCallback(async (text: string): Promise<boolean> => {
    if (!text) return false

    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text)
        return true
      } catch {
        // Fallback to legacy copy below.
      }
    }

    if (typeof document !== 'undefined') {
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.setAttribute('readonly', '')
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      textarea.style.pointerEvents = 'none'
      textarea.style.left = '-9999px'
      document.body.appendChild(textarea)
      try {
        textarea.focus()
        textarea.select()
        textarea.setSelectionRange(0, textarea.value.length)
        return !!document.execCommand?.('copy')
      } catch {
        return false
      } finally {
        document.body.removeChild(textarea)
      }
    }
    return false
  }, [])

  const handleCopyShareText = useCallback(async () => {
    try {
      const copied = await copyTextWithFallback(shareText)
      if (copied) {
        toast('洞察内容已复制', 'success')
      } else {
        toast('复制失败，请优先使用“图片”分享', 'error')
      }
    } catch {
      toast('复制失败，请优先使用“图片”分享', 'error')
    }
  }, [copyTextWithFallback, shareText, toast])

  const handleShareInsight = useCallback(async () => {
    try {
      if (typeof navigator !== 'undefined' && (navigator as any).share) {
        await (navigator as any).share({
          title: `${resolvedName} 洞察`,
          text: shareText,
        })
        return
      }
      const copied = await copyTextWithFallback(shareText)
      if (copied) {
        toast('当前环境不支持系统分享，已自动复制内容', 'success')
      } else {
        toast('当前环境不支持分享且复制失败，请使用“图片”分享', 'error')
      }
    } catch (e: any) {
      if (e?.name === 'AbortError') return
      const copied = await copyTextWithFallback(shareText)
      if (copied) {
        toast('分享失败，已自动复制内容', 'success')
      } else {
        toast('分享失败且复制失败，请使用“图片”分享', 'error')
      }
    }
  }, [copyTextWithFallback, resolvedName, shareText, toast])

  const handleSetAlert = async () => {
    if (!symbol) return
    setAlerting(true)
    try {
      const stocks = await stocksApi.list()
      let stock = (stocks || []).find(s => s.symbol === symbol && s.market === market) || null
      if (!stock) {
        stock = await stocksApi.create({ symbol, name: resolvedName || symbol, market })
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

      await stocksApi.updateAgents(stock.id, { agents: nextAgents })
      await stocksApi.triggerAgent(stock.id, 'intraday_monitor', {
        bypass_throttle: true,
        bypass_market_hours: true,
      })
      toast('已设置提醒并触发一次盘中监测', 'success')
      await Promise.allSettled([loadSuggestions()])
    } catch (e) {
      toast(e instanceof Error ? e.message : '设置提醒失败', 'error')
    } finally {
      setAlerting(false)
    }
  }

  const toggleWatch = useCallback(async () => {
    if (!symbol) return
    if (watchingStock && hasHolding) {
      toast('该股票存在持仓，请先删除持仓后再取消关注', 'error')
      return
    }

    setWatchToggleLoading(true)
    try {
      if (watchingStock) {
        await stocksApi.remove(watchingStock.id)
        setWatchingStock(null)
        delete stockCacheRef.current[`${market}:${symbol}`]
        toast('已取消关注', 'success')
      } else {
        const created = await stocksApi.create({ symbol, name: resolvedName || symbol, market })
        setWatchingStock(created)
        stockCacheRef.current[`${market}:${symbol}`] = created
        toast('已添加关注', 'success')
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : '操作失败', 'error')
    } finally {
      setWatchToggleLoading(false)
    }
  }, [hasHolding, market, resolvedName, symbol, toast, watchingStock])

  const triggerAutoAiSuggestion = useCallback(async () => {
    // 自动建议仅针对“确认未持仓”的股票，且不自动创建股票/绑定 Agent。
    if (!symbol || !market || !holdingLoaded || holdingLoadError || hasHolding || autoSuggesting) return
    const key = `${market}:${symbol}`
    const lastTs = autoTriggeredRef.current[key] || 0
    if (Date.now() - lastTs < 5 * 60 * 1000) return
    autoTriggeredRef.current[key] = Date.now()
    setAutoSuggesting(true)
    try {
      // intraday_monitor 较 chart_analyst 更轻量、稳定，不依赖截图链路
      await stocksApi.triggerAgent(0, 'intraday_monitor', {
        allow_unbound: true,
        symbol,
        market,
        name: resolvedName || symbol,
        bypass_throttle: true,
        bypass_market_hours: true,
      })
      await Promise.allSettled([loadSuggestions()])
    } catch (e) {
      toast(
        e instanceof Error ? e.message : '自动 AI 建议触发失败，可点击「一键设提醒」重试',
        'error'
      )
    } finally {
      setAutoSuggesting(false)
    }
  }, [symbol, market, resolvedName, holdingLoaded, holdingLoadError, hasHolding, autoSuggesting, loadSuggestions, toast])

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

  return (
    <>
      <Dialog open={props.open} onOpenChange={props.onOpenChange}>
        <DialogContent className="w-[92vw] max-w-6xl p-5 md:p-6 overflow-x-hidden">
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
                <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => handleExportShareImage()} disabled={imageExporting}>
                  <Download className={`w-3.5 h-3.5 ${imageExporting ? 'animate-pulse' : ''}`} />
                  <span className="hidden sm:inline">{imageExporting ? '生成中' : '图片'}</span>
                </Button>
                <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => handleShareInsight()}>
                  <Share2 className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline">分享</span>
                </Button>
                <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => handleCopyShareText()}>
                  <Copy className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline">复制</span>
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  className="h-8 px-2.5"
                  onClick={toggleWatch}
                  disabled={watchToggleLoading || (hasHolding && !!watchingStock)}
                  title={hasHolding && watchingStock ? '持仓中的股票无法取消关注' : undefined}
                >
                  {watchToggleLoading ? '处理中...' : (watchingStock ? (hasHolding ? '持仓中' : '取消关注') : '快速关注')}
                </Button>
                <StockPriceAlertPanel mode="inline" symbol={symbol} market={market} stockName={resolvedName} />
                <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={handleSetAlert} disabled={alerting}>
                  {alerting ? '设置中...' : '一键设提醒'}
                </Button>
                <Button variant="outline" size="sm" className="h-8 px-2.5" onClick={() => handleRefreshAll()} disabled={loading}>
                  <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
                </Button>
              </div>
            </div>
          </DialogHeader>

          <div className="flex items-center justify-between gap-2 flex-wrap mb-3">
            <div className="flex items-center gap-1 flex-wrap">
              {[
                { id: 'overview', label: '概览' },
                { id: 'suggestions', label: `建议 (${suggestions.length})` },
                { id: 'reports', label: `报告 (${reports.length})` },
                { id: 'kline', label: 'K线' },
                { id: 'announcements', label: `公告 (${announcements.length})` },
                { id: 'news', label: `新闻 (${news.length})` },
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
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-muted-foreground">自动刷新</span>
              <Switch
                checked={autoRefreshEnabled}
                onCheckedChange={setAutoRefreshEnabled}
                aria-label="自动刷新"
              />
              <Select value={String(autoRefreshSec)} onValueChange={(v) => setAutoRefreshSec(Number(v))}>
                <SelectTrigger className="h-7 w-[84px] text-[11px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="10">10秒</SelectItem>
                  <SelectItem value="20">20秒</SelectItem>
                  <SelectItem value="30">30秒</SelectItem>
                  <SelectItem value="60">60秒</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="max-h-[68vh] overflow-y-auto overflow-x-hidden pr-1 scrollbar">
            {tab === 'overview' && (
              <div className="space-y-3">
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 items-stretch">
                  <div className="card p-4 h-full">
                    <div className="mt-1 flex items-end justify-between gap-3">
                      <div className={`text-[34px] leading-none font-bold font-mono ${priceColor}`}>
                        {quote?.current_price != null ? formatNumber(quote.current_price) : '--'}
                      </div>
                      <div className={`text-[16px] font-mono ${changeColor}`}>
                        {quote?.change_pct != null ? `${quote.change_pct >= 0 ? '+' : ''}${quote.change_pct.toFixed(2)}%` : '--'}
                      </div>
                    </div>
                    <div className="mt-3 grid grid-cols-3 gap-2 text-[12px]">
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">今开</div><div className={`font-mono ${levelColor(quote?.open_price)}`}>{formatNumber(quote?.open_price)}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">最高</div><div className={`font-mono ${levelColor(quote?.high_price)}`}>{formatNumber(quote?.high_price)}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">最低</div><div className={`font-mono ${levelColor(quote?.low_price)}`}>{formatNumber(quote?.low_price)}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">成交量</div><div className="font-mono">{formatCompactNumber(quote?.volume)}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">成交额</div><div className="font-mono">{formatCompactNumber(quote?.turnover)}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">振幅</div><div className="font-mono">{amplitudePct != null ? `${amplitudePct.toFixed(2)}%` : '--'}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">换手率</div><div className="font-mono">{quote?.turnover_rate != null ? `${Number(quote.turnover_rate).toFixed(2)}%` : '--'}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">市盈率</div><div className="font-mono">{quote?.pe_ratio != null ? Number(quote.pe_ratio).toFixed(2) : '--'}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">总市值</div><div className="font-mono">{formatMarketCap(quote?.total_market_value, market)}</div></div>
                    </div>
                    <div className="mt-3 border-t border-border/50 pt-3">
                      <div className="text-[11px] text-muted-foreground mb-2">持仓信息</div>
                      {holdingAgg ? (
                        <div className="grid grid-cols-2 gap-2 text-[12px]">
                          <div className="rounded bg-emerald-500/10 px-2 py-1.5">
                            <div className="text-[10px] text-muted-foreground">持仓数量</div>
                            <div className="font-mono">{holdingAgg.quantity}</div>
                          </div>
                          <div className="rounded bg-emerald-500/10 px-2 py-1.5">
                            <div className="text-[10px] text-muted-foreground">持仓成本(单价)</div>
                            <div
                              className={`font-mono ${
                                quote?.current_price != null
                                  ? quote.current_price > holdingAgg.unitCost
                                    ? 'text-rose-500'
                                    : quote.current_price < holdingAgg.unitCost
                                      ? 'text-emerald-500'
                                      : 'text-foreground'
                                  : 'text-foreground'
                              }`}
                            >
                              {formatNumber(holdingAgg.unitCost)}
                            </div>
                          </div>
                          <div className="rounded bg-emerald-500/10 px-2 py-1.5">
                            <div className="text-[10px] text-muted-foreground">持仓市值</div>
                            <div className="font-mono">{formatCompactNumber(holdingAgg.marketValue)}</div>
                          </div>
                          <div className="rounded bg-emerald-500/10 px-2 py-1.5">
                            <div className="text-[10px] text-muted-foreground">总盈亏</div>
                            <div className={`font-mono ${holdingAgg.pnl >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                              {holdingAgg.pnl >= 0 ? '+' : ''}{formatCompactNumber(holdingAgg.pnl)}
                            </div>
                          </div>
                        </div>
                      ) : (
                        <div className="text-[11px] text-muted-foreground">未在持仓中</div>
                      )}
                    </div>
                  </div>

                  <div className="card p-4 h-full">
                    <div className="text-[12px] text-muted-foreground mb-2">迷你K线</div>
                    {!klineSummary ? (
                      <div className="text-[12px] text-muted-foreground py-8">暂无K线摘要</div>
                    ) : (
                      <>
                        {miniKlineLoading ? (
                          <div className="h-32 rounded bg-accent/30 animate-pulse" />
                        ) : miniKlines.length > 0 && miniKlineExtrema ? (
                          <svg
                            viewBox="0 0 320 120"
                            className="w-full h-32 cursor-pointer"
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
                            <title>点击进入交互式K线</title>
                            {miniKlines.map((k, idx) => {
                              const xStep = 320 / miniKlines.length
                              const x = xStep * idx + xStep / 2
                              const bodyW = Math.max(2, xStep * 0.5)
                              const toY = (v: number) => 114 - ((v - miniKlineExtrema.low) / (miniKlineExtrema.high - miniKlineExtrema.low)) * 100
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
                                  {active && <rect x={x - xStep / 2} y={6} width={xStep} height={108} fill="rgba(59,130,246,0.10)" />}
                                  <line x1={x} y1={yHigh} x2={x} y2={yLow} stroke={color} strokeWidth="1" />
                                  <rect x={x - bodyW / 2} y={bodyTop} width={bodyW} height={bodyH} fill={color} rx="0.6" />
                                </g>
                              )
                            })}
                          </svg>
                        ) : (
                          <div className="h-32 text-[11px] text-muted-foreground flex items-center justify-center">暂无迷你K线</div>
                        )}
                        <div className="mt-2 rounded bg-accent/10 p-2.5">
                          <TechnicalIndicatorStrip
                            klineSummary={klineSummary}
                            technicalSuggestion={technicalFallbackSuggestion}
                            stockName={resolvedName}
                            stockSymbol={symbol}
                            market={market}
                            hasPosition={!!props.hasPosition}
                            score={Number(technicalScored?.score ?? 0)}
                            evidence={technicalScored?.evidence || []}
                          />
                        </div>
                      </>
                    )}
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 items-stretch">
                  <div className="card p-4 h-full flex flex-col">
                    <div className="flex items-center justify-between mb-2">
                      <div className="text-[12px] text-muted-foreground">AI建议</div>
                      <Button variant="ghost" size="sm" className="h-7 px-2 text-[11px] text-muted-foreground" onClick={() => setTab('suggestions')}>
                        更多
                      </Button>
                      {autoSuggesting && suggestions.length > 0 && (
                        <div className="text-[10px] text-primary">更新中...</div>
                      )}
                    </div>
                    {suggestions.length > 0 ? (
                      <div className="space-y-2">
                        <SuggestionBadge
                          suggestion={suggestions[0]}
                          stockName={resolvedName}
                          stockSymbol={symbol}
                          market={market}
                          hasPosition={!!props.hasPosition}
                          showTechnicalCompanion={false}
                        />
                        <div className="rounded bg-accent/10 p-2 text-[11px]">
                          <div className="text-muted-foreground">核心判断</div>
                          <div className="mt-1 text-foreground line-clamp-2">{suggestions[0].signal || suggestions[0].reason || '暂无说明'}</div>
                          <div className="mt-1 text-muted-foreground">动作: {suggestions[0].action_label || suggestions[0].action || '--'}</div>
                          <div className="mt-1 text-foreground line-clamp-2">依据: {suggestions[0].reason || '暂无补充依据'}</div>
                          <div className="mt-1 text-muted-foreground">
                            来源: {suggestions[0].agent_label || suggestions[0].agent_name || 'AI'}{suggestions[0].created_at ? ` · ${formatTime(suggestions[0].created_at)}` : ''}
                          </div>
                        </div>
                        {suggestions.length > 1 && (
                          <div className="rounded bg-accent/10 p-2 text-[11px]">
                            <div className="text-muted-foreground mb-1">近期补充建议</div>
                            {suggestions.slice(1, 3).map((item, idx) => (
                              <div key={`${item.created_at || 'extra'}-${idx}`} className="line-clamp-1 text-foreground">
                                {item.action_label || item.action} · {item.signal || item.reason || '--'}
                              </div>
                            ))}
                          </div>
                        )}
                        <div className="text-[10px] text-primary min-h-[14px]">{autoSuggesting && suggestions.length === 0 ? '正在自动生成 AI 建议...' : ''}</div>
                      </div>
                    ) : (
                      <div className="text-[12px] text-muted-foreground py-6">
                        {autoSuggesting ? '正在自动生成 AI 建议（通常 5-15 秒）...' : '暂无 AI 建议'}
                      </div>
                    )}
                  </div>

                  <div className="card p-4 h-full flex flex-col">
                    <div className="flex items-center justify-between mb-2">
                      <div className="text-[12px] text-muted-foreground">新闻</div>
                      <Button variant="ghost" size="sm" className="h-7 px-2 text-[11px] text-muted-foreground" onClick={() => setTab('news')}>
                        更多
                      </Button>
                    </div>
                    <div className="flex-1 space-y-2">
                      {news.length === 0 ? (
                        <div className="text-[12px] text-muted-foreground py-6">暂无相关新闻</div>
                      ) : (
                        news.slice(0, 3).map((item, idx) => (
                          <a
                            key={`${item.publish_time || 'n'}-${idx}`}
                            href={item.url}
                            target="_blank"
                            rel="noreferrer"
                            className="block rounded-lg border border-border/30 bg-accent/10 p-2.5 hover:bg-accent/20 transition-colors"
                          >
                            <div className="text-[12px] text-foreground line-clamp-2">{item.title}</div>
                            <div className="mt-1 text-[10px] text-muted-foreground">{item.source_label || item.source} · {formatTime(item.publish_time)}</div>
                          </a>
                        ))
                      )}
                    </div>
                  </div>
                  <div className="card p-4 h-full flex flex-col">
                    <div className="flex items-center justify-between gap-2 mb-2">
                      <div className="text-[12px] text-muted-foreground">AI报告</div>
                      <Button variant="ghost" size="sm" className="h-7 px-2 text-[11px] text-muted-foreground" onClick={() => setTab('reports')}>
                        更多
                      </Button>
                    </div>
                    {!latestReport ? (
                      <div className="text-[12px] text-muted-foreground py-3">暂无报告</div>
                    ) : (
                      <div className="rounded-lg border border-border/30 bg-accent/10 p-2.5">
                        <div className="text-[11px] text-muted-foreground">
                          {AGENT_LABELS[latestReport.agent_name] || latestReport.agent_name} · {latestReport.analysis_date}
                        </div>
                        <div className="mt-1 text-[13px] font-medium line-clamp-1">{latestReport.title || '报告摘要'}</div>
                        <div className="mt-1 text-[12px] text-foreground/90 line-clamp-3">
                          {markdownToPlainText(latestReport.content) || '暂无报告内容'}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            {tab === 'kline' && (
              <div className="card p-4">
                <InteractiveKline
                  symbol={symbol}
                  market={market}
                  initialInterval={klineInterval}
                  initialDays={klineDays}
                />
              </div>
            )}

            {tab === 'reports' && (
              <div className="space-y-3">
                <div className="card p-3">
                  <div className="flex items-center gap-1">
                    {([
                      { key: 'premarket_outlook', label: '盘前' },
                      { key: 'daily_report', label: '盘后' },
                      { key: 'news_digest', label: '新闻' },
                    ] as const).map(item => (
                      <button
                        key={item.key}
                        onClick={() => setReportTab(item.key)}
                        className={`text-[11px] px-2.5 py-1 rounded ${
                          reportTab === item.key ? 'bg-primary text-primary-foreground' : 'bg-accent/60 text-muted-foreground hover:bg-accent'
                        }`}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                </div>
                {!activeReport ? (
                  <div className="card p-6 text-[12px] text-muted-foreground text-center">暂无报告</div>
                ) : (
                  <div className="card p-4 space-y-3">
                    <div className="text-[11px] text-muted-foreground">
                      {AGENT_LABELS[activeReport.agent_name] || activeReport.agent_name} · {activeReport.analysis_date}
                    </div>
                    <div className="text-[15px] font-medium">{activeReport.title || '报告摘要'}</div>
                    {activeReport.suggestions && (activeReport.suggestions as any)?.[symbol]?.action_label && (
                      <div className="text-[11px] inline-flex px-2 py-0.5 rounded bg-primary/10 text-primary">
                        {(activeReport.suggestions as any)[symbol].action_label}
                      </div>
                    )}
                    <div className="rounded-lg bg-accent/10 p-3">
                      <div className="prose prose-sm dark:prose-invert max-w-none text-foreground/90 break-words">
                        <ReactMarkdown>{activeReport.content || '暂无报告内容'}</ReactMarkdown>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}

            {tab === 'suggestions' && (
              <div className="space-y-3">
                <div className="card p-3 flex items-center justify-between gap-3">
                  <div className="text-[12px] text-muted-foreground">显示过期建议</div>
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] text-muted-foreground">{includeExpiredSuggestions ? '包含过期' : '仅有效'}</span>
                    <Switch
                      checked={includeExpiredSuggestions}
                      onCheckedChange={setIncludeExpiredSuggestions}
                      aria-label="显示过期建议"
                    />
                  </div>
                </div>
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
                  <div className="max-h-[56vh] overflow-y-auto pr-1 scrollbar space-y-3">
                    {suggestions.map((item, idx) => (
                      <div key={`${item.created_at || 's'}-${idx}`} className="card p-4">
                        <SuggestionBadge suggestion={item} stockName={resolvedName} stockSymbol={symbol} kline={klineSummary} hasPosition={!!props.hasPosition} />
                      </div>
                    ))}
                  </div>
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
                      <SelectItem value="168">近7天</SelectItem>
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

            {tab === 'announcements' && (
              <div className="space-y-3">
                <div className="flex items-center justify-end">
                  <Select value={announcementHours} onValueChange={setAnnouncementHours}>
                    <SelectTrigger className="h-8 w-[110px] text-[12px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="168">近7天</SelectItem>
                      <SelectItem value="336">近14天</SelectItem>
                      <SelectItem value="720">近30天</SelectItem>
                      <SelectItem value="2160">近90天</SelectItem>
                      <SelectItem value="4320">近180天</SelectItem>
                      <SelectItem value="24">近24小时</SelectItem>
                      <SelectItem value="48">近48小时</SelectItem>
                      <SelectItem value="72">近72小时</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {announcements.length === 0 ? (
                  <div className="card p-6 text-[12px] text-muted-foreground text-center">暂无公告</div>
                ) : (
                  announcements.map((item, idx) => (
                    <a
                      key={`${item.publish_time || 'a'}-${idx}`}
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

          </div>
        </DialogContent>
      </Dialog>

    </>
  )
}
