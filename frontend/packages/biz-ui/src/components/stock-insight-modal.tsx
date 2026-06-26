import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Brain, Copy, Download, ExternalLink, Play, RefreshCw, Share2, Sparkles } from 'lucide-react'
import {
  fetchAPI,
  insightApi,
  positionsApi,
  stocksApi,
  tradingAgentsApi,
  analystTypesForMode,
  deepAnalysisModeEta,
  loadDeepAnalysisMode,
  type BudgetInfo,
  type DeepAnalysisMode,
  type DeepAnalysisResult,
  type HistoryComparisonResponse,
  type ProgressResponse,
  type ProgressStage,
  type PositionAddResult,
  type PortfolioRecentTrade,
  type TradingAgentsTriggerResult,
  type TriggerStockAgentResponse,
  localSkillsApi,
  isLocalSkillAgentName,
  parseLocalSkillSlug,
  localSkillAgentName,
} from '@panwatch/api'
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
import AddPositionCalculator, { type PositionHoldingOption } from '@panwatch/biz-ui/components/add-position-calculator'
import { ReportMarkdown } from '@panwatch/biz-ui/components/report-markdown'
import { RollingCostPlanPanel } from '@panwatch/biz-ui/components/rolling-cost-plan'
import { ChanEmotionStrategyPanel } from '@panwatch/biz-ui/components/chan-emotion-strategy-panel'
import { DeepAnalysisModePicker } from '@panwatch/biz-ui/components/deep-analysis-mode-picker'
import { StockConceptTags } from '@panwatch/biz-ui/components/stock-concept-tags'

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
  quality_overview?: Record<string, any> | null
  context_summary?: Record<string, any> | null
  context_payload?: Record<string, any> | null
  prompt_context?: string | null
  prompt_stats?: Record<string, any> | null
  news_debug?: Record<string, any> | null
  created_at: string
  updated_at?: string
}

interface PortfolioPosition {
  id?: number
  symbol: string
  market: string
  quantity: number
  cost_price: number
  market_value_cny: number | null
  pnl: number | null
  account_name?: string
}

interface PortfolioSummaryResponse {
  accounts: Array<{
    id?: number
    name?: string
    available_funds?: number
    total_assets?: number
    positions: PortfolioPosition[]
  }>
  total?: {
    total_market_value?: number
    available_funds?: number
    total_assets?: number
  }
}

export type InsightTab = 'overview' | 'kline' | 'suggestions' | 'news' | 'announcements' | 'reports' | 'deep'

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
  concept_tags?: Array<{ name: string; source: string }>
  concept_tags_auto?: string[]
  concept_tags_manual?: string[]
  agents?: StockAgentInfo[]
}

interface ReportAgentConfig {
  name: string
  display_name: string
  description?: string
  enabled: boolean
  execution_mode?: string
}

const REPORT_TRIGGER_AGENT_NAMES = [
  'daily_report',
  'premarket_outlook',
  'intraday_monitor',
  'lmd_outlook',
] as const

const AGENT_LABELS: Record<string, string> = {
  daily_report: '盘后日报',
  premarket_outlook: '盘前分析',
  intraday_monitor: '盘中监测',
  news_digest: '新闻速递',
  chart_analyst: '技术分析',
  tradingagents: 'TradingAgents 深度',
  lmd_outlook: '老马视角',
}

function resolveAgentLabel(agentName: string, agents: ReportAgentConfig[] = []): string {
  const slug = parseLocalSkillSlug(agentName)
  if (slug) {
    const hit = agents.find(a => a.name === agentName)
    if (hit?.display_name) return hit.display_name
    return slug
  }
  return AGENT_LABELS[agentName] || agentName
}

function historyRecordMatchesStock(
  record: HistoryRecord,
  sym: string,
  name?: string,
): boolean {
  const upperSymbol = sym.toUpperCase()
  const sug = record?.suggestions || {}
  const keys = Object.keys(sug || {})
  if (keys.includes(sym) || keys.map(k => k.toUpperCase()).includes(upperSymbol)) return true
  const text = `${record?.title || ''}\n${record?.content || ''}`.toUpperCase()
  if (upperSymbol && text.includes(upperSymbol)) return true
  const trimmedName = (name || '').trim()
  if (trimmedName && `${record?.title || ''}\n${record?.content || ''}`.includes(trimmedName)) return true
  return false
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
    timeZone: 'Asia/Shanghai',
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
  initialTab?: InsightTab
  initialReportId?: number | null
  /** 打开弹窗时自动展开加仓面板 */
  initialExpandAddPosition?: boolean
  /** 打开弹窗时自动展开减仓面板 */
  initialExpandReducePosition?: boolean
  /** 加仓成功后通知外部刷新持仓列表 */
  onPortfolioChanged?: (result: PositionAddResult) => void
  /** 关注股票信息变更（如概念标签） */
  onStockUpdated?: (stock: StockItem) => void
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
  const [selectedReportId, setSelectedReportId] = useState<number | null>(null)
  const [deepResult, setDeepResult] = useState<DeepAnalysisResult | null>(null)
  const [deepLoading, setDeepLoading] = useState(false)
  const [deepLoaded, setDeepLoaded] = useState(false)
  const [deepRunStage, setDeepRunStage] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [deepTraceId, setDeepTraceId] = useState<string | null>(null)
  const [deepProgress, setDeepProgress] = useState<ProgressResponse | null>(null)
  const [deepBudget, setDeepBudget] = useState<BudgetInfo | null>(null)
  const [deepTriggerError, setDeepTriggerError] = useState('')
  const [deepShowAnalyst, setDeepShowAnalyst] = useState(false)
  const [deepShowDebate, setDeepShowDebate] = useState(false)
  const [deepHistory, setDeepHistory] = useState<HistoryComparisonResponse | null>(null)
  const [deepHistoryLoading, setDeepHistoryLoading] = useState(false)
  const [deepAnalysisMode, setDeepAnalysisMode] = useState<DeepAnalysisMode>(() => loadDeepAnalysisMode())
  const deepPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const deepTriggerStartedRef = useRef(0)
  const [klineInterval] = useState<'1d' | '1w' | '1m'>('1d')
  const [alerting, setAlerting] = useState(false)
  const [alertPanelKey, setAlertPanelKey] = useState(0)
  const [watchingStock, setWatchingStock] = useState<StockItem | null>(null)
  const [watchToggleLoading, setWatchToggleLoading] = useState(false)
  const [autoSuggesting, setAutoSuggesting] = useState(false)
  const [reportAgents, setReportAgents] = useState<ReportAgentConfig[]>([])
  const [reportAgentName, setReportAgentName] = useState<string>('daily_report')
  const [reportGenerating, setReportGenerating] = useState<string | null>(null)
  const reportPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [imageExporting, setImageExporting] = useState(false)
  const [holdingAgg, setHoldingAgg] = useState<{
    quantity: number
    cost: number
    unitCost: number
    marketValue: number
    pnl: number
    totalAssets: number
    totalMarketValue: number
    availableCash: number
  } | null>(null)
  const [holdingOptions, setHoldingOptions] = useState<PositionHoldingOption[]>([])
  const [recentTrades, setRecentTrades] = useState<PortfolioRecentTrade[]>([])
  const [addPositionExpandSignal, setAddPositionExpandSignal] = useState(0)
  const [tradeExpandMode, setTradeExpandMode] = useState<'add' | 'reduce'>('add')
  const [holdingLoaded, setHoldingLoaded] = useState(false)
  const [holdingLoadError, setHoldingLoadError] = useState(false)
  const autoTriggeredRef = useRef<Record<string, number>>({})
  const stockCacheRef = useRef<Record<string, StockItem>>({})
  const pendingReportIdRef = useRef<number | null>(null)
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

  const loadSuggestions = useCallback(async (): Promise<SuggestionInfo[]> => {
    if (!symbol) return []
    const data = await insightApi.suggestions<any[]>(symbol, {
      market,
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
    return list
  }, [symbol, market, includeExpiredSuggestions])

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
      const options: PositionHoldingOption[] = []
      for (const acc of data?.accounts || []) {
        for (const p of acc.positions || []) {
          if (p.symbol !== symbol || p.market !== market) continue
          if (p.id != null) {
            options.push({
              id: p.id,
              account_name: p.account_name || acc.name || '默认账户',
              quantity: Number(p.quantity || 0),
              cost_price: Number(p.cost_price || 0),
            })
          }
          quantity += Number(p.quantity || 0)
          cost += Number(p.cost_price || 0) * Number(p.quantity || 0)
          marketValue += Number(p.market_value_cny || 0)
          pnl += Number(p.pnl || 0)
        }
      }
      setHoldingOptions(options)
      const totalAssets = Number(data?.total?.total_assets || 0)
      const totalMarketValue = Number(data?.total?.total_market_value || 0)
      const availableCash = Number(data?.total?.available_funds || 0)
      if (quantity > 0) {
        setHoldingAgg({
          quantity,
          cost,
          unitCost: cost / quantity,
          marketValue,
          pnl,
          totalAssets,
          totalMarketValue,
          availableCash,
        })
      } else setHoldingAgg(null)

      try {
        const tradeRows = await positionsApi.recentTrades(50)
        const sym = String(symbol || '').toUpperCase()
        setRecentTrades(
          (tradeRows || []).filter(
            (t) => String(t.symbol || '').toUpperCase() === sym && String(t.market || '') === market,
          ),
        )
      } catch {
        setRecentTrades([])
      }
    } catch {
      setHoldingLoadError(true)
    } finally {
      setHoldingLoaded(true)
    }
  }, [symbol, market])

  const handlePositionApplied = useCallback(
    (result: PositionAddResult) => {
      if (result?.position) {
        const pos = result.position
        setHoldingOptions((prev) => {
          const next = prev.map((h) =>
            h.id === pos.id
              ? {
                  ...h,
                  quantity: pos.quantity,
                  cost_price: pos.cost_price,
                }
              : h,
          )
          let quantity = 0
          let cost = 0
          for (const h of next) {
            quantity += h.quantity
            cost += h.cost_price * h.quantity
          }
          setHoldingAgg((agg) =>
            quantity > 0
              ? {
                  quantity,
                  cost,
                  unitCost: cost / quantity,
                  marketValue: agg?.marketValue ?? 0,
                  pnl: agg?.pnl ?? 0,
                  totalAssets: agg?.totalAssets ?? 0,
                  totalMarketValue: agg?.totalMarketValue ?? 0,
                  availableCash: agg?.availableCash ?? 0,
                }
              : null,
          )
          return next
        })
        if (result.trade) {
          const sym = String(symbol || '').toUpperCase()
          setRecentTrades((prev) => {
            const entry: PortfolioRecentTrade = {
              ...result.trade,
              account_name: pos.account_name || '',
              symbol: pos.stock_symbol || symbol,
              market,
              stock_name: pos.stock_name || '',
            }
            if (String(entry.symbol || '').toUpperCase() !== sym || entry.market !== market) {
              return prev
            }
            return [entry, ...prev.filter((t) => t.id !== entry.id)]
          })
        }
      }
      void loadHoldingAgg()
      props.onPortfolioChanged?.(result)
    },
    [loadHoldingAgg, props, symbol, market],
  )

  const loadReports = useCallback(async (): Promise<HistoryRecord[]> => {
    if (!symbol) return []
    try {
      const [bySymbol, globals] = await Promise.all([
        insightApi.history<HistoryRecord[]>({
          stock_symbol: symbol,
          kind: 'all',
          limit: 50,
        }).catch(() => []),
        insightApi.history<HistoryRecord[]>({
          stock_symbol: '*',
          kind: 'all',
          limit: 80,
        }).catch(() => []),
      ])
      const name = (resolvedName || '').trim()
      const globalHits = (globals || []).filter(r => historyRecordMatchesStock(r, symbol, name))
      const seen = new Set<number>()
      const merged = [...(bySymbol || []), ...globalHits]
        .filter((r): r is HistoryRecord => {
          if (!r?.id || seen.has(r.id)) return false
          seen.add(r.id)
          return true
        })
        .sort((a, b) => {
          const am = parseToMs(a.updated_at || a.created_at || a.analysis_date) || 0
          const bm = parseToMs(b.updated_at || b.created_at || b.analysis_date) || 0
          return bm - am
        })
      setReports(merged)
      return merged
    } catch {
      setReports([])
      return []
    }
  }, [symbol, resolvedName])

  const loadReportAgents = useCallback(async () => {
    try {
      const [list, localSkills] = await Promise.all([
        fetchAPI<ReportAgentConfig[]>('/agents'),
        localSkillsApi.list({ enabledOnly: true }).catch(() => []),
      ])
      const filtered = (list || []).filter(
        a => a.enabled && REPORT_TRIGGER_AGENT_NAMES.includes(a.name as (typeof REPORT_TRIGGER_AGENT_NAMES)[number]),
      )
      const localAgents: ReportAgentConfig[] = (localSkills || []).map(s => ({
        name: localSkillAgentName(s.slug),
        display_name: s.display_name || s.slug,
        enabled: true,
      }))
      const merged = [...filtered, ...localAgents]
      if (merged.length > 0) {
        setReportAgents(merged)
        if (!merged.some(a => a.name === reportAgentName)) {
          const preferred = merged.find(a => a.name === 'daily_report') || merged[0]
          setReportAgentName(preferred.name)
        }
        return
      }
    } catch {
      // fallback below
    }
    setReportAgents(
      REPORT_TRIGGER_AGENT_NAMES.map(name => ({
        name,
        display_name: AGENT_LABELS[name] || name,
        enabled: true,
      })),
    )
  }, [reportAgentName])

  const stopReportPoll = useCallback(() => {
    if (reportPollRef.current) {
      clearInterval(reportPollRef.current)
      reportPollRef.current = null
    }
  }, [])

  const ensureStockAgentBinding = useCallback(async (agentName: string): Promise<{
    stockId: number
    useUnbound: boolean
  }> => {
    const stocks = await stocksApi.list()
    let stock =
      watchingStock
      || stockCacheRef.current[`${market}:${symbol}`]
      || (stocks || []).find(s => s.symbol === symbol && s.market === market)
      || null

    if (!stock) {
      return { stockId: 0, useUnbound: true }
    }

    const existingAgents = (stock.agents || []).map(a => ({
      agent_name: a.agent_name,
      schedule: a.schedule || '',
      ai_model_id: a.ai_model_id ?? null,
      notify_channel_ids: a.notify_channel_ids || [],
    }))
    if (existingAgents.some(a => a.agent_name === agentName)) {
      return { stockId: stock.id, useUnbound: false }
    }

    const updated = await stocksApi.updateAgents(stock.id, {
      agents: [
        ...existingAgents,
        { agent_name: agentName, schedule: '', ai_model_id: null, notify_channel_ids: [] },
      ],
    })
    stockCacheRef.current[`${market}:${symbol}`] = updated
    setWatchingStock(updated)
    toast(`已自动关联 ${AGENT_LABELS[agentName] || agentName}`, 'info')
    return { stockId: updated.id, useUnbound: false }
  }, [market, symbol, toast, watchingStock])

  const pollForNewReport = useCallback(async (opts: {
    agentName: string
    baselineReportId: number | null
    baselineReportCount: number
    baselineReportUpdatedAt: string | null
    baselineSuggestionCount: number
  }) => {
    stopReportPoll()
    const startedAt = Date.now()
    const tick = async () => {
      if (Date.now() - startedAt > 180_000) {
        stopReportPoll()
        setReportGenerating(null)
        toast('报告生成超时，请稍后刷新列表', 'info')
        return
      }
      const freshReports = await loadReports()
      const reportChanged =
        freshReports.length > opts.baselineReportCount
        || (freshReports[0]?.id != null && freshReports[0].id !== opts.baselineReportId)
        || (parseToMs(freshReports[0]?.updated_at || '') ?? 0) > (parseToMs(opts.baselineReportUpdatedAt || '') ?? 0)
      const freshSuggestions = await loadSuggestions()
      const suggestionsChanged = freshSuggestions.length > opts.baselineSuggestionCount
      const isSuggestionAgent = opts.agentName === 'intraday_monitor'
      const done = isSuggestionAgent ? suggestionsChanged : (reportChanged || suggestionsChanged)
      if (!done) return
      stopReportPoll()
      setReportGenerating(null)
      if (!isSuggestionAgent && freshReports[0]?.id) {
        setSelectedReportId(freshReports[0].id)
      }
      toast(
        isSuggestionAgent ? 'AI 建议已更新，可在「建议」查看' : '报告已生成',
        'success',
      )
      if (isSuggestionAgent) {
        setTab('suggestions')
      }
    }
    reportPollRef.current = setInterval(() => {
      void tick()
    }, 5_000)
    await tick()
  }, [loadReports, loadSuggestions, stopReportPoll, toast])

  const triggerReportGeneration = useCallback(async () => {
    if (!symbol || reportGenerating) return
    const agentName = reportAgentName || 'daily_report'
    const baselineReportId = reports[0]?.id ?? null
    const baselineReportCount = reports.length
    const baselineReportUpdatedAt = reports[0]?.updated_at ?? null
    const baselineSuggestionCount = suggestions.length
    setReportGenerating(agentName)
    try {
      if (isLocalSkillAgentName(agentName)) {
        const slug = parseLocalSkillSlug(agentName)
        if (!slug) throw new Error('无效的本地 Skill')
        const stocks = await stocksApi.list()
        const stock =
          watchingStock
          || stockCacheRef.current[`${market}:${symbol}`]
          || (stocks || []).find(s => s.symbol === symbol && s.market === market)
          || null
        await localSkillsApi.trigger(
          slug,
          {
            stock_id: stock?.id || 0,
            symbol,
            market,
            name: resolvedName || symbol,
          },
          { wait: true, timeoutMs: 720_000 },
        )
        const fresh = await loadReports()
        setSelectedReportId(fresh[0]?.id ?? baselineReportId)
        toast('Skill 报告已生成', 'success')
        return
      }

      const { stockId, useUnbound } = await ensureStockAgentBinding(agentName)
      const unboundOpts = useUnbound
        ? {
            allow_unbound: true,
            symbol,
            market,
            name: resolvedName || symbol,
          }
        : {}
      const triggerOpts = {
        bypass_throttle: true,
        bypass_market_hours: true,
        ...unboundOpts,
      }
      const syncWait = agentName === 'lmd_outlook' || isLocalSkillAgentName(agentName)
      let resp: TriggerStockAgentResponse
      if (syncWait) {
        const qs = new URLSearchParams({
          bypass_throttle: 'true',
          bypass_market_hours: 'true',
          wait: 'true',
          ...Object.fromEntries(
            Object.entries(unboundOpts).map(([k, v]) => [k, String(v)]),
          ),
        })
        resp = await fetchAPI<TriggerStockAgentResponse>(
          `/stocks/${stockId}/agents/${encodeURIComponent(agentName)}/trigger?${qs.toString()}`,
          { method: 'POST', timeoutMs: 720_000 },
        )
      } else {
        resp = await stocksApi.triggerAgent(stockId, agentName, triggerOpts)
      }

      if (resp?.queued) {
        toast(resp.message || '已提交后台执行，报告生成中...', 'info')
        await pollForNewReport({
          agentName,
          baselineReportId,
          baselineReportCount,
          baselineReportUpdatedAt,
          baselineSuggestionCount,
        })
        return
      }

      const result = resp?.result
      if (result?.success === false) {
        toast(result.message || result.content || '报告生成未通过', 'info')
        return
      }
      const isSkipped = !!result?.skipped || /已跳过执行|非交易时段/.test(result?.content || '')
      if (isSkipped) {
        toast(result?.content || '当前非交易时段，已跳过执行', 'info')
        return
      }
      const fresh = await loadReports()
      setSelectedReportId(fresh[0]?.id ?? baselineReportId)
      await loadSuggestions()
      if (agentName === 'intraday_monitor') {
        toast('AI 建议已更新，可在「建议」查看', 'success')
        setTab('suggestions')
      } else {
        toast(agentName === 'lmd_outlook' ? '老马视角报告已生成' : '报告已生成', 'success')
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : '报告生成失败'
      if (/非交易时段|跳过执行/.test(msg)) {
        toast(msg, 'info')
      } else {
        toast(msg, 'error')
      }
    } finally {
      if (!reportPollRef.current) {
        setReportGenerating(null)
      }
    }
  }, [
    symbol,
    reportGenerating,
    reportAgentName,
    reports,
    suggestions.length,
    market,
    resolvedName,
    ensureStockAgentBinding,
    pollForNewReport,
    loadReports,
    loadSuggestions,
    toast,
  ])

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

  const loadDeepResult = useCallback(async () => {
    if (!symbol) return
    setDeepLoading(true)
    setDeepHistoryLoading(true)
    try {
      const [latest, history] = await Promise.allSettled([
        tradingAgentsApi.getLatestForStock(symbol),
        tradingAgentsApi.getHistoryComparison(symbol, market, 90),
      ])
      const nextResult = latest.status === 'fulfilled' ? latest.value : null
      setDeepResult(nextResult)
      setDeepHistory(history.status === 'fulfilled' ? history.value : null)
      if (nextResult) setDeepRunStage('done')
    } catch {
      setDeepResult(null)
      setDeepHistory(null)
    } finally {
      setDeepLoaded(true)
      setDeepLoading(false)
      setDeepHistoryLoading(false)
    }
  }, [symbol, market])

  const stopDeepPoll = useCallback(() => {
    if (deepPollRef.current) {
      clearInterval(deepPollRef.current)
      deepPollRef.current = null
    }
  }, [])

  const resolveStockId = useCallback(async (): Promise<number> => {
    if (watchingStock?.id) return watchingStock.id
    const cached = stockCacheRef.current[`${market}:${symbol}`]
    if (cached?.id) return cached.id
    try {
      const stocks = await stocksApi.list()
      const found = (stocks || []).find(s => s.symbol === symbol && s.market === market)
      return found?.id ?? 0
    } catch {
      return 0
    }
  }, [market, symbol, watchingStock])

  const triggerDeepAnalysis = useCallback(async (force = false, analystTypes?: string[]): Promise<TradingAgentsTriggerResult> => {
    const types = analystTypes ?? analystTypesForMode(deepAnalysisMode)
    const stockId = await resolveStockId()
    if (stockId > 0) {
      return tradingAgentsApi.trigger(stockId, { force, analystTypes: types })
    }
    const qs = new URLSearchParams({
      allow_unbound: 'true',
      symbol,
      market,
      name: resolvedName || symbol,
    })
    if (force) qs.set('force_refresh', 'true')
    return fetchAPI<TradingAgentsTriggerResult>(
      `/stocks/0/agents/tradingagents/trigger?${qs.toString()}`,
      { method: 'POST', body: JSON.stringify({ analyst_types: types }) },
    )
  }, [deepAnalysisMode, market, resolveStockId, resolvedName, symbol])

  const pollDeepProgress = useCallback(async (tid: string) => {
    try {
      const resp = await tradingAgentsApi.getProgress(tid)
      setDeepProgress(resp)
      if (resp.status === 'success' && resp.run) {
        stopDeepPoll()
        clearDeepRunningTrace(symbol)
        const latest = await tradingAgentsApi.getLatestForStock(symbol)
        if (latest) {
          setDeepResult(latest)
          setDeepRunStage('done')
          setDeepLoaded(true)
        } else {
          setDeepTriggerError('结果未落库，请稍后刷新')
          setDeepRunStage('error')
        }
      } else if (resp.status === 'failed') {
        stopDeepPoll()
        clearDeepRunningTrace(symbol)
        setDeepTriggerError(resp.run?.error || '分析失败')
        setDeepRunStage('error')
      } else if (resp.status === 'stale') {
        stopDeepPoll()
        clearDeepRunningTrace(symbol)
        setDeepTraceId(null)
        setDeepProgress(null)
        setDeepRunStage('idle')
      } else if (resp.status === 'not_found') {
        const sinceTrigger = Date.now() - deepTriggerStartedRef.current
        if (deepTriggerStartedRef.current > 0 && sinceTrigger > DEEP_NOT_FOUND_GRACE_MS) {
          stopDeepPoll()
          clearDeepRunningTrace(symbol)
          setDeepTraceId(null)
          setDeepProgress(null)
          setDeepRunStage('idle')
        }
      }
    } catch {
      /* polling 失败不立即终止 */
    }
  }, [symbol, stopDeepPoll])

  const startDeepPolling = useCallback((tid: string) => {
    stopDeepPoll()
    deepPollRef.current = setInterval(() => {
      void pollDeepProgress(tid)
    }, DEEP_POLL_INTERVAL_MS)
    void pollDeepProgress(tid)
  }, [pollDeepProgress, stopDeepPoll])

  const handleDeepStart = useCallback(async (force = false) => {
    setDeepRunStage('running')
    setDeepTriggerError('')
    setDeepProgress(null)
    deepTriggerStartedRef.current = Date.now()
    try {
      const resp = await triggerDeepAnalysis(force)
      const tid = resp.trace_id || ''
      setDeepTraceId(tid)
      if (!tid) {
        setDeepRunStage('done')
        toast(resp.message || '已触发', 'success')
        await loadDeepResult()
        return
      }
      saveDeepRunningTrace(symbol, tid)
      startDeepPolling(tid)
    } catch (e) {
      setDeepRunStage('error')
      setDeepTriggerError(e instanceof Error ? e.message : '触发失败')
    }
  }, [loadDeepResult, startDeepPolling, symbol, toast, triggerDeepAnalysis])

  const syncDeepRunState = useCallback(async () => {
    const analystTypes = analystTypesForMode(deepAnalysisMode)
    const [runningInfo, budgetInfo] = await Promise.all([
      tradingAgentsApi.findRunning(symbol).catch(() => ({ trace_id: null, status: 'none' as const })),
      tradingAgentsApi.getBudget(analystTypes).catch(() => null),
    ])
    setDeepBudget(budgetInfo)

    if (runningInfo.status === 'running' && runningInfo.trace_id) {
      const tid = runningInfo.trace_id
      setDeepTraceId(tid)
      setDeepRunStage('running')
      deepTriggerStartedRef.current = Date.now() - DEEP_NOT_FOUND_GRACE_MS - 1
      const resp = await tradingAgentsApi.getProgress(tid).catch(() => null)
      if (resp) setDeepProgress(resp)
      startDeepPolling(tid)
      return
    }

    if (runningInfo.status === 'stale' || runningInfo.status === 'failed') {
      clearDeepRunningTrace(symbol)
    }

    if (runningInfo.status === 'none') {
      const localTrace = loadDeepRunningTrace(symbol)
      if (localTrace) {
        setDeepTraceId(localTrace)
        setDeepRunStage('running')
        deepTriggerStartedRef.current = Date.now()
        const resp = await tradingAgentsApi.getProgress(localTrace).catch(() => null)
        if (resp) setDeepProgress(resp)
        startDeepPolling(localTrace)
        return
      }
    }

    if (deepResult) {
      setDeepRunStage('done')
      clearDeepRunningTrace(symbol)
      return
    }

    setDeepRunStage('idle')
    clearDeepRunningTrace(symbol)
  }, [deepAnalysisMode, deepResult, startDeepPolling, symbol])

  useEffect(() => {
    if (!props.open || !symbol) return
    setTab(props.initialTab ?? 'overview')
    pendingReportIdRef.current = props.initialReportId ?? null
    setSuggestions([])
    setNews([])
    setAnnouncements([])
    setReports([])
    setSelectedReportId(props.initialReportId ?? null)
    setMiniKlines([])
    setWatchingStock(null)
    setDeepResult(null)
    setDeepLoaded(false)
    setDeepHistory(null)
    setDeepRunStage('idle')
    setDeepTraceId(null)
    setDeepProgress(null)
    setDeepBudget(null)
    setDeepTriggerError('')
    stopDeepPoll()
    setReportGenerating(null)
    stopReportPoll()
    loadCore()
  }, [props.open, symbol, market, props.initialTab, props.initialReportId, loadCore, stopDeepPoll, stopReportPoll])

  useEffect(() => {
    if (!props.open) return
    if (props.initialExpandReducePosition) {
      setTradeExpandMode('reduce')
      setAddPositionExpandSignal((s) => s + 1)
    } else if (props.initialExpandAddPosition) {
      setTradeExpandMode('add')
      setAddPositionExpandSignal((s) => s + 1)
    }
  }, [props.open, props.initialExpandAddPosition, props.initialExpandReducePosition, symbol])

  // 切到「深度」tab 时按需拉取(仅首次)
  useEffect(() => {
    if (!props.open || !symbol) return
    if (tab === 'deep' && !deepLoaded && !deepLoading) {
      loadDeepResult()
    }
  }, [tab, props.open, symbol, deepLoaded, deepLoading, loadDeepResult])

  useEffect(() => {
    if (!props.open || !symbol || tab !== 'deep' || !deepLoaded) return
    void syncDeepRunState()
  }, [props.open, symbol, tab, deepLoaded, syncDeepRunState])

  useEffect(() => {
    if (!props.open || tab !== 'deep' || deepRunStage !== 'idle') return
    tradingAgentsApi.getBudget(analystTypesForMode(deepAnalysisMode)).then(setDeepBudget).catch(() => null)
  }, [deepAnalysisMode, deepRunStage, props.open, tab])

  useEffect(() => {
    if (!props.open) stopDeepPoll()
    return () => stopDeepPoll()
  }, [props.open, stopDeepPoll])

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
    if (!props.open) return
    void loadReportAgents()
  }, [props.open, loadReportAgents])

  useEffect(() => {
    return () => stopReportPoll()
  }, [stopReportPoll])

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
  const formatTradeContextLine = (t: PortfolioRecentTrade) => {
    const side = t.side === 'sell' ? '卖出' : '买入'
    const time = t.traded_at ? new Date(t.traded_at) : null
    const today = (() => {
      if (!time || Number.isNaN(time.getTime())) return false
      const now = new Date()
      return time.toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai' })
        === now.toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai' })
    })()
    const timeLabel = time && !Number.isNaN(time.getTime())
      ? time.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
      : ''
    const after = t.qty_after != null && t.cost_after != null
      ? ` → 持仓${t.qty_after}股 成本${Number(t.cost_after).toFixed(4)}`
      : ''
    return `${today ? '【今日】' : ''}${side} ${t.quantity}股 @${t.price}${after} (${t.account_name || '账户'}, ${timeLabel})`
  }

  const buildPageContext = useCallback(() => {
    const parts: string[] = []
    if (quote) {
      const items = [`价格${quote.current_price}`, `涨跌幅${quote.change_pct}%`]
      if (quote.volume != null) items.push(`成交量${quote.volume}`)
      if (quote.turnover_rate != null) items.push(`换手率${quote.turnover_rate}%`)
      if (quote.pe_ratio != null) items.push(`市盈率${quote.pe_ratio}`)
      if (quote.total_market_value != null) items.push(`总市值${quote.total_market_value}`)
      parts.push(`实时行情：${items.join('，')}`)
    }
    if (klineSummary) {
      const k = klineSummary as any
      const items = []
      if (k.trend) items.push(`趋势${k.trend}`)
      if (k.macd_status) items.push(`MACD${k.macd_status}`)
      if (k.rsi_status) items.push(`RSI${k.rsi_status}${k.rsi6 != null ? `(${k.rsi6})` : ''}`)
      if (k.kdj_status) items.push(`KDJ${k.kdj_status}`)
      if (k.boll_status) items.push(`布林${k.boll_status}`)
      if (k.volume_trend) items.push(`量能${k.volume_trend}${k.volume_ratio != null ? `(${k.volume_ratio}x)` : ''}`)
      if (k.support != null) items.push(`支撑${k.support}`)
      if (k.resistance != null) items.push(`压力${k.resistance}`)
      if (items.length) parts.push(`技术面：${items.join('，')}`)
    }
    if (technicalScored) {
      parts.push(`技术评分：${technicalScored.action_label}(score=${technicalScored.score})，信号：${technicalScored.signal || '中性'}`)
      const evidence = (technicalScored.evidence || []).filter((e: any) => e.delta !== 0)
      if (evidence.length) {
        parts.push(`评分依据：${evidence.map((e: any) => `${e.text}(${e.delta > 0 ? '+' : ''}${e.delta})`).join('；')}`)
      }
    }
    if (suggestions.length > 0) {
      const lines = suggestions.slice(0, 3).map(s => `- [${s.agent_label || s.agent_name}] ${s.action_label}: ${s.signal}`)
      parts.push(`最近AI建议：\n${lines.join('\n')}`)
    }
    if (holdingAgg) {
      const holdingLines = holdingOptions.length > 0
        ? holdingOptions.map(h => `- ${h.account_name}: ${h.quantity}股 成本${Number(h.cost_price).toFixed(4)}`)
        : []
      parts.push(
        `持仓汇总：${holdingAgg.quantity}股，加权成本${holdingAgg.unitCost.toFixed(4)}，市值${holdingAgg.marketValue}，盈亏${holdingAgg.pnl}`
        + (holdingLines.length ? `\n分账户：\n${holdingLines.join('\n')}` : ''),
      )
    }
    const todayTrades = recentTrades.filter(t => formatTradeContextLine(t).startsWith('【今日】'))
    const tradesForContext = todayTrades.length > 0 ? todayTrades : recentTrades.slice(0, 5)
    if (tradesForContext.length > 0) {
      const title = todayTrades.length > 0 ? '今日持仓变动' : '最近持仓变动'
      parts.push(`${title}：\n${tradesForContext.map(t => `- ${formatTradeContextLine(t)}`).join('\n')}`)
    }
    parts.push(`数据时间：${new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}`)
    return parts.join('\n')
  }, [quote, klineSummary, technicalScored, suggestions, holdingAgg, holdingOptions, recentTrades])

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

  useEffect(() => {
    if (!reports.length) {
      setSelectedReportId(null)
      return
    }
    const pending = pendingReportIdRef.current
    if (pending != null && reports.some(r => r.id === pending)) {
      setSelectedReportId(pending)
      pendingReportIdRef.current = null
      return
    }
    if (selectedReportId && reports.some(r => r.id === selectedReportId)) return
    setSelectedReportId(reports[0].id)
  }, [reports, selectedReportId])

  const activeReport = useMemo(
    () => reports.find(r => r.id === selectedReportId) || reports[0] || null,
    [reports, selectedReportId],
  )
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
      timeZone: 'Asia/Shanghai',
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
      toast('已设置提醒，AI 分析已提交', 'success')
      // 轮询等待建议生成（最多 2 分钟，每 5 秒一次）
      const before = Date.now()
      const poll = setInterval(async () => {
        if (Date.now() - before > 120_000) { clearInterval(poll); setAlerting(false); return }
        await loadSuggestions()
      }, 5_000)
      await loadSuggestions()
      // 延迟清理：2 分钟后 interval 自动停止
      setTimeout(() => clearInterval(poll), 125_000)
      return
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

  const handleUpdateManualConceptTags = useCallback(async (manual: string[]) => {
    if (!watchingStock) return
    const updated = await stocksApi.updateConceptTags(watchingStock.id, manual)
    stockCacheRef.current[`${market}:${symbol}`] = updated
    setWatchingStock(updated)
    props.onStockUpdated?.(updated)
  }, [market, props, symbol, watchingStock])

  const handleRefreshConceptTags = useCallback(async () => {
    if (!watchingStock) return
    const updated = await stocksApi.refreshConceptTags(watchingStock.id)
    stockCacheRef.current[`${market}:${symbol}`] = updated
    setWatchingStock(updated)
    props.onStockUpdated?.(updated)
    toast('概念标签已刷新', 'success')
  }, [market, props, symbol, toast, watchingStock])

  const triggerAutoAiSuggestion = useCallback(async () => {
    // 自动建议仅针对”确认未持仓”的股票，且不自动创建股票/绑定 Agent。
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
      // 异步模式：triggerAgent 立即返回，轮询等待建议生成
      const before = Date.now()
      const poll = setInterval(async () => {
        if (Date.now() - before > 120_000) { clearInterval(poll); setAutoSuggesting(false); return }
        await loadSuggestions()
      }, 5_000)
      await loadSuggestions()
      setTimeout(() => clearInterval(poll), 125_000)
      return
    } catch (e) {
      toast(
        e instanceof Error ? e.message : '自动 AI 建议触发失败，可点击「一键设提醒」重试',
        'error'
      )
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
            <div className="flex items-start justify-between gap-3 pr-10 md:pr-8">
              <div className="shrink-0">
                <DialogTitle className="flex items-center gap-2 flex-wrap">
                  <span className={`text-[10px] px-2 py-0.5 rounded ${badge.style}`}>{badge.label}</span>
                  <span className="break-all">{resolvedName}</span>
                  <span className="font-mono text-[12px] text-muted-foreground">({symbol})</span>
                </DialogTitle>
                {watchingStock && (
                  <StockConceptTags
                    tags={watchingStock.concept_tags || []}
                    market={market}
                    editable
                    className="mt-2"
                    onUpdateManual={handleUpdateManualConceptTags}
                    onRefreshAuto={handleRefreshConceptTags}
                  />
                )}
                <DialogDescription className="hidden md:block">概览、K线、AI建议、新闻、历史分析都在同一弹窗查看</DialogDescription>
              </div>
              <div className="hidden md:flex items-center gap-2">
                <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => handleExportShareImage()} disabled={imageExporting}>
                  <Download className={`w-3.5 h-3.5 ${imageExporting ? 'animate-pulse' : ''}`} />
                  <span>{imageExporting ? '生成中' : '图片'}</span>
                </Button>
                <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => handleShareInsight()}>
                  <Share2 className="w-3.5 h-3.5" />
                  <span>分享</span>
                </Button>
                <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => handleCopyShareText()}>
                  <Copy className="w-3.5 h-3.5" />
                  <span>复制</span>
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
                <StockPriceAlertPanel key={alertPanelKey} mode="inline" symbol={symbol} market={market} stockName={resolvedName} />
                <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={handleSetAlert} disabled={alerting}>
                  {alerting ? '设置中...' : '一键设提醒'}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  className="h-8 px-2.5"
                  onClick={() => {
                    window.dispatchEvent(new CustomEvent('panwatch-open-chat', {
                      detail: { symbol, market, stockName: resolvedName, pageContext: buildPageContext() }
                    }))
                    props.onOpenChange(false)
                  }}
                >
                  <Sparkles className="w-3.5 h-3.5 mr-1" /> 问 AI
                </Button>
                <Button variant="outline" size="sm" className="h-8 px-2.5" onClick={() => handleRefreshAll()} disabled={loading}>
                  <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
                </Button>
              </div>
            </div>
            <div className="flex md:hidden items-center gap-2 mt-2 overflow-x-auto scrollbar-none pb-1 -mb-1">
              <Button variant="secondary" size="sm" className="h-8 px-2.5 shrink-0" onClick={() => handleExportShareImage()} disabled={imageExporting}>
                <Download className={`w-3.5 h-3.5 ${imageExporting ? 'animate-pulse' : ''}`} />
              </Button>
              <Button variant="secondary" size="sm" className="h-8 px-2.5 shrink-0" onClick={() => handleShareInsight()}>
                <Share2 className="w-3.5 h-3.5" />
              </Button>
              <Button variant="secondary" size="sm" className="h-8 px-2.5 shrink-0" onClick={() => handleCopyShareText()}>
                <Copy className="w-3.5 h-3.5" />
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className="h-8 px-2.5 shrink-0"
                onClick={toggleWatch}
                disabled={watchToggleLoading || (hasHolding && !!watchingStock)}
              >
                {watchToggleLoading ? '处理中...' : (watchingStock ? (hasHolding ? '持仓中' : '取消关注') : '快速关注')}
              </Button>
              <StockPriceAlertPanel key={alertPanelKey} mode="inline" symbol={symbol} market={market} stockName={resolvedName} />
              <Button variant="secondary" size="sm" className="h-8 px-2.5 shrink-0" onClick={handleSetAlert} disabled={alerting}>
                {alerting ? '设置中...' : '一键设提醒'}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className="h-8 px-2.5 shrink-0"
                onClick={() => {
                  window.dispatchEvent(new CustomEvent('panwatch-open-chat', {
                    detail: { symbol, market, stockName: resolvedName, pageContext: buildPageContext() }
                  }))
                  props.onOpenChange(false)
                }}
              >
                <Sparkles className="w-3.5 h-3.5 mr-1" /> 问 AI
              </Button>
              <Button variant="outline" size="sm" className="h-8 px-2.5 shrink-0" onClick={() => handleRefreshAll()} disabled={loading}>
                <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              </Button>
            </div>
          </DialogHeader>

          <div className="flex items-center justify-between gap-2 flex-wrap mb-3">
            <div className="flex items-center gap-1 flex-wrap">
              {[
                { id: 'overview', label: '概览' },
                { id: 'suggestions', label: `建议 (${suggestions.length})` },
                { id: 'reports', label: `报告 (${reports.length})` },
                { id: 'deep', label: deepResult ? '深度 (1)' : '深度' },
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
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <div className="text-[11px] text-muted-foreground">持仓信息</div>
                        {holdingAgg ? (
                          <div className="flex items-center gap-1">
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              className="h-6 px-2 text-[10px]"
                              onClick={() => {
                                setTradeExpandMode('add')
                                setAddPositionExpandSignal((s) => s + 1)
                              }}
                            >
                              加仓
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              className="h-6 px-2 text-[10px] border-rose-500/30 text-rose-600 hover:bg-rose-500/10"
                              onClick={() => {
                                setTradeExpandMode('reduce')
                                setAddPositionExpandSignal((s) => s + 1)
                              }}
                            >
                              减仓
                            </Button>
                          </div>
                        ) : null}
                      </div>
                      {holdingAgg ? (
                        <div className="grid grid-cols-2 gap-2 text-[12px]">
                          <div className="rounded bg-emerald-500/10 px-2 py-1.5">
                            <div className="text-[10px] text-muted-foreground flex items-center justify-between">
                              <span>持仓数量</span>
                              {holdingOptions.length > 1 && (
                                <span className="text-[9px] bg-primary/10 text-primary px-1 rounded">{holdingOptions.length} 个账户</span>
                              )}
                            </div>
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
                              {formatNumber(holdingAgg.unitCost, 4)}
                            </div>
                          </div>
                          <div className="rounded bg-emerald-500/10 px-2 py-1.5">
                            <div className="text-[10px] text-muted-foreground">持仓市值</div>
                            <div className="font-mono">{formatCompactNumber(holdingAgg.marketValue)}</div>
                          </div>
                          <div className="rounded bg-emerald-500/10 px-2 py-1.5">
                            <div className="text-[10px] text-muted-foreground">总资产</div>
                            <div className="font-mono">{formatCompactNumber(holdingAgg.totalAssets)}</div>
                            <div className="text-[9px] text-muted-foreground/70 mt-0.5">
                              组合市值+可用
                            </div>
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
                      <AddPositionCalculator
                        symbol={symbol}
                        market={market}
                        currentQuantity={holdingAgg?.quantity ?? 0}
                        currentCost={holdingAgg?.unitCost ?? 0}
                        currentPrice={quote?.current_price ?? null}
                        holdings={holdingOptions}
                        onApplied={handlePositionApplied}
                        defaultOpen={holdingOptions.length > 0}
                        expandSignal={addPositionExpandSignal}
                        expandMode={tradeExpandMode}
                      />
                      <RollingCostPlanPanel
                        symbol={symbol}
                        stockName={resolvedName || symbol}
                        market={market}
                        currentQuantity={holdingAgg?.quantity ?? 0}
                        currentCost={holdingAgg?.unitCost ?? 0}
                        currentPrice={quote?.current_price ?? null}
                        kline={klineSummary}
                        onAlertsCreated={() => setAlertPanelKey(v => v + 1)}
                      />
                      <ChanEmotionStrategyPanel
                        symbol={symbol}
                        market={market}
                        hasPosition={hasHolding}
                      />
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
                      <div className="text-[12px] text-muted-foreground">交易记录</div>
                      {recentTrades.length > 0 && (
                        <span className="text-[10px] text-muted-foreground">共 {recentTrades.length} 条</span>
                      )}
                    </div>
                    <div className="flex-1 space-y-2 overflow-y-auto">
                      {recentTrades.length === 0 ? (
                        <div className="text-[12px] text-muted-foreground py-6">
                          {holdingAgg ? '暂无交易记录' : '未持仓，暂无交易记录'}
                        </div>
                      ) : (
                        <div className="space-y-1.5">
                          {recentTrades.slice(0, 6).map((t) => (
                            <div
                              key={t.id}
                              className="flex items-center justify-between rounded border border-border/30 bg-accent/10 px-2 py-1.5 text-[11px]"
                            >
                              <div className="flex flex-col gap-0.5 min-w-0">
                                <span className="text-muted-foreground truncate">
                                  {t.account_name || '账户'}
                                  {t.traded_at ? ` · ${formatTime(t.traded_at)}` : ''}
                                </span>
                                <span
                                  className={`font-medium ${
                                    t.side === 'sell' ? 'text-emerald-500' : 'text-rose-500'
                                  }`}
                                >
                                  {t.side === 'sell' ? '卖出' : '买入'} {t.quantity} 股
                                  {t.qty_before != null && t.qty_after != null
                                    ? ` (${t.qty_before}→${t.qty_after})`
                                    : ''}
                                </span>
                              </div>
                              <div className="text-right shrink-0">
                                <div className="font-mono">@{formatNumber(t.price, 4)}</div>
                                {t.cost_before != null && t.cost_after != null ? (
                                  <div className="text-[10px] text-muted-foreground">
                                    成本 {formatNumber(t.cost_before, 4)} → {formatNumber(t.cost_after, 4)}
                                  </div>
                                ) : null}
                              </div>
                            </div>
                          ))}
                        </div>
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
                      <div className="space-y-2 py-3">
                        <div className="text-[12px] text-muted-foreground">暂无报告</div>
                        <Button
                          variant="secondary"
                          size="sm"
                          className="h-7 px-2.5 text-[11px]"
                          disabled={!!reportGenerating}
                          onClick={() => {
                            setTab('reports')
                            void triggerReportGeneration()
                          }}
                        >
                          {reportGenerating ? '生成中...' : '立即生成报告'}
                        </Button>
                      </div>
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
                />
              </div>
            )}

            {tab === 'reports' && (
              <div className="space-y-3">
                <div className="card p-3 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                  <div className="text-[12px] text-muted-foreground">
                    选择 Agent 立即生成该股票报告，无需返回列表页配置
                  </div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <Select value={reportAgentName} onValueChange={setReportAgentName}>
                      <SelectTrigger className="h-8 w-[148px] text-[11px]">
                        <SelectValue placeholder="选择 Agent" />
                      </SelectTrigger>
                      <SelectContent>
                        {(reportAgents.length > 0
                          ? reportAgents
                          : REPORT_TRIGGER_AGENT_NAMES.map(name => ({
                              name,
                              display_name: AGENT_LABELS[name] || name,
                              enabled: true,
                            }))
                        ).map(agent => (
                          <SelectItem key={agent.name} value={agent.name}>
                            {agent.display_name || AGENT_LABELS[agent.name] || agent.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Button
                      variant="default"
                      size="sm"
                      className="h-8 px-2.5 text-[11px]"
                      disabled={!!reportGenerating}
                      onClick={() => void triggerReportGeneration()}
                    >
                      {reportGenerating ? (
                        <span className="inline-flex items-center gap-1.5">
                          <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                          生成中...
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1.5">
                          <Play className="w-3 h-3" />
                          立即生成报告
                        </span>
                      )}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-8 px-2.5 text-[11px]"
                      disabled={!!reportGenerating}
                      onClick={() => void loadReports()}
                    >
                      <RefreshCw className="w-3 h-3" />
                    </Button>
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-12 gap-3">
                <div className="md:col-span-4 card p-2 max-h-[62vh] overflow-y-auto scrollbar">
                  {reports.length === 0 ? (
                    <div className="p-6 text-center space-y-3">
                      <div className="text-[12px] text-muted-foreground">暂无报告</div>
                      <Button
                        variant="secondary"
                        size="sm"
                        className="h-8 px-3 text-[11px]"
                        disabled={!!reportGenerating}
                        onClick={() => void triggerReportGeneration()}
                      >
                        {reportGenerating ? '生成中...' : '立即生成报告'}
                      </Button>
                    </div>
                  ) : (
                    <div className="divide-y divide-border/30">
                      {reports.map(r => {
                        const active = r.id === activeReport?.id
                        const stockSuggestion = r.suggestions?.[symbol]
                        return (
                          <button
                            key={r.id}
                            type="button"
                            onClick={() => setSelectedReportId(r.id)}
                            className={`w-full text-left px-2.5 py-2.5 rounded-lg transition-colors ${
                              active ? 'bg-primary/10 ring-1 ring-primary/20' : 'hover:bg-accent/40'
                            }`}
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-[10px] text-muted-foreground">
                                {resolveAgentLabel(r.agent_name, reportAgents)}
                              </span>
                              <span className="text-[10px] text-muted-foreground shrink-0">{r.analysis_date}</span>
                            </div>
                            <div className={`mt-0.5 text-[12px] line-clamp-2 ${active ? 'font-medium text-foreground' : 'text-foreground/90'}`}>
                              {r.title || '报告摘要'}
                            </div>
                            {stockSuggestion?.action_label && (
                              <div className="mt-1 text-[10px] inline-flex px-1.5 py-0.5 rounded bg-primary/10 text-primary">
                                {stockSuggestion.action_label}
                              </div>
                            )}
                          </button>
                        )
                      })}
                    </div>
                  )}
                </div>
                {!activeReport ? (
                  <div className="md:col-span-8 card p-6 text-[12px] text-muted-foreground text-center">选择左侧报告查看详情</div>
                ) : (
                  <div className="md:col-span-8 card p-4 space-y-3">
                    <div className="text-[11px] text-muted-foreground">
                      {resolveAgentLabel(activeReport.agent_name, reportAgents)} · {activeReport.analysis_date}
                    </div>
                    <div className="text-[15px] font-medium">{activeReport.title || '报告摘要'}</div>
                    {activeReport.suggestions && activeReport.suggestions[symbol]?.action_label && (
                      <div className="text-[11px] inline-flex px-2 py-0.5 rounded bg-primary/10 text-primary">
                        {activeReport.suggestions[symbol].action_label}
                      </div>
                    )}
                    <div className="rounded-lg bg-accent/10 p-3 max-h-[58vh] overflow-y-auto scrollbar">
                      <ReportMarkdown content={activeReport.content} />
                    </div>
                    {(activeReport.prompt_context || activeReport.context_payload || activeReport.news_debug) && (
                      <details className="rounded-lg border border-border/40 bg-accent/10 p-3">
                        <summary className="cursor-pointer text-[12px] text-muted-foreground select-none">查看分析上下文</summary>
                        {activeReport.prompt_stats ? (
                          <div className="mt-2">
                            <div className="text-[11px] text-muted-foreground mb-1">Prompt统计</div>
                            <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words overflow-x-auto">{JSON.stringify(activeReport.prompt_stats, null, 2)}</pre>
                          </div>
                        ) : null}
                        {activeReport.news_debug ? (
                          <div className="mt-2">
                            <div className="text-[11px] text-muted-foreground mb-1">新闻注入明细</div>
                            <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words overflow-x-auto">{JSON.stringify(activeReport.news_debug, null, 2)}</pre>
                          </div>
                        ) : null}
                        {activeReport.context_payload ? (
                          <div className="mt-2">
                            <div className="text-[11px] text-muted-foreground mb-1">上下文快照</div>
                            <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words overflow-x-auto max-h-[220px] overflow-y-auto">{JSON.stringify(activeReport.context_payload, null, 2)}</pre>
                          </div>
                        ) : null}
                        {activeReport.prompt_context ? (
                          <div className="mt-2">
                            <div className="text-[11px] text-muted-foreground mb-1">Prompt原文</div>
                            <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words overflow-x-auto max-h-[220px] overflow-y-auto">{activeReport.prompt_context}</pre>
                          </div>
                        ) : null}
                      </details>
                    )}
                  </div>
                )}
                </div>
              </div>
            )}

            {tab === 'deep' && (
              <div className="space-y-3">
                {deepResult && (
                  <div className="flex justify-end">
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-8 px-2.5"
                      onClick={() =>
                        window.open(
                          `/analysis/${symbol}/${deepResult.timestamp ? String(deepResult.timestamp).slice(0, 10) : new Date().toISOString().slice(0, 10)}`,
                          '_blank',
                        )
                      }
                    >
                      打开详情页 ↗
                    </Button>
                  </div>
                )}
                <DeepAnalysisSection
                  symbol={symbol}
                  loading={deepLoading}
                  loaded={deepLoaded}
                  result={deepResult}
                  history={deepHistory}
                  historyLoading={deepHistoryLoading}
                  runStage={deepRunStage}
                  progress={deepProgress}
                  traceId={deepTraceId}
                  budget={deepBudget}
                  triggerError={deepTriggerError}
                  analysisMode={deepAnalysisMode}
                  onAnalysisModeChange={setDeepAnalysisMode}
                  showAnalyst={deepShowAnalyst}
                  setShowAnalyst={setDeepShowAnalyst}
                  showDebate={deepShowDebate}
                  setShowDebate={setDeepShowDebate}
                  onRefresh={loadDeepResult}
                  onStart={() => void handleDeepStart(false)}
                  onRerun={() => void handleDeepStart(true)}
                />
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

const DEEP_DECISION_COLOR: Record<string, string> = {
  buy: 'text-emerald-600 dark:text-emerald-400',
  hold: 'text-amber-600 dark:text-amber-400',
  sell: 'text-rose-600 dark:text-rose-400',
}

const DEEP_STAGE_LABEL: Record<string, string> = {
  market: '技术分析师',
  social: '情绪分析师',
  news: '新闻分析师',
  fundamentals: '基本面分析师',
}

const DEEP_PROGRESS_STAGE_LABEL: Record<string, string> = {
  market_analyst: '技术分析师',
  social_analyst: '情绪分析师',
  news_analyst: '新闻分析师',
  fundamentals_analyst: '基本面分析师',
  bull_bear_debate: '看多看空辩论',
  research_manager: '研究主管',
  trader: '交易员决策',
  risk_judge: '风控判定',
  final_decision: 'PM 整合',
}

const DEEP_POLL_INTERVAL_MS = 2000
const DEEP_NOT_FOUND_GRACE_MS = 60_000
const DEEP_TRACE_STORAGE_PREFIX = 'panwatch:tradingagents:running:'
const DEEP_TRACE_MAX_AGE_MS = 20 * 60 * 1000

function loadDeepRunningTrace(stockSymbol: string): string | null {
  try {
    const raw = localStorage.getItem(DEEP_TRACE_STORAGE_PREFIX + stockSymbol)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { traceId: string; startedAt: number }
    if (!parsed.traceId || !parsed.startedAt) return null
    if (Date.now() - parsed.startedAt > DEEP_TRACE_MAX_AGE_MS) {
      localStorage.removeItem(DEEP_TRACE_STORAGE_PREFIX + stockSymbol)
      return null
    }
    return parsed.traceId
  } catch {
    return null
  }
}

function saveDeepRunningTrace(stockSymbol: string, traceId: string): void {
  try {
    localStorage.setItem(
      DEEP_TRACE_STORAGE_PREFIX + stockSymbol,
      JSON.stringify({ traceId, startedAt: Date.now() }),
    )
  } catch {
    /* ignore */
  }
}

function clearDeepRunningTrace(stockSymbol: string): void {
  try {
    localStorage.removeItem(DEEP_TRACE_STORAGE_PREFIX + stockSymbol)
  } catch {
    /* ignore */
  }
}

function formatDeepElapsed(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return m > 0 ? `${m}分${s}秒` : `${s}秒`
}

function DeepAnalysisSection({
  symbol,
  loading,
  loaded,
  result,
  history,
  historyLoading,
  runStage,
  progress,
  traceId,
  budget,
  triggerError,
  analysisMode,
  onAnalysisModeChange,
  showAnalyst,
  setShowAnalyst,
  showDebate,
  setShowDebate,
  onRefresh,
  onStart,
  onRerun,
}: {
  symbol: string
  loading: boolean
  loaded: boolean
  result: DeepAnalysisResult | null
  history: HistoryComparisonResponse | null
  historyLoading: boolean
  runStage: 'idle' | 'running' | 'done' | 'error'
  progress: ProgressResponse | null
  traceId: string | null
  budget: BudgetInfo | null
  triggerError: string
  analysisMode: DeepAnalysisMode
  onAnalysisModeChange: (mode: DeepAnalysisMode) => void
  showAnalyst: boolean
  setShowAnalyst: (v: boolean) => void
  showDebate: boolean
  setShowDebate: (v: boolean) => void
  onRefresh: () => void
  onStart: () => void
  onRerun: () => void
}) {
  if (loading && !loaded) {
    return (
      <div className="card p-6 text-center text-[12px] text-muted-foreground">
        <span className="inline-block w-4 h-4 border-2 border-primary/30 border-t-primary rounded-full animate-spin mr-2 align-middle" />
        正在加载深度分析报告...
      </div>
    )
  }

  if (runStage === 'running') {
    const elapsed = progress?.elapsed_sec ?? 0
    const cost = progress?.total_cost_usd ?? 0
    const stages = progress?.stages ?? []
    return (
      <div className="card p-4 space-y-3 text-[13px]">
        <div className="rounded-lg bg-accent/30 p-3 space-y-2">
          <div className="flex items-center gap-2">
            <span className="inline-block w-3 h-3 rounded-full bg-primary animate-pulse" />
            <span className="font-medium">深度分析进行中...</span>
            <span className="ml-auto text-[11px] text-muted-foreground">
              已用 {formatDeepElapsed(elapsed)} · ${cost.toFixed(4)}
            </span>
          </div>
          <div className="space-y-1 mt-2">
            {stages.length > 0 ? stages.map((stage) => (
              <DeepProgressStageRow key={stage.name} stage={stage} />
            )) : (
              <div className="text-[12px] text-muted-foreground">准备中...</div>
            )}
          </div>
          {traceId ? (
            <div className="text-[10px] text-muted-foreground/70 font-mono">
              trace_id: {traceId.slice(0, 16)}...
            </div>
          ) : null}
        </div>
        <div className="text-[11px] text-muted-foreground">
          分析需 3-8 分钟，可切换其他标签页；完成后会通过通知渠道推送，也可稍后回来刷新查看。
        </div>
      </div>
    )
  }

  if (runStage === 'error') {
    return (
      <div className="card p-4 space-y-3 text-[13px]">
        <div className="rounded-lg bg-rose-500/10 border border-rose-500/30 p-3 text-rose-600">
          <div className="font-semibold mb-1">分析失败</div>
          <div className="text-[12px]">{triggerError || '未知错误'}</div>
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onStart}>重试</Button>
        </div>
      </div>
    )
  }

  if (!result) {
    const overBudget = budget?.exceeded && budget.over_budget_action === 'reject'
    const est = budget?.estimate_next_run
    return (
      <div className="space-y-3 text-[13px]">
        {history?.items?.length ? (
          <DeepHistoryComparison history={history} loading={historyLoading} />
        ) : null}
        <div className="card p-4 space-y-3">
          <div className="text-center text-[12px] text-muted-foreground">暂无深度分析报告</div>
          <div className="rounded-lg bg-accent/30 p-3 space-y-1.5">
            <div className="font-medium">即将分析：{symbol}</div>
            <DeepAnalysisModePicker mode={analysisMode} onChange={onAnalysisModeChange} />
            <div className="text-[11px] text-muted-foreground mt-2 space-y-0.5">
              <div>⏱ 预计耗时：{deepAnalysisModeEta(analysisMode)}</div>
              {est ? (
                <div>💰 预估成本：${est.cost_low_usd.toFixed(2)} - ${est.cost_high_usd.toFixed(2)} ({est.model})</div>
              ) : (
                <div>💰 预估成本：加载中...</div>
              )}
              <div>ℹ️ 异步执行，可关闭弹窗，完成时推送通知</div>
            </div>
          </div>
          {budget && (
            <div className={`rounded-lg p-3 text-[12px] ${overBudget ? 'bg-rose-500/10 border border-rose-500/30' : 'bg-accent/20'}`}>
              <div className="flex items-center justify-between">
                <span className="font-medium">本月预算</span>
                <span className={overBudget ? 'text-rose-600' : 'text-muted-foreground'}>
                  ${budget.used.toFixed(2)} / ${budget.limit.toFixed(2)}
                  {budget.runs_this_month > 0 && ` · ${budget.runs_this_month} 次`}
                </span>
              </div>
              {overBudget && (
                <div className="text-[11px] text-rose-600 mt-1">
                  ⚠️ 本月预算已用尽，请到「设置 → Agent → TradingAgents」调高预算
                </div>
              )}
            </div>
          )}
          <div className="flex justify-center">
            <Button onClick={onStart} disabled={overBudget} className="gap-1.5">
              <Brain className="w-4 h-4" />
              开始深度分析
            </Button>
          </div>
        </div>
      </div>
    )
  }

  const rawData = (result?.raw_data || {}) as Partial<DeepAnalysisResult['raw_data']>
  const sug = rawData.suggestion
  const reports = rawData.analyst_reports || { market: '', social: '', news: '', fundamentals: '' }
  const debate = rawData.debate_history
  const costUsd = rawData.cost_usd

  return (
    <div className="space-y-3 text-[13px]">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[11px] text-muted-foreground">
          TradingAgents 深度{result?.timestamp ? ` · ${result.timestamp.slice(0, 16).replace('T', ' ')}` : ''}
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2 text-[11px]"
            onClick={onRerun}
          >
            重新分析
          </Button>
          <Button variant="ghost" size="sm" className="h-7 px-2 text-[11px]" onClick={onRefresh} disabled={loading || historyLoading}>
            <RefreshCw className={`w-3.5 h-3.5 ${loading || historyLoading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </div>

      {sug && (
        <div className="rounded-lg bg-accent/30 p-4 space-y-2">
          <div className="flex items-center gap-3">
            <span className={`text-[20px] font-bold ${DEEP_DECISION_COLOR[sug.action] || ''}`}>
              {sug.action_label}
            </span>
            {typeof sug.confidence === 'number' && (
              <span className="text-[12px] text-muted-foreground">
                置信度 {sug.confidence.toFixed(1)} / 10
              </span>
            )}
          </div>
          {sug.reason && <div className="text-[12px] text-foreground/80">{sug.reason.slice(0, 240)}</div>}
          {typeof costUsd === 'number' && (
            <div className="text-[10px] text-muted-foreground mt-2">成本:${costUsd.toFixed(4)}</div>
          )}
        </div>
      )}

      <DeepHistoryComparison history={history} loading={historyLoading} />

      {result?.content && (
        <div className="rounded-lg border border-border/50 p-4">
          <ReportMarkdown content={result.content} />
        </div>
      )}

      {result && (
        <div>
          <button
            className="text-[12px] text-muted-foreground hover:text-foreground flex items-center gap-1"
            onClick={() => setShowAnalyst(!showAnalyst)}
          >
            {showAnalyst ? '▼' : '▶'} 4 位分析师报告
          </button>
          {showAnalyst && (
            <div className="space-y-3 mt-2 pl-3 border-l-2 border-border/40">
              {(['market', 'social', 'news', 'fundamentals'] as const).map((k) => {
                const text = (reports as unknown as Record<string, string>)[k] || ''
                if (!text) return null
                return (
                  <details key={k} open className="text-[12px]">
                    <summary className="font-medium cursor-pointer">{DEEP_STAGE_LABEL[k] || k}</summary>
                    <div className="mt-2 text-[11px] text-foreground/80 whitespace-pre-wrap">
                      {text.slice(0, 1500)}
                      {text.length > 1500 && '... (截断)'}
                    </div>
                  </details>
                )
              })}
            </div>
          )}
        </div>
      )}

      {debate && debate.history && (
        <div>
          <button
            className="text-[12px] text-muted-foreground hover:text-foreground flex items-center gap-1"
            onClick={() => setShowDebate(!showDebate)}
          >
            {showDebate ? '▼' : '▶'} 看多看空辩论
          </button>
          {showDebate && (
            <div className="mt-2 pl-3 border-l-2 border-border/40 text-[11px] text-foreground/80 whitespace-pre-wrap max-h-96 overflow-y-auto">
              {debate.history}
              {debate.judge_decision && (
                <>
                  <div className="font-medium mt-3 mb-1">研究主管裁决:</div>
                  <div>{debate.judge_decision}</div>
                </>
              )}
            </div>
          )}
        </div>
      )}

      <div className="text-[10px] text-muted-foreground/70 italic border-t border-border/30 pt-2">
        本分析由 AI 多 Agent 框架生成,仅供学习研究参考,不构成任何投资建议。
      </div>
    </div>
  )
}

function DeepProgressStageRow({ stage }: { stage: ProgressStage }) {
  const label = DEEP_PROGRESS_STAGE_LABEL[stage.name] || stage.name
  const done = stage.status === 'done'
  const running = stage.status === 'running'
  return (
    <div className="flex items-center gap-2 text-[12px]">
      <span className={`w-2 h-2 rounded-full shrink-0 ${done ? 'bg-emerald-500' : running ? 'bg-primary animate-pulse' : 'bg-muted-foreground/30'}`} />
      <span className={done ? 'text-foreground' : running ? 'text-primary font-medium' : 'text-muted-foreground'}>
        {label}
      </span>
      {typeof stage.duration_sec === 'number' && stage.duration_sec > 0 && (
        <span className="ml-auto text-[10px] text-muted-foreground">{formatDeepElapsed(stage.duration_sec)}</span>
      )}
    </div>
  )
}

function DeepHistoryComparison({
  history,
  loading,
}: {
  history: HistoryComparisonResponse | null
  loading: boolean
}) {
  if (loading && !history) {
    return (
      <div className="rounded-lg border border-border/40 p-3 text-[11px] text-muted-foreground text-center">
        历史对比加载中...
      </div>
    )
  }
  if (!history || history.items.length === 0) return null

  const stats = history.stats
  const fmtPct = (v: number | null): string => (v == null ? '-' : `${(v * 100).toFixed(0)}%`)
  const fmtRet = (v: number | null): string => (v == null ? '-' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`)
  const retCls = (v: number | null): string =>
    v == null ? 'text-muted-foreground' : v > 0 ? 'text-emerald-600 dark:text-emerald-400' : v < 0 ? 'text-rose-600 dark:text-rose-400' : 'text-muted-foreground'

  return (
    <div className="rounded-lg border border-border/50 p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[12px] font-medium">历史决策 vs 实际涨跌</div>
        <div className="text-[10px] text-muted-foreground">仅基于满 20 个交易日的决策统计</div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">
        <div className="rounded bg-accent/30 px-2 py-1.5">
          <div className="text-muted-foreground">总命中率</div>
          <div className="font-semibold">{fmtPct(stats.overall_hit_rate)}</div>
        </div>
        <div className="rounded bg-accent/30 px-2 py-1.5">
          <div className="text-muted-foreground">买入 ({stats.buy_count})</div>
          <div className="font-semibold text-emerald-600 dark:text-emerald-400">{fmtPct(stats.buy_hit_rate)}</div>
        </div>
        <div className="rounded bg-accent/30 px-2 py-1.5">
          <div className="text-muted-foreground">卖出 ({stats.sell_count})</div>
          <div className="font-semibold text-rose-600 dark:text-rose-400">{fmtPct(stats.sell_hit_rate)}</div>
        </div>
        <div className="rounded bg-accent/30 px-2 py-1.5">
          <div className="text-muted-foreground">平均 20 日收益</div>
          <div className={`font-semibold ${retCls(stats.avg_return_20d_pct)}`}>{fmtRet(stats.avg_return_20d_pct)}</div>
        </div>
      </div>
      <div className="overflow-x-auto -mx-1 mt-2">
        <table className="w-full text-[11px]">
          <thead className="text-muted-foreground">
            <tr className="border-b border-border/40">
              <th className="text-left px-1 py-1 font-normal">日期</th>
              <th className="text-left px-1 py-1 font-normal">决策</th>
              <th className="text-right px-1 py-1 font-normal">分析价</th>
              <th className="text-right px-1 py-1 font-normal">1日</th>
              <th className="text-right px-1 py-1 font-normal">5日</th>
              <th className="text-right px-1 py-1 font-normal">20日</th>
              <th className="text-center px-1 py-1 font-normal">命中</th>
            </tr>
          </thead>
          <tbody>
            {history.items.map((item, i) => (
              <tr key={`${item.analysis_date}-${i}`} className="border-b border-border/20 hover:bg-accent/10">
                <td className="px-1 py-1 text-muted-foreground whitespace-nowrap">{item.analysis_date}</td>
                <td className="px-1 py-1">
                  <span className={DEEP_DECISION_COLOR[item.action] || ''}>{item.action_label}</span>
                  {typeof item.confidence === 'number' && (
                    <span className="text-muted-foreground text-[10px] ml-1">({item.confidence.toFixed(1)})</span>
                  )}
                </td>
                <td className="px-1 py-1 text-right text-foreground/80">{item.price_at_analysis ?? '-'}</td>
                <td className={`px-1 py-1 text-right ${retCls(item.return_1d_pct)}`}>{fmtRet(item.return_1d_pct)}</td>
                <td className={`px-1 py-1 text-right ${retCls(item.return_5d_pct)}`}>{fmtRet(item.return_5d_pct)}</td>
                <td className={`px-1 py-1 text-right ${retCls(item.return_20d_pct)}`}>{fmtRet(item.return_20d_pct)}</td>
                <td className="px-1 py-1 text-center">
                  {item.hit_20d == null ? <span className="text-muted-foreground">-</span> : item.hit_20d ? '✓' : '✗'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
