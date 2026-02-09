import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import {
  TrendingUp,
  RefreshCw,
  ArrowUpRight,
  ArrowDownRight,
  Wallet,
  PiggyBank,
  ChevronRight,
  Activity,
  BarChart3,
  Sparkles,
  Newspaper,
  Layers,
  Sun,
  Moon,
} from 'lucide-react'
import { fetchAPI, useLocalStorage } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select'
import { Onboarding } from '@/components/onboarding'
import { type SuggestionInfo, type KlineSummary } from '@/components/suggestion-badge'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import StockInsightModal from '@/components/stock-insight-modal'

interface MarketIndex {
  symbol: string
  name: string
  market: string
  current_price: number | null
  change_pct: number | null
  change_amount: number | null
  prev_close: number | null
}

interface MarketStatus {
  code: string
  name: string
  status: string
  status_text: string
  is_trading: boolean
  sessions: string[]
  local_time: string
}

interface HotStockItem {
  symbol: string
  name: string
  price: number | null
  change_pct: number | null
  turnover: number | null
}

interface HotBoardItem {
  code: string
  name: string
  change_pct: number | null
  turnover: number | null
}

interface PortfolioSummary {
  accounts: AccountSummary[]
  total: {
    total_market_value: number
    total_cost: number
    total_pnl: number
    total_pnl_pct: number
    available_funds: number
    total_assets: number
  }
  exchange_rates?: {
    HKD_CNY: number
    USD_CNY?: number
  }
}

interface AccountSummary {
  id: number
  name: string
  available_funds: number
  total_cost: number
  total_market_value: number
  total_pnl: number
  total_pnl_pct: number
  total_assets: number
  positions: Position[]
}

interface Position {
  id: number
  stock_id: number
  symbol: string
  name: string
  market: string
  cost_price: number
  quantity: number
  invested_amount: number | null
  trading_style: string
  current_price: number | null
  change_pct: number | null
}

interface MonitorStock {
  symbol: string
  name: string
  market: string
  current_price: number
  change_pct: number
  open_price: number | null
  high_price: number | null
  low_price: number | null
  volume: number | null
  turnover: number | null
  alert_type: string | null
  has_position: boolean
  cost_price: number | null
  pnl_pct: number | null
  trading_style: string | null
  kline: KlineSummary | null
  suggestion: SuggestionInfo | null
}

interface Stock {
  id: number
  symbol: string
  name: string
  market: string
  enabled: boolean
}

interface QuoteRequestItem {
  symbol: string
  market: string
}

interface QuoteResponse {
  symbol: string
  market: string
  current_price: number | null
  change_pct: number | null
}

type QuoteMap = Record<string, { current_price: number | null; change_pct: number | null }>

interface AnalysisRecord {
  id: number
  agent_name: string
  stock_symbol: string
  analysis_date: string
  title: string
  content: string
  created_at: string
}

const round2 = (value: number) => Math.round(value * 100) / 100

const mergePortfolioQuotes = (
  portfolio: PortfolioSummary | null,
  quotes: QuoteMap
): PortfolioSummary | null => {
  if (!portfolio) return null

  const hkdRate = portfolio.exchange_rates?.HKD_CNY ?? 0.92
  const usdRate = portfolio.exchange_rates?.USD_CNY ?? 7.25

  let grandMarketValue = 0
  let grandCost = 0
  let grandAvailable = 0

  const accounts = portfolio.accounts.map(account => {
    let accMarketValue = 0
    let accCost = 0

    for (const pos of account.positions) {
      const quote = quotes[`${pos.market}:${pos.symbol}`]
      const current_price = quote?.current_price ?? pos.current_price ?? null
      const rate = pos.market === 'HK' ? hkdRate : pos.market === 'US' ? usdRate : 1
      const cost = pos.cost_price * pos.quantity * rate
      accCost += cost

      if (current_price != null) {
        accMarketValue += current_price * pos.quantity * rate
      }
    }

    const accPnl = accMarketValue - accCost
    const accPnlPct = accCost > 0 ? (accPnl / accCost * 100) : 0
    const accTotalAssets = accMarketValue + account.available_funds

    grandMarketValue += accMarketValue
    grandCost += accCost
    grandAvailable += account.available_funds

    return {
      ...account,
      total_market_value: round2(accMarketValue),
      total_cost: round2(accCost),
      total_pnl: round2(accPnl),
      total_pnl_pct: round2(accPnlPct),
      total_assets: round2(accTotalAssets),
    }
  })

  const grandPnl = grandMarketValue - grandCost
  const grandPnlPct = grandCost > 0 ? (grandPnl / grandCost * 100) : 0
  const grandTotalAssets = grandMarketValue + grandAvailable

  return {
    ...portfolio,
    accounts,
    total: {
      total_market_value: round2(grandMarketValue),
      total_cost: round2(grandCost),
      total_pnl: round2(grandPnl),
      total_pnl_pct: round2(grandPnlPct),
      available_funds: round2(grandAvailable),
      total_assets: round2(grandTotalAssets),
    },
  }
}

export default function DashboardPage() {
  const navigate = useNavigate()

  // Market indices
  const [indices, setIndices] = useState<MarketIndex[]>([])
  const [indicesLoading, setIndicesLoading] = useState(true)

  // Market status
  const [marketStatus, setMarketStatus] = useState<MarketStatus[]>([])

  // Portfolio
  const [portfolio, setPortfolio] = useState<PortfolioSummary | null>(null)
  const [portfolioRaw, setPortfolioRaw] = useState<PortfolioSummary | null>(null)
  const [portfolioLoading, setPortfolioLoading] = useState(false)
  const hasPortfolio = portfolio && portfolio.accounts.length > 0

  // Watchlist
  const [stocks, setStocks] = useState<Stock[]>([])
  // Keyed by `${market}:${symbol}` to avoid cross-market collisions
  const [quotes, setQuotes] = useState<QuoteMap>({})
  const [quotesLoading, setQuotesLoading] = useState(false)
  const hasWatchlist = stocks.filter(s => s.enabled).length > 0

  // Unified stock insight modal
  const [insightOpen, setInsightOpen] = useState(false)
  const [insightSymbol, setInsightSymbol] = useState('')
  const [insightMarket, setInsightMarket] = useState('CN')
  const [insightName, setInsightName] = useState<string | undefined>(undefined)
  const [insightHasPosition, setInsightHasPosition] = useState(false)

  // Monitor stocks
  const [monitorStocks, setMonitorStocks] = useState<MonitorStock[]>([])
  const [scanning, setScanning] = useState(false)
  const [aiScanRunning, setAiScanRunning] = useState(false)
  const [enableAIAnalysis, setEnableAIAnalysis] = useState(true)
  const scanRequestRef = useRef(0)

  // Auto-refresh (持久化到 localStorage)
  const [autoRefresh, setAutoRefresh] = useLocalStorage('panwatch_dashboard_autoRefresh', false)
  const [refreshInterval, setRefreshInterval] = useLocalStorage('panwatch_dashboard_refreshInterval', 30)
  const [lastRefreshTime, setLastRefreshTime] = useState<Date | null>(null)
  const [lastScanTime, setLastScanTime] = useState<Date | null>(null)
  const refreshTimerRef = useRef<ReturnType<typeof setInterval>>()

  // Onboarding
  const [showOnboarding, setShowOnboarding] = useState(false)

  // AI Insights
  const [dailyReport, setDailyReport] = useState<AnalysisRecord | null>(null)
  const [premarketOutlook, setPremarketOutlook] = useState<AnalysisRecord | null>(null)
  const [newsDigest, setNewsDigest] = useState<AnalysisRecord | null>(null)
  const [insightsLoading, setInsightsLoading] = useState(false)
  const [previewInsight, setPreviewInsight] = useState<AnalysisRecord | null>(null)

  // Discovery (Hot boards / stocks)
  const [discoverTab, setDiscoverTab] = useLocalStorage<'boards' | 'stocks'>('panwatch_dashboard_discoverTab', 'boards')
  const [discoverMarket, setDiscoverMarket] = useLocalStorage<'CN' | 'HK' | 'US'>('panwatch_dashboard_discoverMarket', 'CN')
  const [stocksMode, setStocksMode] = useLocalStorage<'turnover' | 'gainers' | 'for_you'>('panwatch_dashboard_stocksMode', 'for_you')
  const [boardsMode, setBoardsMode] = useLocalStorage<'gainers' | 'turnover'>('panwatch_dashboard_boardsMode', 'gainers')
  const [hotStocks, setHotStocks] = useState<HotStockItem[]>([])
  const [hotBoards, setHotBoards] = useState<HotBoardItem[]>([])
  const [discoverLoading, setDiscoverLoading] = useState(false)
  const [discoverError, setDiscoverError] = useState('')
  const [boardDialogOpen, setBoardDialogOpen] = useState(false)
  const [activeBoard, setActiveBoard] = useState<HotBoardItem | null>(null)
  const [boardStocks, setBoardStocks] = useState<HotStockItem[]>([])
  const discoveryCacheRef = useRef<{
    boards: Record<string, { ts: number; data: HotBoardItem[] }>
    stocks: Record<string, { ts: number; data: HotStockItem[] }>
  }>({ boards: {}, stocks: {} })

  const watchlistSet = useMemo(() => {
    return new Set((stocks || []).filter(s => s.enabled).map(s => `${s.market}:${s.symbol}`))
  }, [stocks])

  const holdingSet = useMemo(() => {
    const set = new Set<string>()
    for (const acc of portfolioRaw?.accounts || []) {
      for (const p of acc.positions || []) {
        set.add(`${p.market}:${p.symbol}`)
      }
    }
    return set
  }, [portfolioRaw])

  const stylePreference = useMemo(() => {
    const score: Record<string, number> = { short: 0, swing: 0, long: 0 }
    for (const acc of portfolioRaw?.accounts || []) {
      for (const p of acc.positions || []) {
        if (!p.trading_style) continue
        if (p.trading_style in score) score[p.trading_style] += 1
      }
    }
    const ranked = Object.entries(score).sort((a, b) => b[1] - a[1])
    return ranked[0]?.[1] ? ranked[0][0] : null
  }, [portfolioRaw])

  // Initial load
  useEffect(() => {
    loadIndices()
    loadMarketStatus()
    loadPortfolio()
    loadWatchlist()
    loadAIInsights()
    loadDiscovery('boards')
    loadDiscovery('stocks', { silent: true })

    // Check if onboarding should be shown
    const onboardingCompleted = localStorage.getItem('panwatch_onboarding_completed')
    if (!onboardingCompleted) {
      setShowOnboarding(true)
    }
  }, [])

  // 自选股加载后自动获取监控数据
  const initialScanDone = useRef(false)
  useEffect(() => {
    if (hasWatchlist && !initialScanDone.current) {
      initialScanDone.current = true
      scanAlerts()
    }
  }, [hasWatchlist])

  const loadIndices = async () => {
    setIndicesLoading(true)
    try {
      const data = await fetchAPI<MarketIndex[]>('/market/indices')
      setIndices(data)
    } catch (e) {
      console.error('获取指数失败:', e)
    } finally {
      setIndicesLoading(false)
    }
  }

  const loadMarketStatus = async () => {
    try {
      const data = await fetchAPI<MarketStatus[]>('/stocks/markets/status')
      setMarketStatus(data)
    } catch (e) {
      console.error('获取市场状态失败:', e)
    }
  }

  const loadPortfolio = async () => {
    setPortfolioLoading(true)
    try {
      const data = await fetchAPI<PortfolioSummary>('/portfolio/summary?include_quotes=false')
      setPortfolioRaw(data)
      setPortfolio(mergePortfolioQuotes(data, quotes))
    } catch (e) {
      console.error('获取持仓失败:', e)
    } finally {
      setPortfolioLoading(false)
    }
  }

  const loadWatchlist = async () => {
    try {
      const stocksData = await fetchAPI<Stock[]>('/stocks')
      setStocks(stocksData)
    } catch (e) {
      console.error('获取自选股失败:', e)
    }
  }

  const buildQuoteItems = useCallback((): QuoteRequestItem[] => {
    const items: QuoteRequestItem[] = []
    const seen = new Set<string>()

    for (const stock of stocks) {
      if (!stock.enabled) continue
      const key = `${stock.market}:${stock.symbol}`
      if (seen.has(key)) continue
      seen.add(key)
      items.push({ symbol: stock.symbol, market: stock.market })
    }

    for (const account of portfolioRaw?.accounts || []) {
      for (const pos of account.positions) {
        const key = `${pos.market}:${pos.symbol}`
        if (seen.has(key)) continue
        seen.add(key)
        items.push({ symbol: pos.symbol, market: pos.market })
      }
    }

    return items
  }, [stocks, portfolioRaw])

  const refreshQuotes = useCallback(async () => {
    const items = buildQuoteItems()
    if (items.length === 0) return

    setQuotesLoading(true)
    try {
      const data = await fetchAPI<QuoteResponse[]>('/quotes/batch', {
        method: 'POST',
        body: JSON.stringify({ items }),
      })
      const map: QuoteMap = {}
      for (const item of data) {
        map[`${item.market}:${item.symbol}`] = {
          current_price: item.current_price ?? null,
          change_pct: item.change_pct ?? null,
        }
      }
      setQuotes(map)
      setLastRefreshTime(new Date())
    } catch (e) {
      console.warn('刷新行情失败:', e)
    } finally {
      setQuotesLoading(false)
    }
  }, [buildQuoteItems])

  const openStockInsight = useCallback((symbol: string, market: string, name?: string, hasPosition?: boolean) => {
    setInsightSymbol(symbol)
    setInsightMarket(market || 'CN')
    setInsightName(name)
    setInsightHasPosition(!!hasPosition)
    setInsightOpen(true)
  }, [])

  useEffect(() => {
    if (!portfolioRaw) return
    setPortfolio(mergePortfolioQuotes(portfolioRaw, quotes))
  }, [portfolioRaw, quotes])

  useEffect(() => {
    if (stocks.length === 0 && (!portfolioRaw || portfolioRaw.accounts.length === 0)) return
    refreshQuotes()
  }, [stocks, portfolioRaw, refreshQuotes])

  // Auto-refresh timer
  useEffect(() => {
    if (autoRefresh) {
      refreshQuotes()
      refreshTimerRef.current = setInterval(() => {
        refreshQuotes()
      }, refreshInterval * 1000)
    } else {
      if (refreshTimerRef.current) {
        clearInterval(refreshTimerRef.current)
        refreshTimerRef.current = undefined
      }
    }

    return () => {
      if (refreshTimerRef.current) {
        clearInterval(refreshTimerRef.current)
      }
    }
  }, [autoRefresh, refreshInterval, refreshQuotes])

  const loadAIInsights = async () => {
    setInsightsLoading(true)
    try {
      const [dailyData, premarketData, newsData] = await Promise.all([
        fetchAPI<AnalysisRecord[]>('/history?agent_name=daily_report&limit=1'),
        fetchAPI<AnalysisRecord[]>('/history?agent_name=premarket_outlook&limit=1'),
        fetchAPI<AnalysisRecord[]>('/history?agent_name=news_digest&limit=1'),
      ])
      setDailyReport(dailyData.length > 0 ? dailyData[0] : null)
      setPremarketOutlook(premarketData.length > 0 ? premarketData[0] : null)
      setNewsDigest(newsData.length > 0 ? newsData[0] : null)
    } catch (e) {
      console.error('获取 AI 洞察失败:', e)
    } finally {
      setInsightsLoading(false)
    }
  }

  const loadDiscovery = async (which?: 'boards' | 'stocks', opts?: { silent?: boolean; force?: boolean }) => {
    const tab = which || discoverTab
    const silent = !!opts?.silent
    const force = !!opts?.force
    if (discoverMarket !== 'CN') {
      if (tab === 'boards') setHotBoards([])
      else setHotStocks([])
      setDiscoverError('')
      return
    }

    const cacheKey = tab === 'boards'
      ? `${discoverMarket}:${boardsMode}`
      : `${discoverMarket}:${stocksMode}`
    const now = Date.now()
    const ttlMs = 60 * 1000
    const cache = tab === 'boards'
      ? discoveryCacheRef.current.boards[cacheKey]
      : discoveryCacheRef.current.stocks[cacheKey]
    const isFresh = cache && (now - cache.ts) < ttlMs

    if (!force && isFresh) {
      if (tab === 'boards') setHotBoards(cache.data as HotBoardItem[])
      else setHotStocks(cache.data as HotStockItem[])
      return
    }

    if (!silent) {
      setDiscoverLoading(true)
      setDiscoverError('')
    }
    try {
      if (tab === 'boards') {
        const items = await fetchAPI<HotBoardItem[]>(
          `/discovery/boards?market=${discoverMarket}&mode=${boardsMode}&limit=12`
        )
        const normalized = items || []
        setHotBoards(normalized)
        discoveryCacheRef.current.boards[cacheKey] = { ts: now, data: normalized }
      } else {
        if (stocksMode === 'for_you') {
          const [turnoverItems, gainerItems] = await Promise.all([
            fetchAPI<HotStockItem[]>(`/discovery/stocks?market=${discoverMarket}&mode=turnover&limit=20`),
            fetchAPI<HotStockItem[]>(`/discovery/stocks?market=${discoverMarket}&mode=gainers&limit=20`),
          ])
          const map = new Map<string, HotStockItem>()
          for (const item of [...(turnoverItems || []), ...(gainerItems || [])]) {
            map.set(item.symbol, item)
          }
          const normalized = Array.from(map.values())
          setHotStocks(normalized)
          discoveryCacheRef.current.stocks[cacheKey] = { ts: now, data: normalized }
        } else {
          const items = await fetchAPI<HotStockItem[]>(
            `/discovery/stocks?market=${discoverMarket}&mode=${stocksMode}&limit=20`
          )
          const normalized = items || []
          setHotStocks(normalized)
          discoveryCacheRef.current.stocks[cacheKey] = { ts: now, data: normalized }
        }
      }
    } catch (e) {
      if (!silent) {
        setDiscoverError(e instanceof Error ? e.message : '加载失败')
        if (tab === 'boards') setHotBoards([])
        else setHotStocks([])
      }
    } finally {
      if (!silent) setDiscoverLoading(false)
    }
  }

  const openBoard = async (b: HotBoardItem) => {
    setActiveBoard(b)
    setBoardStocks([])
    setBoardDialogOpen(true)
    try {
      const items = await fetchAPI<HotStockItem[]>(
        `/discovery/boards/${encodeURIComponent(b.code)}/stocks?mode=gainers&limit=20`
      )
      setBoardStocks(items || [])
    } catch {
      setBoardStocks([])
    }
  }

  const scanAlerts = useCallback(async () => {
    if (!hasWatchlist) return

    const reqId = ++scanRequestRef.current
    setScanning(true)
    try {
      // Phase 1: always get fast scan first (no AI), render immediately.
      const result = await fetchAPI<{ stocks: MonitorStock[]; available_funds: number }>('/agents/intraday/scan', { method: 'POST' })
      if (reqId !== scanRequestRef.current) return
      setMonitorStocks(result.stocks || [])
      setLastRefreshTime(new Date())
      setLastScanTime(new Date())
    } catch (e) {
      console.error('扫描失败:', e)
    } finally {
      if (reqId === scanRequestRef.current) setScanning(false)
    }

    // Phase 2: enrich with AI suggestions in background.
    if (!enableAIAnalysis) return
    setAiScanRunning(true)
    try {
      const aiResult = await fetchAPI<{ stocks: MonitorStock[]; available_funds: number }>('/agents/intraday/scan?analyze=true', { method: 'POST' })
      if (reqId !== scanRequestRef.current) return
      const aiStocks = aiResult.stocks || []
      setMonitorStocks(prev => {
        if (!prev || prev.length === 0) return aiStocks
        const aiMap = new Map(aiStocks.map(s => [`${s.market}:${s.symbol}`, s] as const))
        const merged = prev.map(s => aiMap.get(`${s.market}:${s.symbol}`) || s)
        const existing = new Set(merged.map(s => `${s.market}:${s.symbol}`))
        for (const s of aiStocks) {
          const key = `${s.market}:${s.symbol}`
          if (!existing.has(key)) merged.push(s)
        }
        return merged
      })
      setLastRefreshTime(new Date())
      setLastScanTime(new Date())
    } catch (e) {
      console.error('AI扫描失败:', e)
    } finally {
      if (reqId === scanRequestRef.current) setAiScanRunning(false)
    }
  }, [hasWatchlist, enableAIAnalysis])

  const handleRefresh = useCallback(async () => {
    await refreshQuotes()
  }, [refreshQuotes])

  const formatMoney = (value: number) => {
    if (Math.abs(value) >= 10000) {
      return `${(value / 10000).toFixed(2)}万`
    }
    return value.toFixed(2)
  }

  const formatIndexPrice = (value: number | null) => {
    if (value === null) return '--'
    if (value >= 10000) {
      return value.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
    }
    return value.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  }

  const marketBadge = (m: string) => {
    if (m === 'HK') return { style: 'bg-orange-500/10 text-orange-600', label: '港' }
    if (m === 'US') return { style: 'bg-green-500/10 text-green-600', label: '美' }
    return { style: 'bg-blue-500/10 text-blue-600', label: 'A' }
  }

  const handleOnboardingComplete = () => {
    localStorage.setItem('panwatch_onboarding_completed', 'true')
    setShowOnboarding(false)
    // Reload data in case sample stocks were added
    loadWatchlist()
  }

  const portfolioDayPnl = useMemo(() => {
    if (!portfolioRaw) return null
    const hkdRate = portfolioRaw.exchange_rates?.HKD_CNY ?? 0.92
    const usdRate = portfolioRaw.exchange_rates?.USD_CNY ?? 7.25

    let dayPnl = 0
    let prevMv = 0
    let posCount = 0

    for (const acc of portfolioRaw.accounts || []) {
      for (const p of acc.positions || []) {
        const q = quotes[`${p.market}:${p.symbol}`]
        if (!q || q.current_price == null || q.change_pct == null) continue
        const prev = q.change_pct === -100 ? null : (q.current_price / (1 + q.change_pct / 100))
        if (prev == null || !isFinite(prev)) continue
        const fx = p.market === 'HK' ? hkdRate : p.market === 'US' ? usdRate : 1
        const qty = p.quantity || 0
        posCount += 1
        dayPnl += (q.current_price - prev) * qty * fx
        prevMv += prev * qty * fx
      }
    }

    return {
      day_pnl: dayPnl,
      day_pnl_pct: prevMv > 0 ? (dayPnl / prevMv * 100) : 0,
      has_data: posCount > 0,
    }
  }, [portfolioRaw, quotes])

  const dayMovers = useMemo(() => {
    if (!portfolioRaw) return { worst: null as null | { market: string; symbol: string; name: string; day_pnl: number; day_pct: number }, best: null as null | { market: string; symbol: string; name: string; day_pnl: number; day_pct: number } }
    const hkdRate = portfolioRaw.exchange_rates?.HKD_CNY ?? 0.92
    const usdRate = portfolioRaw.exchange_rates?.USD_CNY ?? 7.25

    const rows: Array<{ market: string; symbol: string; name: string; day_pnl: number; day_pct: number }> = []
    for (const acc of portfolioRaw.accounts || []) {
      for (const p of acc.positions || []) {
        const q = quotes[`${p.market}:${p.symbol}`]
        if (!q || q.current_price == null || q.change_pct == null) continue
        const prev = q.change_pct === -100 ? null : (q.current_price / (1 + q.change_pct / 100))
        if (prev == null || !isFinite(prev)) continue
        const fx = p.market === 'HK' ? hkdRate : p.market === 'US' ? usdRate : 1
        const qty = p.quantity || 0
        const pnl = (q.current_price - prev) * qty * fx
        const prevMv = prev * qty * fx
        const pct = prevMv > 0 ? (pnl / prevMv * 100) : 0
        rows.push({ market: p.market, symbol: p.symbol, name: p.name, day_pnl: pnl, day_pct: pct })
      }
    }

    if (rows.length === 0) return { worst: null, best: null }
    const worst = rows.slice().sort((a, b) => a.day_pnl - b.day_pnl)[0]
    const best = rows.slice().sort((a, b) => b.day_pnl - a.day_pnl)[0]
    return { worst, best }
  }, [portfolioRaw, quotes])

  const stripMarkdown = (input: string): string => {
    return (input || '')
      .replace(/```[\s\S]*?```/g, ' ')
      .replace(/`[^`]*`/g, ' ')
      .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
      .replace(/\[[^\]]+\]\([^)]*\)/g, ' ')
      .replace(/[#>*_~-]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
  }

  const insightCards = useMemo(() => {
    const cards = [
      { key: 'daily', title: '盘后日报', icon: Moon, style: 'bg-orange-500/10 text-orange-500', record: dailyReport },
      { key: 'premarket', title: '盘前分析', icon: Sun, style: 'bg-amber-500/10 text-amber-500', record: premarketOutlook },
      { key: 'news', title: '新闻速递', icon: Newspaper, style: 'bg-blue-500/10 text-blue-500', record: newsDigest },
    ]
    return cards.filter(c => !!c.record).map(c => ({
      ...c,
      preview: stripMarkdown(c.record?.content || '').slice(0, 120),
    }))
  }, [dailyReport, premarketOutlook, newsDigest])

  const actionableSignals = useMemo(() => {
    const urgency = (s: MonitorStock) => {
      let score = 0
      if (s.alert_type) score += 4
      if (s.suggestion?.should_alert) score += 3
      if (s.suggestion && ['sell', 'reduce', 'avoid', 'alert', 'buy', 'add'].includes(s.suggestion.action)) score += 2
      if (s.has_position) score += 1
      return score
    }
    return (monitorStocks || [])
      .filter(s => s.alert_type || s.suggestion?.should_alert || s.suggestion)
      .slice()
      .sort((a, b) => urgency(b) - urgency(a))
      .slice(0, 6)
      .map(s => ({
        ...s,
        _source: s.suggestion?.agent_label || '盘中监控',
      }))
  }, [monitorStocks])

  const personalizedHotStocks = useMemo(() => {
    const monitorMap = new Map<string, MonitorStock>()
    for (const s of monitorStocks || []) monitorMap.set(`${s.market}:${s.symbol}`, s)

    const scored = (hotStocks || []).map(stock => {
      const key = `CN:${stock.symbol}`
      const reasons: string[] = []
      let score = 0

      const pctAbs = Math.abs(stock.change_pct || 0)
      const turnoverScore = Math.min((stock.turnover || 0) / 1e8, 8)
      score += turnoverScore + pctAbs * 0.6

      if (holdingSet.has(key)) {
        score += 10
        reasons.push('持仓相关')
      } else if (watchlistSet.has(key)) {
        score += 6
        reasons.push('自选相关')
      }

      const monitor = monitorMap.get(key)
      if (monitor?.suggestion?.should_alert || monitor?.alert_type) {
        score += 5
        reasons.push('监控信号')
      }

      if (stylePreference === 'short' && pctAbs >= 3) {
        score += 3
        reasons.push('短线风格匹配')
      } else if (stylePreference === 'swing' && pctAbs >= 1.5 && pctAbs <= 6) {
        score += 2
        reasons.push('波段风格匹配')
      } else if (stylePreference === 'long' && pctAbs <= 4) {
        score += 2
        reasons.push('长线波动适中')
      }

      if (reasons.length === 0) reasons.push('市场活跃度高')
      return { ...stock, _score: score, _reasons: reasons.slice(0, 2) }
    })

    return scored.sort((a, b) => b._score - a._score)
  }, [hotStocks, holdingSet, watchlistSet, stylePreference, monitorStocks])

  const visibleHotStocks = useMemo(() => {
    if (stocksMode === 'for_you') return personalizedHotStocks.slice(0, 8)
    return hotStocks.slice(0, 8)
  }, [stocksMode, personalizedHotStocks, hotStocks])

  useEffect(() => {
    loadDiscovery('boards', { silent: true })
    loadDiscovery('stocks', { silent: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [discoverMarket, boardsMode, stocksMode])

  return (
    <div>
      {/* Onboarding */}
      <Onboarding
        open={showOnboarding}
        onComplete={handleOnboardingComplete}
        hasStocks={hasWatchlist}
      />

      <StockInsightModal
        open={insightOpen}
        onOpenChange={setInsightOpen}
        symbol={insightSymbol}
        market={insightMarket}
        stockName={insightName}
        hasPosition={insightHasPosition}
        onOpenFullDetail={(m, s) => navigate(`/stock/${encodeURIComponent(m)}/${encodeURIComponent(s)}`)}
      />

      {/* Risk dialog removed (was too noisy when empty) */}

      {/* Header */}
      <div className="mb-5">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
          <div className="flex items-center gap-2 md:gap-3">
            <div>
              <h1 className="text-[18px] md:text-[20px] font-bold text-foreground tracking-tight">Dashboard</h1>
            </div>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <div className="flex items-center gap-2 md:gap-3 px-2 md:px-3 py-2 rounded-2xl bg-accent/20 border border-border/40">
              <div className="flex items-center gap-1 md:gap-1.5">
                <Switch checked={autoRefresh} onCheckedChange={setAutoRefresh} className="scale-90" />
                <span className="text-[11px] md:text-[12px] text-muted-foreground hidden sm:inline">自动刷新</span>
                {autoRefresh && (
                  <Select value={refreshInterval.toString()} onValueChange={v => setRefreshInterval(parseInt(v))}>
                    <SelectTrigger className="h-6 w-14 md:w-16 text-[10px] md:text-[11px] px-1.5 md:px-2">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="10">10s</SelectItem>
                      <SelectItem value="30">30s</SelectItem>
                      <SelectItem value="60">1分钟</SelectItem>
                      <SelectItem value="120">2分钟</SelectItem>
                    </SelectContent>
                  </Select>
                )}
              </div>
              <div className="w-px h-4 bg-border hidden sm:block" />
              <div className="flex items-center gap-1 md:gap-1.5">
                <Switch checked={enableAIAnalysis} onCheckedChange={setEnableAIAnalysis} className="scale-90" />
                <span className="text-[11px] md:text-[12px] text-muted-foreground hidden sm:inline">AI 建议</span>
              </div>
              {lastRefreshTime && (
                <span className="text-[9px] md:text-[10px] text-muted-foreground/60 hidden md:inline font-mono">
                  {lastRefreshTime.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                </span>
              )}
            </div>

            <Button variant="secondary" size="sm" onClick={handleRefresh} disabled={quotesLoading || portfolioLoading} className="h-9 px-3">
              <RefreshCw className={`w-4 h-4 ${quotesLoading || portfolioLoading ? 'animate-spin' : ''}`} />
              <span className="hidden sm:inline">刷新</span>
            </Button>
          </div>
        </div>

        {/* Market status pills */}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {marketStatus.map(m => {
            const statusColors: Record<string, string> = {
              trading: 'bg-emerald-500',
              pre_market: 'bg-amber-500',
              break: 'bg-amber-500',
              after_hours: 'bg-slate-400',
              closed: 'bg-slate-400',
            }
            return (
              <div
                key={m.code}
                className="px-2.5 py-1 rounded-full bg-background/70 border border-border/50 text-[11px] text-muted-foreground flex items-center gap-1.5"
                title={`${m.sessions.join(', ')} (${m.local_time})`}
              >
                <span className={`w-1.5 h-1.5 rounded-full ${statusColors[m.status] || 'bg-slate-400'}`} />
                <span className="text-foreground/90">{m.name}</span>
                <span className={`${m.is_trading ? 'text-emerald-600' : 'text-muted-foreground/60'}`}>{m.status_text}</span>
              </div>
            )
          })}
          {lastRefreshTime ? (
            <div className="px-2.5 py-1 rounded-full bg-background/70 border border-border/50 text-[11px] text-muted-foreground">
              更新 <span className="font-mono text-foreground/90">{lastRefreshTime.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</span>
            </div>
          ) : null}
        </div>
      </div>

      {/* Portfolio Summary Cards */}
      {hasPortfolio && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-6">
          <div className="card p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <PiggyBank className="w-4 h-4" />
              <span className="text-[12px]">总资产</span>
            </div>
            <div className="text-[20px] font-bold text-foreground font-mono">
              {formatMoney(portfolio!.total.total_assets)}
            </div>
          </div>

          <div className="card p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              {portfolio!.total.total_pnl >= 0 ? (
                <ArrowUpRight className="w-4 h-4 text-rose-500" />
              ) : (
                <ArrowDownRight className="w-4 h-4 text-emerald-500" />
              )}
              <span className="text-[12px]">总盈亏</span>
            </div>
            <div className={`text-[20px] font-bold font-mono ${portfolio!.total.total_pnl >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
              {portfolio!.total.total_pnl >= 0 ? '+' : ''}{formatMoney(portfolio!.total.total_pnl)}
              <span className="text-[13px] ml-1.5">
                ({portfolio!.total.total_pnl_pct >= 0 ? '+' : ''}{portfolio!.total.total_pnl_pct.toFixed(2)}%)
              </span>
            </div>
          </div>

          <div className="card p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <TrendingUp className="w-4 h-4" />
              <span className="text-[12px]">持仓市值</span>
            </div>
            <div className="text-[20px] font-bold text-foreground font-mono">
              {formatMoney(portfolio!.total.total_market_value)}
            </div>
          </div>

          <div className="card p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Wallet className="w-4 h-4" />
              <span className="text-[12px]">可用资金</span>
            </div>
            <div className="text-[20px] font-bold text-foreground font-mono">
              {formatMoney(portfolio!.total.available_funds)}
            </div>
          </div>

          <div className="card p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              {(portfolioDayPnl?.day_pnl ?? 0) >= 0 ? (
                <ArrowUpRight className="w-4 h-4 text-rose-500" />
              ) : (
                <ArrowDownRight className="w-4 h-4 text-emerald-500" />
              )}
              <span className="text-[12px]">今日盈亏</span>
            </div>
            <div className={`text-[20px] font-bold font-mono ${(portfolioDayPnl?.day_pnl ?? 0) >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
              {(portfolioDayPnl?.day_pnl ?? 0) >= 0 ? '+' : ''}{formatMoney(portfolioDayPnl?.day_pnl ?? 0)}
              <span className="text-[13px] ml-1.5">({(portfolioDayPnl?.day_pnl_pct ?? 0) >= 0 ? '+' : ''}{(portfolioDayPnl?.day_pnl_pct ?? 0).toFixed(2)}%)</span>
            </div>
            {!portfolioDayPnl?.has_data && (
              <div className="mt-1 text-[11px] text-muted-foreground">等待行情数据</div>
            )}
          </div>

          <button
            type="button"
            className="card p-4 text-left hover:bg-accent/10 transition-colors"
            onClick={() => {
              if (dayMovers.worst) openStockInsight(dayMovers.worst.symbol, dayMovers.worst.market, dayMovers.worst.name, true)
            }}
          >
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Activity className="w-4 h-4" />
              <span className="text-[12px]">今日最大拖累</span>
            </div>
            {dayMovers.worst ? (
              <>
                <div className="text-[14px] font-medium text-foreground truncate">{dayMovers.worst.name}</div>
                <div className={`mt-1 text-[16px] font-bold font-mono ${dayMovers.worst.day_pnl >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                  {dayMovers.worst.day_pnl >= 0 ? '+' : ''}{formatMoney(dayMovers.worst.day_pnl)}
                  <span className="text-[12px] ml-1.5">({dayMovers.worst.day_pct >= 0 ? '+' : ''}{dayMovers.worst.day_pct.toFixed(2)}%)</span>
                </div>
                <div className="mt-1 text-[11px] text-muted-foreground">点击查看详情</div>
              </>
            ) : (
              <div className="text-[12px] text-muted-foreground">等待行情数据</div>
            )}
          </button>
        </div>
      )}

      {/* Market Indices */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-[15px] font-semibold text-foreground flex items-center gap-2">
            <BarChart3 className="w-4 h-4 text-primary" />
            大盘指数
          </h2>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          {indicesLoading ? (
            Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="card p-3 animate-pulse">
                <div className="h-4 bg-accent/50 rounded w-16 mb-2" />
                <div className="h-6 bg-accent/50 rounded w-20 mb-1" />
                <div className="h-3 bg-accent/30 rounded w-12" />
              </div>
            ))
          ) : (
            indices.map(idx => {
              const isUp = idx.change_pct !== null && idx.change_pct > 0
              const isDown = idx.change_pct !== null && idx.change_pct < 0
              const changeColor = isUp ? 'text-rose-500' : isDown ? 'text-emerald-500' : 'text-muted-foreground'
              const bgColor = isUp ? 'bg-rose-500/5' : isDown ? 'bg-emerald-500/5' : 'bg-accent/30'

              return (
                <div key={idx.symbol} className={`card p-3 ${bgColor} border-0`}>
                  <div className="flex items-center gap-1.5 mb-1">
                    <span className={`text-[9px] px-1 py-0.5 rounded ${marketBadge(idx.market).style}`}>
                      {marketBadge(idx.market).label}
                    </span>
                    <span className="text-[12px] text-muted-foreground">{idx.name}</span>
                  </div>
                  <div className={`text-[18px] font-bold font-mono ${changeColor}`}>
                    {formatIndexPrice(idx.current_price)}
                  </div>
                  <div className={`text-[12px] font-mono ${changeColor}`}>
                    {idx.change_pct !== null ? (
                      <>
                        {isUp ? '+' : ''}{idx.change_pct.toFixed(2)}%
                        <span className="ml-1.5 opacity-60">
                          {isUp ? '+' : ''}{idx.change_amount?.toFixed(2)}
                        </span>
                      </>
                    ) : (
                      '--'
                    )}
                  </div>
                </div>
              )
            })
          )}
        </div>
      </div>

      {/* Discover */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-[15px] font-semibold text-foreground flex items-center gap-2">
            <Layers className="w-4 h-4 text-primary" />
            机会发现
          </h2>
          <div className="flex items-center gap-2">
            <Select value={discoverMarket} onValueChange={(v) => setDiscoverMarket(v as any)}>
              <SelectTrigger className="h-7 w-[90px] text-[12px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="CN">A股</SelectItem>
                <SelectItem value="HK">港股</SelectItem>
                <SelectItem value="US">美股</SelectItem>
              </SelectContent>
            </Select>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => loadDiscovery()}
              disabled={discoverLoading}
              className="h-7 text-[12px]"
              title="刷新"
            >
              {discoverLoading ? (
                <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
              ) : (
                <RefreshCw className="w-3.5 h-3.5" />
              )}
            </Button>
          </div>
        </div>

        <div className="card p-4">
          <div className="flex items-center gap-1.5 mb-3">
            <button
              onClick={() => { setDiscoverTab('boards'); loadDiscovery('boards') }}
              className={`text-[11px] px-2.5 py-1 rounded transition-colors ${discoverTab === 'boards' ? 'bg-primary text-primary-foreground' : 'bg-accent/50 text-muted-foreground hover:bg-accent'}`}
            >
              热门板块
            </button>
            <button
              onClick={() => { setDiscoverTab('stocks'); loadDiscovery('stocks') }}
              className={`text-[11px] px-2.5 py-1 rounded transition-colors ${discoverTab === 'stocks' ? 'bg-primary text-primary-foreground' : 'bg-accent/50 text-muted-foreground hover:bg-accent'}`}
            >
              热门股票
            </button>

            <div className="ml-auto flex items-center gap-2">
              {discoverTab === 'boards' ? (
                <Select value={boardsMode} onValueChange={(v) => { setBoardsMode(v as any); setTimeout(() => loadDiscovery('boards'), 0) }}>
                  <SelectTrigger className="h-7 w-[110px] text-[12px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="gainers">涨幅榜</SelectItem>
                    <SelectItem value="turnover">成交额榜</SelectItem>
                  </SelectContent>
                </Select>
              ) : (
                <Select value={stocksMode} onValueChange={(v) => { setStocksMode(v as any); setTimeout(() => loadDiscovery('stocks'), 0) }}>
                  <SelectTrigger className="h-7 w-[110px] text-[12px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="for_you">For You</SelectItem>
                    <SelectItem value="turnover">成交额榜</SelectItem>
                    <SelectItem value="gainers">涨幅榜</SelectItem>
                  </SelectContent>
                </Select>
              )}
            </div>
          </div>

          {discoverMarket !== 'CN' ? (
            <div className="text-[12px] text-muted-foreground py-6 text-center">暂只支持 A股（CN）</div>
          ) : discoverLoading ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={`discover-skeleton-${i}`} className="p-3 rounded-xl bg-accent/20 animate-pulse">
                  <div className="h-3 w-24 rounded bg-accent/60 mb-2" />
                  <div className="h-3 w-16 rounded bg-accent/50" />
                </div>
              ))}
            </div>
          ) : discoverTab === 'boards' ? (
            hotBoards.length === 0 ? (
              <div className="text-[12px] text-muted-foreground py-6 text-center">{discoverError || '暂无数据'}</div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {hotBoards.slice(0, 8).map(b => {
                  const pct = b.change_pct ?? 0
                  const color = pct > 0 ? 'text-rose-500' : pct < 0 ? 'text-emerald-500' : 'text-muted-foreground'
                  return (
                    <button
                      key={b.code}
                      onClick={() => openBoard(b)}
                      className="flex items-center justify-between gap-3 p-3 rounded-xl bg-accent/20 hover:bg-accent/35 transition-colors text-left"
                      title="查看板块成分股"
                    >
                      <div className="min-w-0">
                        <div className="text-[13px] font-medium text-foreground truncate">{b.name}</div>
                        <div className="text-[11px] text-muted-foreground font-mono truncate">{b.code}</div>
                      </div>
                      <div className={`text-[12px] font-mono font-semibold ${color}`}>{pct >= 0 ? '+' : ''}{pct.toFixed(2)}%</div>
                    </button>
                  )
                })}
              </div>
            )
          ) : (
            hotStocks.length === 0 ? (
              <div className="text-[12px] text-muted-foreground py-6 text-center">{discoverError || '暂无数据'}</div>
            ) : (
              <div className="space-y-2">
                {stocksMode === 'for_you' && (
                  <div className="text-[11px] text-muted-foreground px-1">
                    根据持仓/自选/监控信号/风格偏好排序
                  </div>
                )}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {visibleHotStocks.map(s => {
                  const pct = s.change_pct ?? 0
                  const color = pct > 0 ? 'text-rose-500' : pct < 0 ? 'text-emerald-500' : 'text-muted-foreground'
                  return (
                    <div
                      key={s.symbol}
                      onClick={() => openStockInsight(s.symbol, 'CN', s.name, false)}
                      className="flex items-center justify-between gap-3 p-3 rounded-xl bg-accent/20 hover:bg-accent/35 transition-colors text-left cursor-pointer"
                      title="打开股票详情弹窗"
                    >
                      <div className="min-w-0">
                        <div className="text-[13px] font-medium text-foreground truncate">{s.name}</div>
                        <div className="text-[11px] text-muted-foreground font-mono">{s.symbol}</div>
                        {(s as any)._reasons?.length > 0 && (
                          <div className="text-[10px] text-muted-foreground truncate mt-0.5">
                            {(s as any)._reasons.join(' · ')}
                          </div>
                        )}
                      </div>
                      <div className="flex items-center gap-3">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 px-2 text-[11px]"
                          onClick={(e) => {
                            e.stopPropagation()
                            openStockInsight(s.symbol, 'CN', s.name, false)
                          }}
                        >
                          详情
                        </Button>
                        <div className="text-right">
                        <div className="text-[12px] font-mono text-foreground">{s.price != null ? s.price.toFixed(2) : '--'}</div>
                        <div className={`text-[11px] font-mono ${color}`}>{pct >= 0 ? '+' : ''}{pct.toFixed(2)}%</div>
                        </div>
                      </div>
                    </div>
                  )
                })}
                </div>
              </div>
            )
          )}
        </div>
      </div>

      <Dialog open={boardDialogOpen} onOpenChange={setBoardDialogOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{activeBoard ? `板块：${activeBoard.name}` : '板块成分股'}</DialogTitle>
            <DialogDescription>点击个股打开统一详情弹窗（含概览、K线、建议、新闻、历史）</DialogDescription>
          </DialogHeader>
          {boardStocks.length === 0 ? (
            <div className="text-[12px] text-muted-foreground py-6 text-center">暂无数据</div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2 max-h-[60vh] overflow-y-auto scrollbar">
              {boardStocks.map(s => {
                const pct = s.change_pct ?? 0
                const color = pct > 0 ? 'text-rose-500' : pct < 0 ? 'text-emerald-500' : 'text-muted-foreground'
                return (
                  <div
                    key={s.symbol}
                    onClick={() => {
                      setBoardDialogOpen(false)
                      openStockInsight(s.symbol, 'CN', s.name, false)
                    }}
                    className="flex items-center justify-between gap-3 p-3 rounded-xl bg-accent/20 hover:bg-accent/35 transition-colors text-left cursor-pointer"
                  >
                    <div className="min-w-0">
                      <div className="text-[13px] font-medium text-foreground truncate">{s.name}</div>
                      <div className="text-[11px] text-muted-foreground font-mono">{s.symbol}</div>
                    </div>
                    <div className="flex items-center gap-3">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-[11px]"
                        onClick={(e) => {
                          e.stopPropagation()
                          setBoardDialogOpen(false)
                          openStockInsight(s.symbol, 'CN', s.name, false)
                        }}
                      >
                        详情
                      </Button>
                      <div className="text-right">
                      <div className="text-[12px] font-mono text-foreground">{s.price != null ? s.price.toFixed(2) : '--'}</div>
                      <div className={`text-[11px] font-mono ${color}`}>{pct >= 0 ? '+' : ''}{pct.toFixed(2)}%</div>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Action Center */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-[15px] font-semibold text-foreground flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-primary" />
            行动中心
          </h2>
          <div className="flex items-center gap-2">
            {hasWatchlist && (
              <Button variant="ghost" size="sm" onClick={scanAlerts} disabled={scanning || aiScanRunning} className="h-7 text-[12px]">
                {scanning || aiScanRunning ? (
                  <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                ) : (
                  <RefreshCw className="w-3.5 h-3.5" />
                )}
                {scanning ? '扫描中' : aiScanRunning ? 'AI分析中' : '扫描'}
              </Button>
            )}
            <button
              onClick={() => navigate('/portfolio')}
              className="flex items-center gap-1 text-[12px] text-muted-foreground hover:text-primary transition-colors"
            >
              去持仓页执行 <ChevronRight className="w-4 h-4" />
            </button>
            {(dailyReport || premarketOutlook || newsDigest) && (
              <button
                onClick={() => navigate('/history')}
                className="flex items-center gap-1 text-[12px] text-muted-foreground hover:text-primary transition-colors"
              >
                AI历史 <ChevronRight className="w-4 h-4" />
              </button>
            )}
            {lastScanTime && (
              <span className="text-[10px] text-muted-foreground/70 font-mono hidden md:inline">
                监控 {lastScanTime.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
              </span>
            )}
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-5 gap-4 items-stretch">
          <div className="xl:col-span-3">
            <div className="text-[12px] text-muted-foreground mb-2">待处理信号</div>
            {aiScanRunning && !scanning && (
              <div className="mb-2 text-[11px] text-primary">基础结果已返回，AI 建议补充中...</div>
            )}
            {scanning ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {Array.from({ length: 4 }).map((_, i) => (
                  <div key={`action-skeleton-${i}`} className="card h-[126px] p-4 animate-pulse">
                    <div className="h-3 w-24 rounded bg-accent/60 mb-2" />
                    <div className="h-3 w-16 rounded bg-accent/50 mb-3" />
                    <div className="h-3 w-full rounded bg-accent/40 mb-2" />
                    <div className="h-3 w-2/3 rounded bg-accent/40" />
                  </div>
                ))}
              </div>
            ) : actionableSignals.length === 0 ? (
              <div className="card h-[126px] p-6 text-center">
                <p className="text-[13px] text-muted-foreground">当前没有待处理信号</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {actionableSignals.map(s => (
                  <button
                    key={`${s.market}:${s.symbol}`}
                    onClick={() => openStockInsight(s.symbol, s.market, s.name, s.has_position)}
                    className="card h-[126px] p-4 text-left hover:bg-accent/20 transition-colors overflow-hidden"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div>
                        <div className="text-[13px] font-semibold text-foreground">{s.name}</div>
                        <div className="text-[11px] text-muted-foreground font-mono">{s.market}:{s.symbol}</div>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/50 text-muted-foreground">{(s as any)._source || '盘中监控'}</span>
                        {s.alert_type && (
                          <span className={`text-[10px] px-1.5 py-0.5 rounded ${s.alert_type === '急涨' ? 'bg-rose-500/10 text-rose-500' : 'bg-emerald-500/10 text-emerald-500'}`}>
                            {s.alert_type}
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="mt-2 flex items-center justify-between text-[12px]">
                      <span className="font-mono text-foreground">{s.current_price?.toFixed(2) || '--'}</span>
                      <span className={`font-mono ${s.change_pct > 0 ? 'text-rose-500' : s.change_pct < 0 ? 'text-emerald-500' : 'text-muted-foreground'}`}>
                        {s.change_pct >= 0 ? '+' : ''}{(s.change_pct || 0).toFixed(2)}%
                      </span>
                    </div>
                    {s.suggestion && (
                      <div className="mt-2 text-[11px] text-muted-foreground line-clamp-1">
                        {s.suggestion.action_label} · {s.suggestion.signal || s.suggestion.reason || '有新的建议'}
                      </div>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="xl:col-span-2">
            <div className="text-[12px] text-muted-foreground mb-2">AI宏观摘要</div>
            {insightsLoading ? (
              <div className="card h-[126px] p-4 animate-pulse">
                <div className="h-4 bg-accent/50 rounded w-24 mb-3" />
                <div className="h-3 bg-accent/30 rounded w-full mb-2" />
                <div className="h-3 bg-accent/30 rounded w-2/3" />
              </div>
            ) : insightCards.length === 0 ? (
              <div className="card h-[126px] p-5 text-center">
                <p className="text-[13px] text-muted-foreground mb-3">暂无 AI 摘要</p>
                <Button variant="secondary" size="sm" onClick={() => navigate('/agents')}>
                  配置 Agent
                </Button>
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-3">
                {insightCards.map(card => {
                  const Icon = card.icon
                  return (
                    <button
                      key={card.key}
                      className="card w-full h-[126px] p-4 text-left hover:bg-accent/20 transition-colors overflow-hidden"
                      onClick={() => setPreviewInsight(card.record || null)}
                    >
                      <div className="flex items-center gap-2">
                        <div className={`w-7 h-7 rounded-lg flex items-center justify-center ${card.style}`}>
                          <Icon className="w-3.5 h-3.5" />
                        </div>
                        <div className="min-w-0">
                          <div className="text-[12px] font-medium text-foreground">{card.title}</div>
                          <div className="text-[10px] text-muted-foreground">{card.record?.analysis_date || '--'}</div>
                        </div>
                      </div>
                      <div className="mt-2 text-[11px] text-foreground/85 line-clamp-2">{card.preview || card.record?.title || '暂无摘要'}</div>
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      </div>

      <Dialog open={!!previewInsight} onOpenChange={(open) => !open && setPreviewInsight(null)}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{previewInsight?.title || 'AI 摘要预览'}</DialogTitle>
            <DialogDescription>
              {previewInsight ? `${previewInsight.analysis_date} · ${previewInsight.agent_name}` : ''}
            </DialogDescription>
          </DialogHeader>
          {previewInsight && (
            <div className="prose prose-sm dark:prose-invert max-w-none max-h-[60vh] overflow-y-auto">
              <ReactMarkdown>{previewInsight.content}</ReactMarkdown>
            </div>
          )}
          <div className="flex justify-end">
            <Button variant="outline" size="sm" onClick={() => navigate('/history')}>
              查看完整历史
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Empty Portfolio Hint */}
      {!hasPortfolio && hasWatchlist && (
        <div className="card p-6 text-center border-dashed">
          <div className="w-12 h-12 rounded-2xl bg-blue-500/10 flex items-center justify-center mx-auto mb-3">
            <Wallet className="w-5 h-5 text-blue-500" />
          </div>
          <p className="text-[14px] font-medium text-foreground mb-1">添加持仓查看盈亏</p>
          <p className="text-[12px] text-muted-foreground mb-4">记录你的持仓成本，系统会自动计算盈亏情况</p>
          <Button variant="secondary" size="sm" onClick={() => navigate('/portfolio')}>
            管理持仓
          </Button>
        </div>
      )}
    </div>
  )
}
