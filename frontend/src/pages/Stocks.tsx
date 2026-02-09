import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, Trash2, Pencil, Search, X, TrendingUp, Bot, Play, RefreshCw, Wallet, PiggyBank, ArrowUpRight, ArrowDownRight, Building2, ChevronDown, ChevronRight, Cpu, Bell, Clock, Newspaper, ExternalLink, BarChart3 } from 'lucide-react'
import { fetchAPI, useLocalStorage, type AIService, type NotifyChannel } from '@/lib/utils'
import { SuggestionBadge, type SuggestionInfo, type KlineSummary } from '@/components/suggestion-badge'
import { buildKlineSuggestion } from '@/lib/kline-scorer'
import { KlineSummaryDialog } from '@/components/kline-summary-dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectGroup, SelectLabel, SelectItem } from '@/components/ui/select'
import { useToast } from '@/components/ui/toast'
import StockInsightModal from '@/components/stock-insight-modal'

interface AgentResult {
  title: string
  content: string
  should_alert: boolean
  notified: boolean
}

interface StockAgentInfo {
  agent_name: string
  schedule: string
  ai_model_id: number | null
  notify_channel_ids: number[]
}

interface Stock {
  id: number
  symbol: string
  name: string
  market: string
  enabled: boolean
  agents: StockAgentInfo[]
}

interface Account {
  id: number
  name: string
  available_funds: number
  enabled: boolean
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
  trading_style: string  // short: 短线, swing: 波段, long: 长线
  current_price: number | null
  current_price_cny: number | null  // 人民币价格（港股换算后）
  change_pct: number | null
  market_value: number | null
  market_value_cny: number | null  // 人民币市值
  pnl: number | null
  pnl_pct: number | null
  exchange_rate: number | null  // 汇率（仅港股）
}

interface AccountSummary {
  id: number
  name: string
  available_funds: number
  total_market_value: number
  total_cost: number
  total_pnl: number
  total_pnl_pct: number
  total_assets: number
  positions: Position[]
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
  quotes?: Record<string, { current_price: number | null; change_pct: number | null }>
}

interface AgentConfig {
  name: string
  display_name: string
  description: string
  enabled: boolean
  schedule: string
  execution_mode: string  // batch: 批量分析, single: 逐只分析
}

interface SchedulePreview {
  schedule: string
  timezone: string
  next_runs: string[]
}

interface SearchResult {
  symbol: string
  name: string
  market: string
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

interface StockForm {
  symbol: string
  name: string
  market: string
}

interface AccountForm {
  name: string
  available_funds: string
}

interface PositionForm {
  account_id: number
  stock_id: number
  cost_price: string
  quantity: string
  invested_amount: string
  trading_style: string
  // 搜索选中的股票信息（新增持仓时用）
  stock_symbol: string
  stock_name: string
  stock_market: string
}

// 股票建议信息（来自盘中监控 API）
interface StockSuggestionData {
  symbol: string
  suggestion: SuggestionInfo | null
  kline: KlineSummary | null
}

// 建议池中的建议（包含来源和时间信息）
interface PoolSuggestion {
  id: number
  stock_symbol: string
  stock_name: string
  action: string
  action_label: string
  signal: string
  reason: string
  agent_name: string
  agent_label: string
  created_at: string
  expires_at: string | null
  is_expired: boolean
  prompt_context: string
  ai_response: string
  meta?: Record<string, any>
  should_alert?: boolean
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

const emptyStockForm: StockForm = { symbol: '', name: '', market: 'CN' }
const emptyAccountForm: AccountForm = { name: '', available_funds: '0' }

const round2 = (value: number) => Math.round(value * 100) / 100

const mergePortfolioQuotes = (
  portfolio: PortfolioSummary | null,
  quotes: Record<string, { current_price: number | null; change_pct: number | null }>
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

    const positions = account.positions.map(pos => {
      const quote = quotes[pos.symbol]
      const current_price = quote?.current_price ?? pos.current_price ?? null
      const change_pct = quote?.change_pct ?? pos.change_pct ?? null
      const rate = pos.market === 'HK' ? hkdRate : pos.market === 'US' ? usdRate : 1

      const cost = pos.cost_price * pos.quantity * rate
      accCost += cost

      let market_value: number | null = null
      let market_value_cny: number | null = null
      let pnl: number | null = null
      let pnl_pct: number | null = null

      if (current_price != null) {
        market_value = current_price * pos.quantity
        market_value_cny = market_value * rate
        accMarketValue += market_value_cny
        pnl = market_value_cny - cost
        pnl_pct = cost > 0 ? (pnl / cost * 100) : 0
      }

      return {
        ...pos,
        current_price,
        current_price_cny: current_price != null ? current_price * rate : null,
        change_pct,
        market_value,
        market_value_cny,
        pnl,
        pnl_pct,
        exchange_rate: pos.market === 'HK' || pos.market === 'US' ? rate : null,
      }
    })

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
      positions,
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

export default function StocksPage() {
  const [stocks, setStocks] = useState<Stock[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const [agents, setAgents] = useState<AgentConfig[]>([])
  const [services, setServices] = useState<AIService[]>([])
  const [channels, setChannels] = useState<NotifyChannel[]>([])
  const [loading, setLoading] = useState(true)

  // Portfolio
  const [portfolio, setPortfolio] = useState<PortfolioSummary | null>(null)
  const [portfolioRaw, setPortfolioRaw] = useState<PortfolioSummary | null>(null)
  const [portfolioLoading, setPortfolioLoading] = useState(false)
  const [expandedAccounts, setExpandedAccounts] = useState<Set<number>>(new Set())

  // Quotes for all stocks (used in stock list)
  const [quotes, setQuotes] = useState<Record<string, { current_price: number | null; change_pct: number | null }>>({})
  const [quotesLoading, setQuotesLoading] = useState(false)
  // Keyed by `${market}:${symbol}` to avoid cross-market symbol collisions
  const [klineSummaries, setKlineSummaries] = useState<Record<string, KlineSummary>>({})

  // Auto-refresh (持久化到 localStorage)
  const [autoRefresh, setAutoRefresh] = useLocalStorage('panwatch_stocks_autoRefresh', false)
  const [refreshInterval, setRefreshInterval] = useLocalStorage('panwatch_stocks_refreshInterval', 30)
  const [lastRefreshTime, setLastRefreshTime] = useState<Date | null>(null)
  const refreshTimerRef = useRef<ReturnType<typeof setInterval>>()

  // Alerts / Scanning
  const [scanning, setScanning] = useState(false)
  const [enableAIAnalysis, setEnableAIAnalysis] = useState(true)  // 是否启用 AI 分析建议

  type ViewTab = 'positions' | 'watchlist'
  const [viewTab, setViewTab] = useLocalStorage<ViewTab>('panwatch_stocks_viewTab', 'positions')

  // 股票 AI 建议（来自盘中监控 API）
  const [suggestions] = useState<Record<string, StockSuggestionData>>({})
  const [suggestionsLoading] = useState(false)

  // 建议池建议（来自 /suggestions API）
  const [poolSuggestions, setPoolSuggestions] = useState<Record<string, PoolSuggestion>>({})
  const [poolSuggestionsLoading, setPoolSuggestionsLoading] = useState(false)

  // News Dialog
  const [newsDialogOpen, setNewsDialogOpen] = useState(false)
  const [newsDialogSymbol, setNewsDialogSymbol] = useState<string>('')  // 空=全部, 否则=指定股票
  const [news, setNews] = useState<NewsItem[]>([])
  const [newsLoading, setNewsLoading] = useState(false)

  // Kline Dialog
  const [klineDialogOpen, setKlineDialogOpen] = useState(false)
  const [klineDialogSymbol, setKlineDialogSymbol] = useState('')
  const [klineDialogMarket, setKlineDialogMarket] = useState('CN')
  const [klineDialogName, setKlineDialogName] = useState<string | undefined>(undefined)
  const [klineDialogHasPosition, setKlineDialogHasPosition] = useState<boolean>(false)
  const [klineDialogInitialSummary, setKlineDialogInitialSummary] = useState<KlineSummary | null>(null)
  const [insightOpen, setInsightOpen] = useState(false)
  const [insightSymbol, setInsightSymbol] = useState('')
  const [insightMarket, setInsightMarket] = useState('CN')
  const [insightName, setInsightName] = useState<string | undefined>(undefined)
  const [insightHasPosition, setInsightHasPosition] = useState(false)

  // Market status
  const [marketStatus, setMarketStatus] = useState<MarketStatus[]>([])
  // Guard to prevent overlapping K线刷新任务导致实际并发超限
  const klineRefreshInFlight = useRef<Promise<void> | null>(null)

  // Stock form
  const [showStockForm, setShowStockForm] = useState(false)
  const [stockForm, setStockForm] = useState<StockForm>(emptyStockForm)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchMarket, setSearchMarket] = useState('')  // 搜索市场筛选
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [showDropdown, setShowDropdown] = useState(false)
  const [searching, setSearching] = useState(false)
  const [refreshingStockList, setRefreshingStockList] = useState(false)

  // Account form
  const [accountDialogOpen, setAccountDialogOpen] = useState(false)
  const [accountForm, setAccountForm] = useState<AccountForm>(emptyAccountForm)
  const [editAccountId, setEditAccountId] = useState<number | null>(null)

  // Position form
  const [positionDialogOpen, setPositionDialogOpen] = useState(false)
  const [positionForm, setPositionForm] = useState<PositionForm>({ account_id: 0, stock_id: 0, cost_price: '', quantity: '', invested_amount: '', trading_style: '', stock_symbol: '', stock_name: '', stock_market: 'CN' })
  const [editPositionId, setEditPositionId] = useState<number | null>(null)
  const [positionDialogAccountId, setPositionDialogAccountId] = useState<number | null>(null)
  const [positionSearchQuery, setPositionSearchQuery] = useState('')
  const [positionSearchMarket, setPositionSearchMarket] = useState('')  // 搜索市场筛选
  const [positionSearchResults, setPositionSearchResults] = useState<SearchResult[]>([])
  const [positionSearching, setPositionSearching] = useState(false)
  const [showPositionDropdown, setShowPositionDropdown] = useState(false)
  const positionSearchTimer = useRef<ReturnType<typeof setTimeout>>()
  const positionDropdownRef = useRef<HTMLDivElement>(null)

  // Agent dialog
  const [agentDialogStock, setAgentDialogStock] = useState<Stock | null>(null)
  const [triggeringAgent, setTriggeringAgent] = useState<string | null>(null)
  const [schedulePreviewCache, setSchedulePreviewCache] = useState<Record<string, SchedulePreview | { error: string }>>({})
  const [schedulePreviewLoading, setSchedulePreviewLoading] = useState<Record<string, boolean>>({})
  // 运行中的单只股票 Agent（按股票标记具体 Agent 名称）
  const [runningAgents, setRunningAgents] = useState<Record<number, string | null>>({})
  const [agentResultDialog, setAgentResultDialog] = useState<{ title: string; content: string; should_alert: boolean; notified: boolean } | null>(null)

  // Stock list filter
  const [stockListFilter, setStockListFilter] = useState('')  // '' = 全部, 'CN' = A股, 'HK' = 港股, 'US' = 美股
  const [watchlistOnlyAlerts, setWatchlistOnlyAlerts] = useLocalStorage<boolean>('panwatch_watchlist_only_alerts', false)

  // Remove watchlist modal
  const [removeWatchStock, setRemoveWatchStock] = useState<Stock | null>(null)
  const [removingWatchStock, setRemovingWatchStock] = useState(false)

  const { toast } = useToast()
  const navigate = useNavigate()

  const isSuppressCardClick = () => {
    try {
      const until = (window as any).__panwatch_suppress_card_click_until
      return typeof until === 'number' && Date.now() < until
    } catch {
      return false
    }
  }
  const searchTimer = useRef<ReturnType<typeof setTimeout>>()
  const dropdownRef = useRef<HTMLDivElement>(null)

  // 非核心数据后台加载（不阻塞 UI）
  const loadConfigAsync = async () => {
    try {
      const [agentData, servicesData, channelsData] = await Promise.all([
        fetchAPI<AgentConfig[]>('/agents'),
        fetchAPI<AIService[]>('/providers/services'),
        fetchAPI<NotifyChannel[]>('/channels'),
      ])
      setAgents(agentData)
      setServices(servicesData)
      setChannels(channelsData)
    } catch (e) {
      console.warn('加载配置数据失败:', e)
    }
  }

  const load = async () => {
    try {
      // 核心数据（立即需要）
      const [stockData, accountData] = await Promise.all([
        fetchAPI<Stock[]>('/stocks'),
        fetchAPI<Account[]>('/accounts'),
      ])
      setStocks(stockData)
      setAccounts(accountData)
      // 默认展开所有账户
      setExpandedAccounts(new Set(accountData.map((a: Account) => a.id)))
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)  // 提前解除阻塞
    }

    // 非核心数据（后台加载，不阻塞 UI）
    loadConfigAsync()

    // 市场状态（非核心，失败不影响页面）
    try {
      const marketStatusData = await fetchAPI<MarketStatus[]>('/stocks/markets/status')
      setMarketStatus(marketStatusData)
    } catch (e) {
      console.warn('获取市场状态失败:', e)
    }
  }

  const loadPortfolio = async () => {
    setPortfolioLoading(true)
    try {
      // 核心数据：仅本地账户/持仓
      const portfolioData = await fetchAPI<PortfolioSummary>('/portfolio/summary?include_quotes=false')
      setPortfolioRaw(portfolioData)
      setPortfolio(mergePortfolioQuotes(portfolioData, quotes))

      // 市场状态（非核心，失败不影响页面）
      try {
        const marketStatusData = await fetchAPI<MarketStatus[]>('/stocks/markets/status')
        setMarketStatus(marketStatusData)
      } catch (e) {
        console.warn('获取市场状态失败:', e)
      }
    } catch (e) {
      console.error(e)
    } finally {
      setPortfolioLoading(false)
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
      const map: Record<string, { current_price: number | null; change_pct: number | null }> = {}
      for (const item of data) {
        map[item.symbol] = {
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

  useEffect(() => {
    if (!portfolioRaw) return
    setPortfolio(mergePortfolioQuotes(portfolioRaw, quotes))
  }, [portfolioRaw, quotes])

  useEffect(() => {
    if (stocks.length === 0 && (!portfolioRaw || portfolioRaw.accounts.length === 0)) return
    refreshQuotes()
    // 刷新 K 线摘要（用于常驻评分徽章）
    ;(async () => {
      try { await refreshKlines() } catch {}
    })()
  }, [stocks, portfolioRaw, refreshQuotes])

  // 刷新 K 线摘要（并发受限的单个请求，避免批量接口慢）；并防止重入
  const refreshKlines = useCallback(async () => {
    if (klineRefreshInFlight.current) return klineRefreshInFlight.current
    const run = (async () => {
      const items = buildQuoteItems()
      if (items.length === 0) return
      const limit = 5
      const map: Record<string, KlineSummary> = {}
      let idx = 0
      const worker = async () => {
        while (idx < items.length) {
          const i = idx++
          const it = items[i]
          try {
            const res = await fetchAPI<{ symbol: string; market: string; summary: KlineSummary }>(`/klines/${encodeURIComponent(it.symbol)}/summary?market=${encodeURIComponent(it.market)}`)
            if (res && (res as any).summary) {
              map[`${it.market}:${it.symbol}`] = (res as any).summary as KlineSummary
            }
          } catch {
            // ignore single failure
          }
        }
      }
      await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => worker()))
      setKlineSummaries(map)
    })()
    klineRefreshInFlight.current = run
    try { await run } finally { klineRefreshInFlight.current = null }
  }, [buildQuoteItems])

  // 从建议池加载建议（包含历史建议和多来源建议）
  const loadPoolSuggestions = useCallback(async () => {
    setPoolSuggestionsLoading(true)
    try {
      const data = await fetchAPI<Record<string, PoolSuggestion>>('/suggestions')
      setPoolSuggestions(data)
    } catch (e) {
      console.warn('加载建议池失败:', e)
    } finally {
      setPoolSuggestionsLoading(false)
    }
  }, [])

  // Load news for specific stock or all watchlist
  const loadNews = useCallback(async (stockName?: string) => {
    setNewsLoading(true)
    try {
      const params = new URLSearchParams({ hours: '168', limit: '50' })  // 7天
      if (stockName) {
        // 直接传递股票名称，比代码更稳定
        params.set('names', stockName)
      }
      const newsData = await fetchAPI<NewsItem[]>(`/news?${params}`)
      setNews(newsData)
    } catch (e) {
      console.error('加载新闻失败:', e)
    } finally {
      setNewsLoading(false)
    }
  }, [])

  const openKlineDialog = useCallback((symbol: string, market: string, name?: string, hasPosition?: boolean) => {
    setKlineDialogSymbol(symbol)
    setKlineDialogMarket(market || 'CN')
    setKlineDialogName(name)
    setKlineDialogHasPosition(!!hasPosition)
    const m = market || 'CN'
    setKlineDialogInitialSummary(klineSummaries[`${m}:${symbol}`] || null)
    setKlineDialogOpen(true)
  }, [klineSummaries])

  // Open news dialog - pass stock name for more stable search
  const openNewsDialog = useCallback((stockName?: string) => {
    setNewsDialogSymbol(stockName || '')  // 存储名称用于 UI 显示
    setNewsDialogOpen(true)
    loadNews(stockName)
  }, [loadNews])

  const openStockDetail = useCallback((stockSymbol: string, stockMarket: string, stockName?: string, hasPosition?: boolean) => {
    setInsightSymbol(stockSymbol)
    setInsightMarket(stockMarket || 'CN')
    setInsightName(stockName)
    setInsightHasPosition(!!hasPosition)
    setInsightOpen(true)
  }, [])

  const formatPreviewTime = (iso: string, tz?: string): string => {
    try {
      const d = new Date(iso)
      if (isNaN(d.getTime())) return iso
      return d.toLocaleString('zh-CN', {
        timeZone: tz || undefined,
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      })
    } catch {
      return iso
    }
  }

  const effectiveSchedule = (agent: AgentConfig, stockAgent?: StockAgentInfo | null): string => {
    const local = (stockAgent?.schedule || '').trim()
    if (local) return local
    return (agent.schedule || '').trim()
  }

  // Refresh quotes only (decoupled from portfolio and scans)
  const handleRefresh = useCallback(async () => {
    await Promise.all([
      refreshQuotes(),
      loadPoolSuggestions(),
      refreshKlines(),
    ])
  }, [refreshQuotes, loadPoolSuggestions, refreshKlines])

  useEffect(() => { load(); loadPortfolio(); loadPoolSuggestions(); refreshKlines() }, [])

  // 仅关注列表场景（无持仓）也要在列表加载后预取 K 线摘要，保证技术指标徽章可见
  const watchlistKlineInitDone = useRef(false)
  const klineMissingRetryRef = useRef<Record<string, number>>({})
  useEffect(() => {
    if (watchlistKlineInitDone.current) return
    if (!stocks || stocks.length === 0) return
    watchlistKlineInitDone.current = true
    refreshKlines()
  }, [stocks, refreshKlines])

  // 关注列表变更后，自动补齐缺失的 K 线摘要（避免未配置 agent 时没有技术指标徽章）
  useEffect(() => {
    const enabled = (stocks || []).filter(s => s.enabled)
    if (enabled.length === 0) return
    const now = Date.now()
    const retryGapMs = 2 * 60 * 1000
    const missing = enabled.filter(s => {
      const key = `${s.market || 'CN'}:${s.symbol}`
      if (klineSummaries[key]) return false
      const lastTry = klineMissingRetryRef.current[key] || 0
      return (now - lastTry) > retryGapMs
    })
    if (missing.length === 0) return
    for (const s of missing) {
      const key = `${s.market || 'CN'}:${s.symbol}`
      klineMissingRetryRef.current[key] = now
    }
    refreshKlines()
  }, [stocks, klineSummaries, refreshKlines])

  // Agent 配置弹窗：预览未来触发时间（用于自检工作日/周末语义）
  useEffect(() => {
    if (!agentDialogStock) return
    if (!agents || agents.length === 0) return

    const stockAgentMap = new Map((agentDialogStock.agents || []).map(a => [a.agent_name, a]))
    const schedules = new Set<string>()
    for (const agent of agents) {
      if (agent.execution_mode === 'batch') continue
      const sa = stockAgentMap.get(agent.name)
      if (!sa) continue
      const eff = effectiveSchedule(agent, sa)
      if (eff) schedules.add(eff)
    }

    const toFetch = Array.from(schedules).filter(s => !schedulePreviewCache[s] && !schedulePreviewLoading[s])
    if (toFetch.length === 0) return

    let cancelled = false
    ;(async () => {
      // Mark loading
      setSchedulePreviewLoading(prev => {
        const next = { ...prev }
        for (const s of toFetch) next[s] = true
        return next
      })
      try {
        const pairs = await Promise.all(toFetch.map(async s => {
          try {
            const p = await fetchAPI<SchedulePreview>(`/agents/schedule/preview?schedule=${encodeURIComponent(s)}&count=5`)
            return [s, p] as const
          } catch (e) {
            const msg = e instanceof Error ? e.message : '预览失败'
            return [s, { error: msg }] as const
          }
        }))
        if (cancelled) return
        setSchedulePreviewCache(prev => ({ ...prev, ...Object.fromEntries(pairs) }))
      } finally {
        if (cancelled) return
        setSchedulePreviewLoading(prev => {
          const next = { ...prev }
          for (const s of toFetch) next[s] = false
          return next
        })
      }
    })()

    return () => { cancelled = true }
  }, [agentDialogStock, agents, schedulePreviewCache, schedulePreviewLoading])

  // 触发扫描：调用盘中监控扫描，并刷新建议池
  const scanAndReload = useCallback(async () => {
    setScanning(true)
    try {
      const url = enableAIAnalysis ? '/agents/intraday/scan?analyze=true' : '/agents/intraday/scan'
      await fetchAPI(url, { method: 'POST' })
      await loadPoolSuggestions()
      await refreshKlines()
      setLastRefreshTime(new Date())
    } catch (e) {
      console.error('扫描失败:', e)
    } finally {
      setScanning(false)
    }
  }, [enableAIAnalysis, loadPoolSuggestions, refreshKlines])

  // 首次加载后，按需刷新 K 线摘要与建议池
  const initialKlineDone = useRef(false)
  useEffect(() => {
    if (portfolio && portfolio.accounts.length > 0 && !initialKlineDone.current) {
      initialKlineDone.current = true
      refreshKlines()
      loadPoolSuggestions()
    }
  }, [portfolio, refreshKlines, loadPoolSuggestions])

  // Auto-refresh timer
  useEffect(() => {
    if (autoRefresh) {
      refreshQuotes()
      refreshKlines()
      refreshTimerRef.current = setInterval(() => {
        refreshQuotes()
        refreshKlines()
      }, refreshInterval * 1000)
    } else {
      // Clear interval when disabled
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
  }, [autoRefresh, refreshInterval, refreshQuotes, refreshKlines])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowDropdown(false)
      }
      if (positionDropdownRef.current && !positionDropdownRef.current.contains(e.target as Node)) {
        setShowPositionDropdown(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // ========== Stock handlers ==========
  const doSearch = async (q: string, market: string = searchMarket) => {
    if (q.length < 1) { setSearchResults([]); setShowDropdown(false); return }
    setSearching(true)
    try {
      const marketParam = market ? `&market=${market}` : ''
      const results = await fetchAPI<SearchResult[]>(`/stocks/search?q=${encodeURIComponent(q)}${marketParam}`)
      setSearchResults(results)
      setShowDropdown(results.length > 0)
    } catch { setSearchResults([]) }
    finally { setSearching(false) }
  }

  const handleSearchInput = (value: string) => {
    setSearchQuery(value)
    clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => doSearch(value), 500)
  }

  const handleSearchMarketChange = (market: string) => {
    setSearchMarket(market)
    if (searchQuery) {
      doSearch(searchQuery, market)
    }
  }

  const refreshStockListCache = async () => {
    setRefreshingStockList(true)
    try {
      const result = await fetchAPI<{ count: number }>('/stocks/refresh-list', { method: 'POST' })
      toast(`已刷新股票列表，共 ${result.count} 只`, 'success')
      if (searchQuery) {
        doSearch(searchQuery)
      }
    } catch (e) {
      toast('刷新失败', 'error')
    } finally {
      setRefreshingStockList(false)
    }
  }

  const selectStock = (item: SearchResult) => {
    setStockForm({ symbol: item.symbol, name: item.name, market: item.market })
    setSearchQuery(`${item.symbol} ${item.name}`)
    setShowDropdown(false)
  }

  const handleStockSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    await fetchAPI('/stocks', { method: 'POST', body: JSON.stringify(stockForm) })
    setStockForm(emptyStockForm)
    setSearchQuery('')
    setShowStockForm(false)
    load()
    toast('股票已添加', 'success')
  }

  const hasAnyPositionForStockId = (id: number): boolean => {
    return (portfolio?.accounts || []).some(acc => (acc.positions || []).some(p => p.stock_id === id))
  }

  const removeFromWatchlist = async (stock: Stock) => {
    setRemovingWatchStock(true)
    try {
      await fetchAPI(`/stocks/${stock.id}`, { method: 'PUT', body: JSON.stringify({ enabled: false }) })
      toast('已移除关注（不会影响持仓）', 'success')
      setRemoveWatchStock(null)
      load()
      // 持仓独立存储，但刷新一次可避免 UI 误解
      loadPortfolio()
    } catch (e) {
      toast(e instanceof Error ? e.message : '移除失败', 'error')
    } finally {
      setRemovingWatchStock(false)
    }
  }

  // ========== Account handlers ==========
  const openAccountDialog = (account?: Account) => {
    if (account) {
      setAccountForm({ name: account.name, available_funds: account.available_funds.toString() })
      setEditAccountId(account.id)
    } else {
      setAccountForm(emptyAccountForm)
      setEditAccountId(null)
    }
    setAccountDialogOpen(true)
  }

  const handleAccountSubmit = async () => {
    const payload = {
      name: accountForm.name,
      available_funds: parseFloat(accountForm.available_funds) || 0,
    }
    if (editAccountId) {
      await fetchAPI(`/accounts/${editAccountId}`, { method: 'PUT', body: JSON.stringify(payload) })
    } else {
      await fetchAPI('/accounts', { method: 'POST', body: JSON.stringify(payload) })
    }
    setAccountDialogOpen(false)
    load()
    loadPortfolio()
    toast(editAccountId ? '账户已更新' : '账户已创建', 'success')
  }

  const handleDeleteAccount = async (id: number) => {
    if (!confirm('确定删除该账户？这将同时删除该账户的所有持仓记录')) return
    await fetchAPI(`/accounts/${id}`, { method: 'DELETE' })
    load()
    loadPortfolio()
    toast('账户已删除', 'success')
  }

  // ========== Position handlers ==========
  const openPositionDialog = (accountId: number, position?: Position) => {
    setPositionDialogAccountId(accountId)
    setPositionSearchQuery('')
    setPositionSearchResults([])
    setShowPositionDropdown(false)
    if (position) {
      setPositionForm({
        account_id: accountId,
        stock_id: position.stock_id,
        cost_price: position.cost_price.toString(),
        quantity: position.quantity.toString(),
        invested_amount: position.invested_amount?.toString() || '',
        trading_style: position.trading_style || '',
        stock_symbol: position.symbol,
        stock_name: position.name,
        stock_market: position.market,
      })
      setEditPositionId(position.id)
    } else {
      setPositionForm({
        account_id: accountId,
        stock_id: 0,
        cost_price: '',
        quantity: '',
        invested_amount: '',
        trading_style: '',
        stock_symbol: '',
        stock_name: '',
        stock_market: 'CN',
      })
      setEditPositionId(null)
    }
    setPositionDialogOpen(true)
  }

  const doPositionSearch = async (q: string, market: string = positionSearchMarket) => {
    if (q.length < 1) { setPositionSearchResults([]); setShowPositionDropdown(false); return }
    setPositionSearching(true)
    try {
      const marketParam = market ? `&market=${market}` : ''
      const results = await fetchAPI<SearchResult[]>(`/stocks/search?q=${encodeURIComponent(q)}${marketParam}`)
      setPositionSearchResults(results)
      setShowPositionDropdown(results.length > 0)
    } catch { setPositionSearchResults([]) }
    finally { setPositionSearching(false) }
  }

  const handlePositionSearchInput = (value: string) => {
    setPositionSearchQuery(value)
    clearTimeout(positionSearchTimer.current)
    positionSearchTimer.current = setTimeout(() => doPositionSearch(value), 500)
  }

  const handlePositionSearchMarketChange = (market: string) => {
    setPositionSearchMarket(market)
    if (positionSearchQuery) {
      doPositionSearch(positionSearchQuery, market)
    }
  }

  const selectPositionStock = (item: SearchResult) => {
    // 检查是否已有此股票
    const existing = stocks.find(s => s.symbol === item.symbol && s.market === item.market)
    setPositionForm({
      ...positionForm,
      stock_id: existing?.id || 0,
      stock_symbol: item.symbol,
      stock_name: item.name,
      stock_market: item.market,
    })
    setPositionSearchQuery(`${item.symbol} ${item.name}`)
    setShowPositionDropdown(false)
  }

  const handlePositionSubmit = async () => {
    let stockId = positionForm.stock_id

    // 如果是新增且股票不在自选中，先添加到自选
    if (!editPositionId && !stockId && positionForm.stock_symbol) {
      try {
        const newStock = await fetchAPI<Stock>('/stocks', {
          method: 'POST',
          body: JSON.stringify({
            symbol: positionForm.stock_symbol,
            name: positionForm.stock_name,
            market: positionForm.stock_market,
          })
        })
        stockId = newStock.id
        load() // 刷新股票列表
      } catch (e) {
        // 股票可能已存在，尝试获取
        const existingStocks = await fetchAPI<Stock[]>('/stocks')
        const existing = existingStocks.find(s => s.symbol === positionForm.stock_symbol && s.market === positionForm.stock_market)
        if (existing) {
          stockId = existing.id
        } else {
          toast('添加股票失败', 'error')
          return
        }
      }
    }

    const payload = {
      account_id: positionForm.account_id,
      stock_id: stockId,
      cost_price: parseFloat(positionForm.cost_price),
      quantity: parseInt(positionForm.quantity),
      invested_amount: positionForm.invested_amount ? parseFloat(positionForm.invested_amount) : null,
      trading_style: positionForm.trading_style,  // 空字符串表示清空
    }
    if (editPositionId) {
      await fetchAPI(`/positions/${editPositionId}`, { method: 'PUT', body: JSON.stringify(payload) })
    } else {
      await fetchAPI('/positions', { method: 'POST', body: JSON.stringify(payload) })
    }
    setPositionDialogOpen(false)
    loadPortfolio()
    toast(editPositionId ? '持仓已更新' : '持仓已添加', 'success')
  }

  const handleDeletePosition = async (id: number) => {
    if (!confirm('确定删除该持仓？')) return
    await fetchAPI(`/positions/${id}`, { method: 'DELETE' })
    loadPortfolio()
    toast('持仓已删除', 'success')
  }

  // ========== Agent handlers ==========
  const toggleAgent = async (stock: Stock, agentName: string) => {
    const current = stock.agents || []
    const isAssigned = current.some(a => a.agent_name === agentName)
    const newAgents = isAssigned
      ? current.filter(a => a.agent_name !== agentName)
      : [...current, { agent_name: agentName, schedule: '', ai_model_id: null, notify_channel_ids: [] }]
    await fetchAPI(`/stocks/${stock.id}/agents`, { method: 'PUT', body: JSON.stringify({ agents: newAgents }) })
    load()
    setAgentDialogStock(prev => prev ? { ...prev, agents: newAgents } : null)
  }

  const triggerStockAgent = async (stockId: number, agentName: string) => {
    setTriggeringAgent(agentName)
    setRunningAgents(prev => ({ ...prev, [stockId]: agentName }))
    // 触发后立即关闭配置弹窗，避免多层弹窗干扰
    setAgentDialogStock(null)
    try {
      // 手动触发时跳过节流，方便测试
      const resp = await fetchAPI<{ result: AgentResult }>(`/stocks/${stockId}/agents/${agentName}/trigger?bypass_throttle=true`, { method: 'POST' })
      const result = resp?.result
      if (result) {
        // 仅提示，不再弹出结果弹窗，避免干扰
        toast(result.should_alert ? 'AI 建议关注' : 'AI 判断无需关注', result.should_alert ? 'success' : 'info')
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : '触发失败', 'error')
    } finally {
      setTriggeringAgent(null)
      setRunningAgents(prev => ({ ...prev, [stockId]: null }))
    }
  }

  const updateStockAgentModel = async (stock: Stock, agentName: string, modelId: number | null) => {
    const newAgents = (stock.agents || []).map(a =>
      a.agent_name === agentName ? { ...a, ai_model_id: modelId } : a
    )
    await fetchAPI(`/stocks/${stock.id}/agents`, { method: 'PUT', body: JSON.stringify({ agents: newAgents }) })
    load()
    setAgentDialogStock(prev => prev ? { ...prev, agents: newAgents } : null)
  }

  const toggleStockAgentChannel = async (stock: Stock, agentName: string, channelId: number) => {
    const newAgents = (stock.agents || []).map(a => {
      if (a.agent_name !== agentName) return a
      const current = a.notify_channel_ids || []
      const newIds = current.includes(channelId)
        ? current.filter(id => id !== channelId)
        : [...current, channelId]
      return { ...a, notify_channel_ids: newIds }
    })
    await fetchAPI(`/stocks/${stock.id}/agents`, { method: 'PUT', body: JSON.stringify({ agents: newAgents }) })
    load()
    setAgentDialogStock(prev => prev ? { ...prev, agents: newAgents } : null)
  }

  const updateStockAgentSchedule = async (stock: Stock, agentName: string, schedule: string) => {
    const newAgents = (stock.agents || []).map(a =>
      a.agent_name === agentName ? { ...a, schedule } : a
    )
    await fetchAPI(`/stocks/${stock.id}/agents`, { method: 'PUT', body: JSON.stringify({ agents: newAgents }) })
    load()
    setAgentDialogStock(prev => prev ? { ...prev, agents: newAgents } : null)
  }

  // ========== Helpers ==========
  const formatMoney = (value: number) => {
    if (Math.abs(value) >= 10000) {
      return `${(value / 10000).toFixed(2)}万`
    }
    return value.toFixed(2)
  }

  const marketLabel = (m: string) => m === 'CN' ? 'A股' : m === 'HK' ? '港股' : m === 'US' ? '美股' : m

  // 市场徽章样式和短标签
  const marketBadge = (m: string) => {
    if (m === 'HK') return { style: 'bg-orange-500/10 text-orange-600', label: '港' }
    if (m === 'US') return { style: 'bg-green-500/10 text-green-600', label: '美' }
    return { style: 'bg-blue-500/10 text-blue-600', label: 'A' }
  }

  // 保留原始精度显示价格（不强制截断小数位）
  const formatPrice = (value: number) => {
    // 最多显示4位小数，去除末尾的0
    const formatted = value.toFixed(4).replace(/\.?0+$/, '')
    return formatted
  }

  // 获取股票的行情信息
  const getStockQuote = (symbol: string) => {
    return quotes[symbol] || null
  }

  // 获取股票的建议信息（优先使用建议池，包含来源和时间信息）
  const getSuggestionForStock = (symbol: string, market: string, hasPosition?: boolean): { suggestion: SuggestionInfo | null; kline: KlineSummary | null } => {
    const key = `${market || 'CN'}:${symbol}`
    // 优先使用建议池的建议（包含来源和时间信息）
    const poolSug = poolSuggestions[symbol]
    if (poolSug && !poolSug.is_expired) {
      const preloadedKline = klineSummaries[key] || (suggestions[symbol]?.kline as any) || null
      return {
        suggestion: {
          id: poolSug.id,
          action: poolSug.action,
          action_label: poolSug.action_label,
          signal: poolSug.signal,
          reason: poolSug.reason,
          should_alert: poolSug.should_alert ?? (['alert', 'avoid', 'sell', 'reduce'].includes(poolSug.action)),
          agent_name: poolSug.agent_name,
          agent_label: poolSug.agent_label,
          created_at: poolSug.created_at,
          is_expired: poolSug.is_expired,
          prompt_context: poolSug.prompt_context,
          ai_response: poolSug.ai_response,
          meta: poolSug.meta,
        },
        // 优先使用本页并发预取的 kline 摘要，确保徽章与弹窗一致且免加载
        kline: preloadedKline,
      }
    }

    // 无池建议时，使用 K 线评分构建轻量建议（仅用于徽章展示）
    const ks = klineSummaries[key]
    if (ks) {
      const scored = buildKlineSuggestion(ks as any, hasPosition)
      return {
        suggestion: {
          action: scored.action,
          action_label: scored.action_label,
          signal: scored.signal,
          reason: '',
          should_alert: false,
          agent_label: '技术指标',
        },
        kline: ks,
      }
    }

    return { suggestion: null, kline: null }
  }

  const positionRatio = useMemo(() => {
    if (!portfolio) return null
    const mv = portfolio.total.total_market_value || 0
    const assets = portfolio.total.total_assets || 0
    const pct = assets > 0 ? (mv / assets * 100) : 0
    return { mv, assets, pct }
  }, [portfolio])

  const positionsCount = useMemo(() => {
    return (portfolio?.accounts || []).reduce((acc, a) => acc + (a.positions?.length || 0), 0)
  }, [portfolio])

  const watchlistCount = useMemo(() => {
    return stocks.filter(s => s.enabled).length
  }, [stocks])

  const toggleAccountExpanded = (id: number) => {
    setExpandedAccounts(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // 骨架屏：初始加载时显示
  if (loading) {
    return (
      <div>
        {/* Header Skeleton */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <Skeleton className="h-6 w-16 mb-2" />
            <Skeleton className="h-4 w-32" />
          </div>
          <div className="hidden md:flex items-center gap-3">
            <Skeleton className="h-9 w-24" />
            <Skeleton className="h-9 w-24" />
            <Skeleton className="h-9 w-24" />
          </div>
        </div>
        {/* Summary Cards Skeleton */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="card p-4">
              <Skeleton className="h-4 w-16 mb-2" />
              <Skeleton className="h-6 w-24" />
            </div>
          ))}
        </div>
        {/* Account List Skeleton */}
        <div className="space-y-4">
          {[...Array(2)].map((_, i) => (
            <div key={i} className="card">
              <div className="px-4 py-3 border-b border-border/50">
                <Skeleton className="h-5 w-32" />
              </div>
              <div className="divide-y divide-border/50">
                {[...Array(3)].map((_, j) => (
                  <div key={j} className="px-4 py-3 flex items-center gap-4">
                    <Skeleton className="h-4 w-16" />
                    <Skeleton className="h-4 w-24" />
                    <Skeleton className="h-4 w-16 ml-auto" />
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div>
      {/* Header */}
      <div className="flex flex-col gap-3 mb-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-[20px] md:text-[22px] font-bold text-foreground tracking-tight">持仓</h1>
            <div className="flex items-center gap-2 mt-1 flex-wrap">
              {marketStatus.map(m => {
                const statusColors: Record<string, string> = {
                  trading: 'bg-emerald-500',
                  pre_market: 'bg-amber-500',
                  break: 'bg-amber-500',
                  after_hours: 'bg-slate-400',
                  closed: 'bg-slate-400',
                }
                return (
                  <div key={m.code} className="flex items-center gap-1" title={`${m.sessions.join(', ')} (${m.local_time})`}>
                    <span className={`w-1.5 h-1.5 rounded-full ${statusColors[m.status] || 'bg-slate-400'}`} />
                    <span className="text-[11px] text-muted-foreground">{m.name}</span>
                    <span className={`text-[10px] ${m.is_trading ? 'text-emerald-600' : 'text-muted-foreground/60'}`}>
                      {m.status_text}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
          {/* Desktop buttons + controls */}
          <div className="hidden md:flex items-center gap-3">
            {/* Controls */}
            <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-accent/30">
              <div className="flex items-center gap-1.5">
                <Switch checked={autoRefresh} onCheckedChange={setAutoRefresh} className="scale-90" />
                <span className="text-[11px] text-muted-foreground">自动刷新</span>
                {autoRefresh && (
                  <Select value={refreshInterval.toString()} onValueChange={v => setRefreshInterval(parseInt(v))}>
                    <SelectTrigger className="h-6 w-14 text-[10px] px-1.5">
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
              <div className="w-px h-4 bg-border" />
              <div className="flex items-center gap-1.5">
                <Switch checked={enableAIAnalysis} onCheckedChange={setEnableAIAnalysis} className="scale-90" />
                <span className="text-[11px] text-muted-foreground">AI 建议</span>
                {poolSuggestionsLoading && (
                  <span className="w-3 h-3 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
                )}
                {!poolSuggestionsLoading && Object.keys(poolSuggestions).length > 0 && (
                  <span className="text-[10px] text-primary">
                    {Object.keys(poolSuggestions).length}
                  </span>
                )}
              </div>
              {lastRefreshTime && (
                <>
                  <div className="w-px h-4 bg-border" />
                  <span className="text-[10px] text-muted-foreground/60">
                    {lastRefreshTime.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                  </span>
                </>
              )}
            </div>
            {/* Buttons */}
            <Button variant="secondary" onClick={handleRefresh} disabled={quotesLoading}>
              <RefreshCw className={`w-4 h-4 ${quotesLoading ? 'animate-spin' : ''}`} />
              刷新
            </Button>
            <Button variant="secondary" onClick={scanAndReload} disabled={scanning}>
              <Bot className="w-4 h-4" /> 扫描
            </Button>
            <Button variant="secondary" onClick={() => openAccountDialog()}>
              <Building2 className="w-4 h-4" /> 添加账户
            </Button>
            <Button onClick={() => { setStockForm(emptyStockForm); setSearchQuery(''); setShowStockForm(true) }}>
              <Plus className="w-4 h-4" /> 添加股票
            </Button>
          </div>
          {/* Mobile buttons */}
          <div className="flex md:hidden items-center gap-1.5">
            <Button variant="secondary" size="sm" className="h-8 w-8 p-0" onClick={handleRefresh} disabled={quotesLoading}>
              <RefreshCw className={`w-4 h-4 ${quotesLoading ? 'animate-spin' : ''}`} />
            </Button>
            <Button variant="secondary" size="sm" className="h-8 w-8 p-0" onClick={scanAndReload} disabled={scanning}>
              <Bot className="w-4 h-4" />
            </Button>
            <Button variant="secondary" size="sm" className="h-8 w-8 p-0" onClick={() => openAccountDialog()}>
              <Building2 className="w-4 h-4" />
            </Button>
            <Button size="sm" className="h-8 w-8 p-0" onClick={() => { setStockForm(emptyStockForm); setSearchQuery(''); setShowStockForm(true) }}>
              <Plus className="w-4 h-4" />
            </Button>
          </div>
        </div>
        {/* Mobile Controls row */}
        <div className="flex md:hidden items-center gap-2 flex-wrap">
          <div className="flex items-center gap-2 px-2 py-1 rounded-lg bg-accent/30">
            <div className="flex items-center gap-1">
              <Switch checked={autoRefresh} onCheckedChange={setAutoRefresh} className="scale-90" />
              <span className="text-[11px] text-muted-foreground">自动刷新</span>
              {autoRefresh && (
                <Select value={refreshInterval.toString()} onValueChange={v => setRefreshInterval(parseInt(v))}>
                  <SelectTrigger className="h-6 w-14 text-[10px] px-1.5">
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
            <div className="w-px h-4 bg-border" />
            <div className="flex items-center gap-1">
              <Switch checked={enableAIAnalysis} onCheckedChange={setEnableAIAnalysis} className="scale-90" />
              <span className="text-[11px] text-muted-foreground">AI 建议</span>
              {suggestionsLoading && (
                <span className="w-3 h-3 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
              )}
            </div>
          </div>
          {lastRefreshTime && (
            <span className="text-[10px] text-muted-foreground/60">
              {lastRefreshTime.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
          )}
        </div>
      </div>

      {/* Portfolio Total Summary */}
      {portfolioLoading && !portfolio ? (
        // 首次加载时显示骨架屏
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="card p-4">
              <div className="flex items-center gap-2 mb-2">
                <Skeleton className="h-4 w-4 rounded" />
                <Skeleton className="h-3 w-12" />
              </div>
              <Skeleton className="h-6 w-20" />
            </div>
          ))}
        </div>
      ) : portfolio ? (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-6">
          <div className="card p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <TrendingUp className="w-4 h-4" />
              <span className="text-[12px]">总市值</span>
            </div>
            <div className="text-[20px] font-bold text-foreground font-mono">
              {formatMoney(portfolio.total.total_market_value)}
            </div>
          </div>
          <div className="card p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              {portfolio.total.total_pnl >= 0 ? (
                <ArrowUpRight className="w-4 h-4 text-rose-500" />
              ) : (
                <ArrowDownRight className="w-4 h-4 text-emerald-500" />
              )}
              <span className="text-[12px]">总盈亏</span>
            </div>
            <div className={`text-[20px] font-bold font-mono ${portfolio.total.total_pnl >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
              {portfolio.total.total_pnl >= 0 ? '+' : ''}{formatMoney(portfolio.total.total_pnl)}
              <span className="text-[13px] ml-1.5">
                ({portfolio.total.total_pnl_pct >= 0 ? '+' : ''}{portfolio.total.total_pnl_pct.toFixed(2)}%)
              </span>
            </div>
          </div>

          {(() => {
            let dayPnl = 0
            let prevMv = 0
            const allPos = (portfolio.accounts || []).flatMap(a => a.positions || [])
            for (const p of allPos) {
              if (p.current_price_cny == null || p.change_pct == null) continue
              const prev = p.change_pct === -100 ? null : (p.current_price_cny / (1 + p.change_pct / 100))
              if (prev == null || !isFinite(prev)) continue
              const qty = p.quantity || 0
              dayPnl += (p.current_price_cny - prev) * qty
              prevMv += prev * qty
            }
            const pct = prevMv > 0 ? (dayPnl / prevMv * 100) : 0
            const isUp = dayPnl >= 0
            return (
              <div className="card p-4">
                <div className="flex items-center gap-2 text-muted-foreground mb-1">
                  {isUp ? (
                    <ArrowUpRight className="w-4 h-4 text-rose-500" />
                  ) : (
                    <ArrowDownRight className="w-4 h-4 text-emerald-500" />
                  )}
                  <span className="text-[12px]">今日盈亏</span>
                </div>
                <div className={`text-[20px] font-bold font-mono ${isUp ? 'text-rose-500' : 'text-emerald-500'}`}>
                  {isUp ? '+' : ''}{formatMoney(dayPnl)}
                  <span className="text-[13px] ml-1.5">({pct >= 0 ? '+' : ''}{pct.toFixed(2)}%)</span>
                </div>
              </div>
            )
          })()}

          <div className="card p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Wallet className="w-4 h-4" />
              <span className="text-[12px]">可用资金</span>
            </div>
            <div className="text-[20px] font-bold text-foreground font-mono">
              {formatMoney(portfolio.total.available_funds)}
            </div>
          </div>
          <div className="card p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <PiggyBank className="w-4 h-4" />
              <span className="text-[12px]">总资产</span>
            </div>
            <div className="text-[20px] font-bold text-foreground font-mono">
              {formatMoney(portfolio.total.total_assets)}
            </div>
          </div>

          <div className="card p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Bell className="w-4 h-4" />
              <span className="text-[12px]">仓位占比</span>
            </div>
            <div className="text-[20px] font-bold text-foreground font-mono">
              {positionRatio ? `${positionRatio.pct.toFixed(1)}%` : '--'}
            </div>
            <div className="mt-1 text-[11px] text-muted-foreground line-clamp-1">
              {positionRatio ? `持仓市值 ${formatMoney(positionRatio.mv)} / 总资产 ${formatMoney(positionRatio.assets)}` : '—'}
            </div>
          </div>
        </div>
      ) : null}

      {/* Tabs: Positions / Watchlist */}
      <div className="mb-4">
        <div className="inline-flex items-center gap-1 p-1 rounded-lg bg-accent/30">
          <button
            onClick={() => setViewTab('positions')}
            className={`px-3 py-1.5 rounded-md text-[12px] transition-colors ${
              viewTab === 'positions'
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            持仓 <span className="ml-1 font-mono text-[11px] opacity-70">{positionsCount}</span>
          </button>
          <button
            onClick={() => setViewTab('watchlist')}
            className={`px-3 py-1.5 rounded-md text-[12px] transition-colors ${
              viewTab === 'watchlist'
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            关注 <span className="ml-1 font-mono text-[11px] opacity-70">{watchlistCount}</span>
          </button>
        </div>
      </div>

      {/* Add Stock Dialog */}
      <Dialog open={showStockForm} onOpenChange={(open) => { setShowStockForm(open); if (!open) { setSearchQuery(''); setSearchMarket('') } }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>添加股票到自选</DialogTitle>
            <DialogDescription>搜索并添加到自选股列表</DialogDescription>
          </DialogHeader>
          <form onSubmit={handleStockSubmit}>
            <div className="relative" ref={dropdownRef}>
              <div className="flex items-center gap-2 mb-2">
                <Label className="mb-0">搜索股票</Label>
                <div className="flex items-center gap-1">
                  {[
                    { value: '', label: '全部' },
                    { value: 'CN', label: 'A股' },
                    { value: 'HK', label: '港股' },
                    { value: 'US', label: '美股' },
                  ].map(opt => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => handleSearchMarketChange(opt.value)}
                      className={`text-[11px] px-2 py-0.5 rounded transition-colors ${
                        searchMarket === opt.value
                          ? 'bg-primary text-primary-foreground'
                          : 'bg-accent/50 text-muted-foreground hover:bg-accent'
                      }`}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  onClick={refreshStockListCache}
                  disabled={refreshingStockList}
                  className="text-[10px] text-muted-foreground hover:text-foreground transition-colors ml-2"
                  title="搜索不到？点击刷新股票列表"
                >
                  {refreshingStockList ? (
                    <span className="flex items-center gap-1">
                      <RefreshCw className="w-3 h-3 animate-spin" /> 刷新中...
                    </span>
                  ) : (
                    <span className="flex items-center gap-1">
                      <RefreshCw className="w-3 h-3" /> 刷新列表
                    </span>
                  )}
                </button>
              </div>
              <div className="relative">
                <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground/50" />
                <Input
                  value={searchQuery}
                  onChange={e => handleSearchInput(e.target.value)}
                  onFocus={() => searchResults.length > 0 && setShowDropdown(true)}
                  placeholder={searchMarket === 'HK' ? '代码或名称，如 00700 或 腾讯' : searchMarket === 'US' ? '代码或名称，如 AAPL 或 苹果' : '代码或名称，如 600519 或 茅台'}
                  className="pl-10"
                  autoComplete="off"
                />
                {searching && <span className="absolute right-3.5 top-1/2 -translate-y-1/2 w-4 h-4 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />}
              </div>
              {showDropdown && (
                <div className="absolute z-50 w-full mt-2 max-h-64 overflow-auto scrollbar card shadow-lg">
                  {searchResults.map(item => (
                    <button
                      key={`${item.market}-${item.symbol}`}
                      type="button"
                      onClick={() => selectStock(item)}
                      className="w-full flex items-center gap-3 px-4 py-3 text-[13px] hover:bg-accent/50 text-left transition-colors"
                    >
                      <span className="font-mono text-muted-foreground text-[12px] w-14">{item.symbol}</span>
                      <span className="flex-1 font-medium text-foreground">{item.name}</span>
                      <Badge variant="secondary">{marketLabel(item.market)}</Badge>
                    </button>
                  ))}
                </div>
              )}
              {stockForm.symbol && (
                <div className="mt-2.5 flex items-center gap-2">
                  <Badge><span className="font-mono">{stockForm.symbol}</span> {stockForm.name}</Badge>
                  <Badge variant="secondary">{marketLabel(stockForm.market)}</Badge>
                </div>
              )}
            </div>
            <div className="mt-6 flex items-center gap-3 justify-end">
              <Button type="button" variant="ghost" onClick={() => { setShowStockForm(false); setSearchQuery('') }}>取消</Button>
              <Button type="submit" disabled={!stockForm.symbol}>确认添加</Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      {/* Accounts & Positions */}
      {viewTab === 'positions' && (
        portfolio && portfolio.accounts.length === 0 ? (
          <div className="card flex flex-col items-center justify-center py-20">
            <div className="w-14 h-14 rounded-xl bg-primary/10 flex items-center justify-center mb-4">
              <Building2 className="w-6 h-6 text-primary" />
            </div>
            <p className="text-[15px] font-semibold text-foreground">还没有账户</p>
            <p className="text-[13px] text-muted-foreground mt-1.5">点击"添加账户"创建你的第一个交易账户</p>
          </div>
        ) : (
          <div className="space-y-4">
            {portfolio?.accounts.map(account => (
              <div key={account.id} className="card overflow-hidden">
              {/* Account Header */}
              <div
                className="flex flex-col md:flex-row md:items-center justify-between p-3 md:p-4 cursor-pointer hover:bg-accent/30 transition-colors gap-2"
                onClick={() => toggleAccountExpanded(account.id)}
              >
                <div className="flex items-center gap-2 md:gap-3">
                  {expandedAccounts.has(account.id) ? (
                    <ChevronDown className="w-4 h-4 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-muted-foreground" />
                  )}
                  <Building2 className="w-4 h-4 text-primary" />
                  <span className="text-[14px] md:text-[15px] font-semibold text-foreground">{account.name}</span>
                  <span className="text-[11px] md:text-[12px] text-muted-foreground">
                    {account.positions.length} 只
                  </span>
                </div>
                <div className="flex items-center justify-between md:justify-end gap-3 md:gap-6 pl-6 md:pl-0">
                  <div className="flex items-center gap-3 md:gap-6">
                    <div className="text-left md:text-right">
                      <div className="text-[10px] md:text-[11px] text-muted-foreground">市值</div>
                      <div className="text-[12px] md:text-[13px] font-mono font-medium">{formatMoney(account.total_market_value)}</div>
                    </div>
                    <div className="text-left md:text-right">
                      <div className="text-[10px] md:text-[11px] text-muted-foreground">盈亏</div>
                      <div className={`text-[12px] md:text-[13px] font-mono font-medium ${account.total_pnl >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                        {account.total_pnl >= 0 ? '+' : ''}{formatMoney(account.total_pnl)}
                        <span className="text-[10px] md:text-[11px] ml-1">({account.total_pnl_pct >= 0 ? '+' : ''}{account.total_pnl_pct.toFixed(2)}%)</span>
                      </div>
                    </div>
                    <div className="text-left md:text-right hidden sm:block">
                      <div className="text-[10px] md:text-[11px] text-muted-foreground">可用</div>
                      <div className="text-[12px] md:text-[13px] font-mono">{formatMoney(account.available_funds)}</div>
                    </div>
                  </div>
                  <div className="flex items-center gap-0.5 md:gap-1" onClick={e => e.stopPropagation()}>
                    <Button variant="ghost" size="icon" className="h-7 w-7 md:h-8 md:w-8" onClick={() => openPositionDialog(account.id)}>
                      <Plus className="w-3 md:w-3.5 h-3 md:h-3.5" />
                    </Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7 md:h-8 md:w-8" onClick={() => openAccountDialog(accounts.find(a => a.id === account.id))}>
                      <Pencil className="w-3 md:w-3.5 h-3 md:h-3.5" />
                    </Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7 md:h-8 md:w-8 hover:text-destructive" onClick={() => handleDeleteAccount(account.id)}>
                      <Trash2 className="w-3 md:w-3.5 h-3 md:h-3.5" />
                    </Button>
                  </div>
                </div>
              </div>

              {/* Positions */}
              {expandedAccounts.has(account.id) && (
                <div className="border-t border-border/30">
                  {account.positions.length === 0 ? (
                    <p className="text-[13px] text-muted-foreground text-center py-8">暂无持仓，点击 + 添加</p>
                  ) : (
                    <>
                      {/* Desktop Table */}
                      <div className="hidden md:block overflow-x-auto">
                        <table className="w-full">
                          <thead>
                            <tr className="border-b border-border/30 bg-accent/20">
                              <th className="text-left px-4 py-2 text-[11px] font-semibold text-muted-foreground">股票</th>
                              <th className="text-right px-4 py-2 text-[11px] font-semibold text-muted-foreground">现价</th>
                              <th className="text-right px-4 py-2 text-[11px] font-semibold text-muted-foreground">涨跌</th>
                              <th className="text-right px-4 py-2 text-[11px] font-semibold text-muted-foreground">成本</th>
                              <th className="text-right px-4 py-2 text-[11px] font-semibold text-muted-foreground">持仓</th>
                              <th className="text-right px-4 py-2 text-[11px] font-semibold text-muted-foreground">市值</th>
                              <th className="text-right px-4 py-2 text-[11px] font-semibold text-muted-foreground">盈亏</th>
                              <th className="text-center px-4 py-2 text-[11px] font-semibold text-muted-foreground">风格</th>
                              <th className="text-left px-4 py-2 text-[11px] font-semibold text-muted-foreground">Agent</th>
                              <th className="text-center px-4 py-2 text-[11px] font-semibold text-muted-foreground">操作</th>
                            </tr>
                          </thead>
                          <tbody>
                            {account.positions.map((pos, i) => {
                              const stock = stocks.find(s => s.id === pos.stock_id)
                              const badge = marketBadge(pos.market)
                              const isForeign = pos.market === 'HK' || pos.market === 'US'
                              const changeColor = pos.change_pct != null
                                ? (pos.change_pct > 0 ? 'text-rose-500' : pos.change_pct < 0 ? 'text-emerald-500' : 'text-muted-foreground')
                                : 'text-muted-foreground'
                              const pnlColor = pos.pnl != null
                                ? (pos.pnl > 0 ? 'text-rose-500' : pos.pnl < 0 ? 'text-emerald-500' : 'text-muted-foreground')
                                : 'text-muted-foreground'
                              return (
                                <tr key={pos.id} className={`group hover:bg-accent/30 transition-colors ${i > 0 ? 'border-t border-border/20' : ''}`}>
                                  <td className="px-4 py-2.5">
                                    <span className={`text-[9px] px-1 py-0.5 rounded mr-1.5 ${badge.style}`}>{badge.label}</span>
                                    <button
                                      className="font-mono text-[12px] font-semibold text-foreground hover:text-primary"
                                      onClick={() => openStockDetail(pos.symbol, pos.market, pos.name, true)}
                                    >
                                      {pos.symbol}
                                    </button>
                                    <button
                                      className="ml-1.5 text-[12px] text-muted-foreground hover:text-primary"
                                      onClick={() => openStockDetail(pos.symbol, pos.market, pos.name, true)}
                                    >
                                      {pos.name}
                                    </button>
                                    {(() => {
                                      const { suggestion, kline } = getSuggestionForStock(pos.symbol, pos.market, true)
                                      return (suggestion || kline) ? (
                                        <span className="ml-2">
                                          <SuggestionBadge
                                            suggestion={suggestion}
                                            stockName={pos.name}
                                            stockSymbol={pos.symbol}
                                            kline={kline}
                                            market={pos.market}
                                            hasPosition={true}
                                          />
                                        </span>
                                      ) : null
                                    })()}
                                  </td>
                                  <td className={`px-4 py-2.5 text-right font-mono text-[12px] ${changeColor}`}>
                                    {pos.current_price != null ? <span>{pos.current_price.toFixed(2)}{isForeign ? (pos.market === 'HK' ? ' HKD' : ' USD') : ''}</span> : '—'}
                                  </td>
                                  <td className={`px-4 py-2.5 text-right font-mono text-[12px] ${changeColor}`}>
                                    {pos.change_pct != null ? `${pos.change_pct >= 0 ? '+' : ''}${pos.change_pct.toFixed(2)}%` : '—'}
                                  </td>
                                  <td className="px-4 py-2.5 text-right font-mono text-[12px] text-muted-foreground">{formatPrice(pos.cost_price)}</td>
                                  <td className="px-4 py-2.5 text-right font-mono text-[12px] text-muted-foreground">{pos.quantity}</td>
                                  <td className="px-4 py-2.5 text-right font-mono text-[12px] text-muted-foreground">
                                    {pos.market_value != null ? (
                                      <div className="flex flex-col items-end">
                                        {isForeign ? (
                                          <>
                                            <span>{formatMoney(pos.market_value)} {pos.market === 'HK' ? 'HKD' : 'USD'}</span>
                                            {pos.market_value_cny && <span className="text-[10px] text-muted-foreground/60">≈{formatMoney(pos.market_value_cny)}</span>}
                                          </>
                                        ) : <span>{formatMoney(pos.market_value)}</span>}
                                      </div>
                                    ) : '—'}
                                  </td>
                                  <td className={`px-4 py-2.5 text-right font-mono text-[12px] ${pnlColor}`}>
                                    {pos.pnl != null ? (
                                      <div className="flex flex-col items-end">
                                        <span>{pos.pnl >= 0 ? '+' : ''}{formatMoney(pos.pnl)}</span>
                                        <span className="text-[10px] opacity-70">{pos.pnl_pct != null ? `${pos.pnl_pct >= 0 ? '+' : ''}${pos.pnl_pct.toFixed(2)}%` : ''}{isForeign && ' CNY'}</span>
                                      </div>
                                    ) : '—'}
                                  </td>
                                  <td className="px-4 py-2.5 text-center">
                                    {pos.trading_style ? (
                                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${pos.trading_style === 'short' ? 'bg-rose-500/10 text-rose-600' : pos.trading_style === 'long' ? 'bg-blue-500/10 text-blue-600' : 'bg-amber-500/10 text-amber-600'}`}>
                                        {pos.trading_style === 'short' ? '短线' : pos.trading_style === 'long' ? '长线' : '波段'}
                                      </span>
                                    ) : (
                                      <span className="text-[10px] text-muted-foreground/50">-</span>
                                    )}
                                  </td>
                                  <td className="px-4 py-2.5">
                                    {stock && (
                                      <button onClick={() => setAgentDialogStock(stock)} className="flex items-center gap-1.5 hover:opacity-70 transition-opacity">
                                        {stock.agents && stock.agents.length > 0 ? (
                                          <div className="flex items-center gap-1.5 flex-wrap">
                                            {stock.agents.map(sa => {
                                              const agent = agents.find(a => a.name === sa.agent_name)
                                              const isRunning = runningAgents[stock.id] === sa.agent_name
                                              return (
                                                <span key={sa.agent_name} className="inline-flex items-center gap-1">
                                                  <Badge variant="default" className="text-[10px]">{agent?.display_name || sa.agent_name}</Badge>
                                                  {isRunning && (
                                                    <span className="inline-flex items-center gap-1 text-[10px] text-amber-600">
                                                      <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                                                      执行中
                                                    </span>
                                                  )}
                                                </span>
                                              )
                                            })}
                                          </div>
                                        ) : (
                                          <span className="text-[11px] text-muted-foreground/50 flex items-center gap-1"><Bot className="w-3 h-3" /> 未配置</span>
                                        )}
                                      </button>
                                    )}
                                  </td>
                                  <td className="px-4 py-2.5 text-center">
                                    <div className="flex items-center justify-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                      {(() => { const { suggestion, kline } = getSuggestionForStock(pos.symbol, pos.market, true); return (!suggestion && !kline) ? (
                                        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openKlineDialog(pos.symbol, pos.market, pos.name, true)} title="K线指标"><BarChart3 className="w-3 h-3" /></Button>
                                      ) : null })()}
                                      <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openNewsDialog(pos.name)} title="相关资讯"><Newspaper className="w-3 h-3" /></Button>
                                      <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openStockDetail(pos.symbol, pos.market, pos.name, true)} title="详情"><ExternalLink className="w-3 h-3" /></Button>
                                      <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openPositionDialog(account.id, pos)}><Pencil className="w-3 h-3" /></Button>
                                      <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-destructive" onClick={() => handleDeletePosition(pos.id)}><Trash2 className="w-3 h-3" /></Button>
                                    </div>
                                  </td>
                                </tr>
                              )
                            })}
                          </tbody>
                        </table>
                      </div>

                      {/* Mobile Cards */}
                      <div className="md:hidden divide-y divide-border/30">
                        {account.positions.map(pos => {
                          const stock = stocks.find(s => s.id === pos.stock_id)
                          const badge = marketBadge(pos.market)
                          const changeColor = pos.change_pct != null
                            ? (pos.change_pct > 0 ? 'text-rose-500' : pos.change_pct < 0 ? 'text-emerald-500' : 'text-muted-foreground')
                            : 'text-muted-foreground'
                          const pnlColor = pos.pnl != null
                            ? (pos.pnl > 0 ? 'text-rose-500' : pos.pnl < 0 ? 'text-emerald-500' : 'text-muted-foreground')
                            : 'text-muted-foreground'
                          return (
                            <div key={pos.id} className="p-3 hover:bg-accent/30 transition-colors">
                              {/* Row 1: Stock info + Current price */}
                              <div className="flex items-center justify-between mb-2">
                                <div className="flex items-center gap-1.5 flex-wrap">
                                  <span className={`text-[9px] px-1 py-0.5 rounded ${badge.style}`}>{badge.label}</span>
                                  <button
                                    className="font-mono text-[12px] font-semibold text-foreground hover:text-primary"
                                    onClick={() => openStockDetail(pos.symbol, pos.market, pos.name, true)}
                                  >
                                    {pos.symbol}
                                  </button>
                                  <button
                                    className="text-[12px] text-muted-foreground hover:text-primary"
                                    onClick={() => openStockDetail(pos.symbol, pos.market, pos.name, true)}
                                  >
                                    {pos.name}
                                  </button>
                                  {pos.trading_style && (
                                    <span className={`text-[9px] px-1 py-0.5 rounded ${pos.trading_style === 'short' ? 'bg-rose-500/10 text-rose-600' : pos.trading_style === 'long' ? 'bg-blue-500/10 text-blue-600' : 'bg-amber-500/10 text-amber-600'}`}>
                                      {pos.trading_style === 'short' ? '短' : pos.trading_style === 'long' ? '长' : '波'}
                                    </span>
                                  )}
                                  {(() => {
                                    const { suggestion, kline } = getSuggestionForStock(pos.symbol, pos.market, true)
                                    return (suggestion || kline) ? (
                                      <SuggestionBadge
                                        suggestion={suggestion}
                                        stockName={pos.name}
                                        stockSymbol={pos.symbol}
                                        kline={kline}
                                        market={pos.market}
                                        hasPosition={true}
                                      />
                                    ) : null
                                  })()}
                                </div>
                                <div className={`font-mono text-[13px] font-medium ${changeColor}`}>
                                  {pos.current_price?.toFixed(2) || '—'}
                                  {pos.change_pct != null && <span className="text-[11px] ml-1">{pos.change_pct >= 0 ? '+' : ''}{pos.change_pct.toFixed(2)}%</span>}
                                </div>
                              </div>
                              {/* Row 2: Details */}
                              <div className="flex items-center justify-between text-[11px]">
                                <div className="flex items-center gap-3">
                                  <span className="text-muted-foreground">成本 <span className="font-mono text-foreground">{formatPrice(pos.cost_price)}</span></span>
                                  <span className="text-muted-foreground">数量 <span className="font-mono text-foreground">{pos.quantity}</span></span>
                                </div>
                                <div className={`font-mono ${pnlColor}`}>
                                  {pos.pnl != null ? `${pos.pnl >= 0 ? '+' : ''}${formatMoney(pos.pnl)}` : '—'}
                                  {pos.pnl_pct != null && <span className="ml-1">({pos.pnl_pct >= 0 ? '+' : ''}{pos.pnl_pct.toFixed(2)}%)</span>}
                                </div>
                              </div>
                              {/* Row 3: Actions */}
                              <div className="flex items-center justify-between mt-2 pt-2 border-t border-border/20">
                                <div>
                                  {stock && stock.agents && stock.agents.length > 0 ? (
                                    <button onClick={() => setAgentDialogStock(stock)} className="flex items-center gap-1">
                                      {stock.agents.slice(0, 2).map(sa => {
                                        const agent = agents.find(a => a.name === sa.agent_name)
                                        const isRunning = runningAgents[stock.id] === sa.agent_name
                                        return (
                                          <span key={sa.agent_name} className="inline-flex items-center gap-1">
                                            <Badge variant="secondary" className="text-[9px]">{agent?.display_name || sa.agent_name}</Badge>
                                            {isRunning && (
                                              <span className="inline-flex items-center gap-1 text-[10px] text-amber-600">
                                                <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                                                执行中
                                              </span>
                                            )}
                                          </span>
                                        )
                                      })}
                                    </button>
                                  ) : (
                                    <button onClick={() => stock && setAgentDialogStock(stock)} className="text-[10px] text-muted-foreground/50 flex items-center gap-1">
                                      <Bot className="w-3 h-3" /> Agent
                                    </button>
                                  )}
                                </div>
                                <div className="flex items-center gap-1">
                                  {(() => { const { suggestion, kline } = getSuggestionForStock(pos.symbol, pos.market, true); return (!suggestion && !kline) ? (
                                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openKlineDialog(pos.symbol, pos.market, pos.name, true)} title="K线指标"><BarChart3 className="w-3 h-3" /></Button>
                                  ) : null })()}
                                  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openNewsDialog(pos.name)}><Newspaper className="w-3 h-3" /></Button>
                                  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openStockDetail(pos.symbol, pos.market, pos.name, true)} title="详情"><ExternalLink className="w-3 h-3" /></Button>
                                  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openPositionDialog(account.id, pos)}><Pencil className="w-3 h-3" /></Button>
                                  <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-destructive" onClick={() => handleDeletePosition(pos.id)}><Trash2 className="w-3 h-3" /></Button>
                                </div>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
        )
      )}

      {/* Watchlist */}
      {viewTab === 'watchlist' && (
        <div className="card p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-[13px] font-semibold text-foreground">关注列表</h3>
            <div className="flex items-center gap-1">
              {[
                { value: '', label: '全部', count: stocks.filter(s => s.enabled).length },
                { value: 'CN', label: 'A股', count: stocks.filter(s => s.enabled && s.market === 'CN').length },
                { value: 'HK', label: '港股', count: stocks.filter(s => s.enabled && s.market === 'HK').length },
                { value: 'US', label: '美股', count: stocks.filter(s => s.enabled && s.market === 'US').length },
              ].map(opt => (
                <button
                  key={opt.value}
                  onClick={() => setStockListFilter(opt.value)}
                  className={`text-[11px] px-2 py-0.5 rounded transition-colors ${
                    stockListFilter === opt.value
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-accent/50 text-muted-foreground hover:bg-accent'
                  }`}
                >
                  {opt.label} ({opt.count})
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center justify-between mb-3">
            <div className="text-[11px] text-muted-foreground">筛选</div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setWatchlistOnlyAlerts(!watchlistOnlyAlerts)}
                className={`text-[11px] px-2.5 py-1 rounded-md border transition-colors ${
                  watchlistOnlyAlerts
                    ? 'bg-rose-500/10 border-rose-500/30 text-rose-600'
                    : 'bg-accent/30 border-border/50 text-muted-foreground hover:border-rose-500/30'
                }`}
                title="只显示需要关注/预警的股票"
              >
                仅预警
              </button>
            </div>
          </div>
          {stocks.filter(s => s.enabled).length === 0 ? (
            <div className="py-12 text-center">
              <div className="text-[13px] text-muted-foreground">还没有添加关注股票</div>
              <div className="mt-2 text-[11px] text-muted-foreground/70">点击右上角“添加股票”开始</div>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {stocks
                .filter(s => s.enabled && (!stockListFilter || s.market === stockListFilter))
                .map(stock => {
                  const quote = getStockQuote(stock.symbol)
                  const { suggestion, kline } = getSuggestionForStock(stock.symbol, stock.market, false)
                  const action = suggestion?.action || ''
                  const aiPriority = suggestion?.agent_label && suggestion.agent_label !== '技术指标'
                    ? (['sell', 'reduce', 'avoid', 'alert'].includes(action) ? 30 : 20)
                    : 0
                  const techPriority = !aiPriority && kline
                    ? (['sell', 'reduce', 'avoid', 'alert'].includes(buildKlineSuggestion(kline as any, false).action) ? 10 : 0)
                    : 0
                  const movePriority = quote?.change_pct != null ? Math.min(9, Math.floor(Math.abs(quote.change_pct))) : 0
                  const alertPriority = suggestion?.should_alert ? 100 : 0
                  return { stock, quote, suggestion, kline, _p: alertPriority + aiPriority + techPriority + movePriority }
                })
                .filter(({ suggestion }) => {
                  if (!watchlistOnlyAlerts) return true
                  return !!suggestion?.should_alert
                })
                .sort((a, b) => b._p - a._p)
                .map(({ stock }) => {
                const quote = getStockQuote(stock.symbol)
                const changeColor = quote?.change_pct != null
                  ? (quote.change_pct > 0 ? 'text-rose-500' : quote.change_pct < 0 ? 'text-emerald-500' : 'text-muted-foreground')
                  : 'text-muted-foreground'
                const { suggestion, kline } = getSuggestionForStock(stock.symbol, stock.market, false)
                return (
                  <div
                    key={stock.id}
                    className="group rounded-xl border border-border/40 bg-background/30 hover:bg-accent/20 transition-colors p-3 cursor-pointer"
                    onClick={() => {
                      if (isSuppressCardClick()) return
                      setAgentDialogStock(stock)
                    }}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className={`text-[9px] px-1 py-0.5 rounded ${marketBadge(stock.market).style}`}>
                            {marketBadge(stock.market).label}
                          </span>
                          <button
                            className="font-mono text-[12px] font-semibold text-foreground hover:text-primary"
                            onClick={(e) => { e.stopPropagation(); openStockDetail(stock.symbol, stock.market, stock.name, false) }}
                          >
                            {stock.symbol}
                          </button>
                          <button
                            className="text-[12px] text-muted-foreground truncate hover:text-primary"
                            onClick={(e) => { e.stopPropagation(); openStockDetail(stock.symbol, stock.market, stock.name, false) }}
                          >
                            {stock.name}
                          </button>
                        </div>
                      </div>
                      <div className="text-right">
                        <div className={`font-mono text-[14px] font-bold leading-tight ${changeColor}`}>
                          {quote?.current_price != null ? quote.current_price.toFixed(2) : '--'}
                        </div>
                        <div className={`font-mono text-[11px] leading-tight ${changeColor}`}>
                          {quote?.change_pct != null ? `${quote.change_pct >= 0 ? '+' : ''}${quote.change_pct.toFixed(2)}%` : '--'}
                        </div>
                      </div>
                    </div>

                    <div className="mt-2">
                      {(suggestion || kline) ? (
                        <SuggestionBadge
                          suggestion={suggestion}
                          stockName={stock.name}
                          stockSymbol={stock.symbol}
                          kline={kline}
                          market={stock.market}
                          hasPosition={false}
                        />
                      ) : (
                        <div className="text-[11px] text-muted-foreground/70 py-2">暂无技术面/AI 分析</div>
                      )}
                    </div>

                    <div className="mt-2 pt-2 border-t border-border/30 flex items-center justify-between gap-2">
                      <div className="flex items-center gap-1 flex-wrap">
                        {stock.agents && stock.agents.length > 0 ? (
                          <Badge variant="secondary" className="text-[10px]">{stock.agents.length} Agent</Badge>
                        ) : (
                          <span className="text-[10px] text-muted-foreground/60">未配置 Agent</span>
                        )}
                        {runningAgents[stock.id] && (
                          <span className="inline-flex items-center gap-1 text-[10px] text-amber-600">
                            <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                            {agents.find(a => a.name === runningAgents[stock.id])?.display_name || runningAgents[stock.id]}
                          </span>
                        )}
                      </div>
                      <div
                        className="flex items-center gap-1 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => openKlineDialog(stock.symbol, stock.market, stock.name, false)}
                          title="K线指标"
                        >
                          <BarChart3 className="w-3.5 h-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => openNewsDialog(stock.name)}
                          title="相关资讯"
                        >
                          <Newspaper className="w-3.5 h-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => openStockDetail(stock.symbol, stock.market, stock.name, false)}
                          title="详情"
                        >
                          <ExternalLink className="w-3.5 h-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7 hover:text-destructive"
                          onClick={() => setRemoveWatchStock(stock)}
                          title="移除关注（不影响持仓）"
                        >
                          <X className="w-3.5 h-3.5" />
                        </Button>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* Kline Dialog */}
      <KlineSummaryDialog
        open={klineDialogOpen}
        onOpenChange={setKlineDialogOpen}
        symbol={klineDialogSymbol}
        market={klineDialogMarket}
        stockName={klineDialogName}
        hasPosition={klineDialogHasPosition}
        initialSummary={klineDialogInitialSummary as any}
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

      {/* Remove Watchlist Dialog */}
      <Dialog open={!!removeWatchStock} onOpenChange={(open) => { if (!open) setRemoveWatchStock(null) }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>移除关注</DialogTitle>
            <DialogDescription>仅从关注列表移除，不会删除持仓</DialogDescription>
          </DialogHeader>
          {removeWatchStock && (
            <div className="space-y-4 mt-2">
              <div className="rounded-lg border border-border/40 bg-accent/20 p-3">
                <div className="text-[13px] font-semibold text-foreground">
                  {removeWatchStock.name}
                  <span className="ml-2 font-mono text-[12px] text-muted-foreground">{removeWatchStock.symbol}</span>
                </div>
                <div className="mt-1 text-[12px] text-muted-foreground">
                  {hasAnyPositionForStockId(removeWatchStock.id)
                    ? '该股票在持仓中。移除关注不影响持仓；如需删除持仓，请到“持仓”Tab 删除持仓记录。'
                    : '移除后将不再出现在关注列表，也不会在自选行情中拉取。'}
                </div>
              </div>

              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setRemoveWatchStock(null)} disabled={removingWatchStock}>取消</Button>
                <Button variant="destructive" onClick={() => removeFromWatchlist(removeWatchStock)} disabled={removingWatchStock}>
                  {removingWatchStock ? '处理中…' : '移除关注'}
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Account Dialog */}
      <Dialog open={accountDialogOpen} onOpenChange={setAccountDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editAccountId ? '编辑账户' : '添加账户'}</DialogTitle>
            <DialogDescription>设置交易账户信息</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label>账户名称</Label>
              <Input
                value={accountForm.name}
                onChange={e => setAccountForm({ ...accountForm, name: e.target.value })}
                placeholder="如：招商证券、华泰证券"
              />
            </div>
            <div>
              <Label>可用资金（元）</Label>
              <Input
                value={accountForm.available_funds}
                onChange={e => setAccountForm({ ...accountForm, available_funds: e.target.value })}
                placeholder="0"
                className="font-mono"
                inputMode="decimal"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setAccountDialogOpen(false)}>取消</Button>
              <Button onClick={handleAccountSubmit} disabled={!accountForm.name}>
                {editAccountId ? '保存' : '创建'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Position Dialog */}
      <Dialog
        open={positionDialogOpen}
        onOpenChange={(open) => {
          setPositionDialogOpen(open)
          if (!open) {
            setPositionSearchQuery('')
            setPositionSearchResults([])
            setShowPositionDropdown(false)
            setPositionSearchMarket('')
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editPositionId ? '编辑持仓' : '添加持仓'}</DialogTitle>
            <DialogDescription>
              {accounts.find(a => a.id === positionDialogAccountId)?.name} 账户持仓
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            {editPositionId ? (
              <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-accent/30">
                <span className={`text-[9px] px-1.5 py-0.5 rounded ${marketBadge(positionForm.stock_market).style}`}>
                  {marketBadge(positionForm.stock_market).label}
                </span>
                <span className="font-mono text-[12px] text-muted-foreground">{positionForm.stock_symbol}</span>
                <span className="text-[13px] text-foreground">{positionForm.stock_name}</span>
              </div>
            ) : (
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <Label className="mb-0">搜索股票</Label>
                  <div className="flex items-center gap-1">
                    {[
                      { value: '', label: '全部' },
                      { value: 'CN', label: 'A股' },
                      { value: 'HK', label: '港股' },
                      { value: 'US', label: '美股' },
                    ].map(opt => (
                      <button
                        key={opt.value}
                        type="button"
                        onClick={() => handlePositionSearchMarketChange(opt.value)}
                        className={`text-[11px] px-2 py-0.5 rounded transition-colors ${
                          positionSearchMarket === opt.value
                            ? 'bg-primary text-primary-foreground'
                            : 'bg-accent/50 text-muted-foreground hover:bg-accent'
                        }`}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="relative" ref={positionDropdownRef}>
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground/50" />
                  <Input
                    value={positionSearchQuery}
                    onChange={e => handlePositionSearchInput(e.target.value)}
                    onFocus={() => positionSearchResults.length > 0 && setShowPositionDropdown(true)}
                    placeholder={positionSearchMarket === 'HK' ? '代码或名称，如 00700 或 腾讯' : positionSearchMarket === 'US' ? '代码或名称，如 LI 或 理想汽车' : positionSearchMarket === 'CN' ? '代码或名称，如 600519 或 茅台' : '代码或名称，如 600519 / 00700 / AAPL'}
                    className="pl-9"
                    autoComplete="off"
                  />
                  {positionSearching && <span className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />}
                  {showPositionDropdown && positionSearchResults.length > 0 && (
                    <div className="absolute z-50 w-full mt-1 max-h-48 overflow-auto scrollbar card shadow-lg">
                      {positionSearchResults.map(item => (
                        <button
                          key={`${item.market}-${item.symbol}`}
                          type="button"
                          onClick={() => selectPositionStock(item)}
                          className="w-full flex items-center gap-2 px-3 py-2 text-[13px] hover:bg-accent/50 text-left transition-colors"
                        >
                          <span className={`text-[9px] px-1 py-0.5 rounded ${marketBadge(item.market).style}`}>
                            {marketBadge(item.market).label}
                          </span>
                          <span className="font-mono text-muted-foreground text-[12px]">{item.symbol}</span>
                          <span className="flex-1 text-foreground">{item.name}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                {positionForm.stock_symbol && (
                  <div className="mt-2 flex items-center gap-2">
                    <span className={`text-[9px] px-1.5 py-0.5 rounded ${marketBadge(positionForm.stock_market).style}`}>
                      {marketBadge(positionForm.stock_market).label}
                    </span>
                    <span className="font-mono text-[12px] text-muted-foreground">{positionForm.stock_symbol}</span>
                    <span className="text-[13px] text-foreground">{positionForm.stock_name}</span>
                    <button
                      type="button"
                      onClick={() => {
                        setPositionForm({ ...positionForm, stock_id: 0, stock_symbol: '', stock_name: '', stock_market: '' })
                        setPositionSearchQuery('')
                      }}
                      className="ml-1 text-muted-foreground hover:text-destructive"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                )}
              </div>
            )}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label>成本价</Label>
                <Input
                  value={positionForm.cost_price}
                  onChange={e => setPositionForm({ ...positionForm, cost_price: e.target.value })}
                  placeholder="0.00"
                  className="font-mono"
                  inputMode="decimal"
                />
              </div>
              <div>
                <Label>持仓数量</Label>
                <Input
                  value={positionForm.quantity}
                  onChange={e => setPositionForm({ ...positionForm, quantity: e.target.value })}
                  placeholder="0"
                  className="font-mono"
                  inputMode="numeric"
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label>投入资金 <span className="text-muted-foreground/60 text-[11px]">(选填)</span></Label>
                <Input
                  value={positionForm.invested_amount}
                  onChange={e => setPositionForm({ ...positionForm, invested_amount: e.target.value })}
                  placeholder="选填"
                  className="font-mono"
                  inputMode="decimal"
                />
              </div>
              <div>
                <Label>交易风格 <span className="text-muted-foreground font-normal">(选填)</span></Label>
                <Select
                  value={positionForm.trading_style}
                  onValueChange={val => setPositionForm({ ...positionForm, trading_style: val === '__none__' ? '' : val })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="不设置" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">不设置</SelectItem>
                    <SelectItem value="short">短线 (1-5天)</SelectItem>
                    <SelectItem value="swing">波段 (1-4周)</SelectItem>
                    <SelectItem value="long">长线 (数月)</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setPositionDialogOpen(false)}>取消</Button>
              <Button
                onClick={handlePositionSubmit}
                disabled={!positionForm.cost_price || !positionForm.quantity || (!editPositionId && !positionForm.stock_id && !positionForm.stock_symbol)}
              >
                {editPositionId ? '保存' : '添加'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Agent Assignment Dialog */}
      <Dialog open={!!agentDialogStock} onOpenChange={open => !open && setAgentDialogStock(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>配置监控 Agent</DialogTitle>
            <DialogDescription>
              为 {agentDialogStock?.name}（{agentDialogStock?.symbol}）选择要监控的 Agent
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 mt-2">
            {agents.length === 0 ? (
              <p className="text-[13px] text-muted-foreground py-4 text-center">暂无可用 Agent</p>
            ) : (
              agents.map(agent => {
                const stockAgent = agentDialogStock?.agents?.find(a => a.agent_name === agent.name)
                const isAssigned = !!stockAgent
                const isBatchMode = agent.execution_mode === 'batch'
                return (
                  <div key={agent.name} className="rounded-xl bg-accent/30 hover:bg-accent/50 transition-colors overflow-hidden">
                    <div className="flex items-center justify-between p-3.5">
                      <div className="flex items-center gap-3">
                        <div className={`w-2 h-2 rounded-full ${agent.enabled ? 'bg-emerald-500' : 'bg-border'}`} />
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="text-[13px] font-medium text-foreground">{agent.display_name}</span>
                            <Badge variant="secondary" className="text-[9px]">
                              {isBatchMode ? '批量' : '逐只'}
                            </Badge>
                          </div>
                          <p className="text-[11px] text-muted-foreground mt-0.5">{agent.description}</p>
                        </div>
                      </div>
                      <Switch
                        checked={isAssigned}
                        onCheckedChange={() => agentDialogStock && toggleAgent(agentDialogStock, agent.name)}
                        disabled={!agent.enabled}
                      />
                    </div>
                    {isAssigned && isBatchMode && (
                      <div className="px-3.5 pb-3.5 pt-0">
                        <p className="text-[11px] text-muted-foreground">
                          调度、AI模型、通知渠道请在 <a href="/agents" className="text-primary hover:underline">Agent 配置</a> 页面统一设置
                        </p>
                      </div>
                    )}
                    {isAssigned && !isBatchMode && (
                      <div className="px-3.5 pb-3.5 pt-0 space-y-2.5">
                        {/* Schedule/Interval Select */}
                        <div className="flex items-center gap-2">
                          <Clock className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
                          <Select
                            value={stockAgent?.schedule || '__default__'}
                            onValueChange={val => agentDialogStock && updateStockAgentSchedule(agentDialogStock, agent.name, val === '__default__' ? '' : val)}
                          >
                            <SelectTrigger className="h-7 text-[11px] w-auto min-w-[140px] px-2.5 bg-accent/50 border-border/50">
                              <SelectValue placeholder="执行间隔" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="__default__">跟随全局</SelectItem>
                              <SelectItem value="*/1 9-15 * * 1-5">每 1 分钟</SelectItem>
                              <SelectItem value="*/3 9-15 * * 1-5">每 3 分钟</SelectItem>
                              <SelectItem value="*/5 9-15 * * 1-5">每 5 分钟</SelectItem>
                              <SelectItem value="*/10 9-15 * * 1-5">每 10 分钟</SelectItem>
                              <SelectItem value="*/15 9-15 * * 1-5">每 15 分钟</SelectItem>
                              <SelectItem value="*/30 9-15 * * 1-5">每 30 分钟</SelectItem>
                            </SelectContent>
                          </Select>
                          <span className="text-[10px] text-muted-foreground">交易时段</span>
                        </div>

                        {/* Schedule Preview */}
                        {(() => {
                          const eff = effectiveSchedule(agent, stockAgent)
                          const isFollowingGlobal = !(stockAgent?.schedule || '').trim() && !!(agent.schedule || '').trim()
                          const preview = eff ? schedulePreviewCache[eff] : null
                          const isLoading = eff ? !!schedulePreviewLoading[eff] : false
                          if (!eff) return null
                          return (
                            <div className="ml-[22px] rounded-lg border border-border/40 bg-background/30 px-2.5 py-2">
                              <div className="flex items-center justify-between">
                                <div className="text-[11px] text-muted-foreground">
                                  未来触发时间预览{isFollowingGlobal ? <span className="ml-1 opacity-70">(跟随全局)</span> : null}
                                </div>
                                {isLoading && (
                                  <span className="w-3 h-3 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
                                )}
                              </div>
                              {'error' in (preview || {}) ? (
                                <div className="mt-1 text-[11px] text-muted-foreground">{(preview as any).error}</div>
                              ) : (preview as SchedulePreview | undefined)?.next_runs?.length ? (
                                <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                                  {(preview as SchedulePreview).next_runs.map((t, i) => (
                                    <span key={i} className="px-1.5 py-0.5 rounded border border-border/60 bg-accent/20 font-mono" title={t}>
                                      {formatPreviewTime(t, (preview as SchedulePreview).timezone)}
                                    </span>
                                  ))}
                                  {(preview as SchedulePreview).timezone ? (
                                    <span className="opacity-60">({(preview as SchedulePreview).timezone})</span>
                                  ) : null}
                                </div>
                              ) : (
                                <div className="mt-1 text-[11px] text-muted-foreground">—</div>
                              )}
                              <div className="mt-1 text-[10px] text-muted-foreground/70 font-mono">schedule: {eff}</div>
                            </div>
                          )
                        })()}

                        {/* AI Model Select */}
                        <div className="flex items-center gap-2">
                          <Cpu className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
                          <Select
                            value={stockAgent?.ai_model_id?.toString() ?? '__default__'}
                            onValueChange={val => agentDialogStock && updateStockAgentModel(agentDialogStock, agent.name, val === '__default__' ? null : parseInt(val))}
                          >
                            <SelectTrigger className="h-7 text-[11px] w-auto min-w-[140px] px-2.5 bg-accent/50 border-border/50">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="__default__">系统默认</SelectItem>
                              {services.map(svc => (
                                <SelectGroup key={svc.id}>
                                  <SelectLabel>{svc.name}</SelectLabel>
                                  {svc.models.map(m => (
                                    <SelectItem key={m.id} value={m.id.toString()}>
                                      {m.name}{m.name !== m.model ? ` (${m.model})` : ''}
                                    </SelectItem>
                                  ))}
                                </SelectGroup>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                        {/* Notification Channels */}
                        {channels.length > 0 && (
                          <div className="flex items-center gap-2 flex-wrap">
                            <Bell className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
                            {channels.map(ch => {
                              const isSelected = (stockAgent?.notify_channel_ids || []).includes(ch.id)
                              return (
                                <button
                                  key={ch.id}
                                  onClick={() => agentDialogStock && toggleStockAgentChannel(agentDialogStock, agent.name, ch.id)}
                                  className={`text-[10px] px-2 py-0.5 rounded-md border transition-colors ${
                                    isSelected
                                      ? 'bg-primary/10 border-primary/30 text-primary font-medium'
                                      : 'bg-accent/30 border-border/50 text-muted-foreground hover:border-primary/30'
                                  }`}
                                >
                                  {ch.name}
                                </button>
                              )
                            })}
                            {(stockAgent?.notify_channel_ids || []).length === 0 && (
                              <span className="text-[10px] text-muted-foreground">系统默认</span>
                            )}
                          </div>
                        )}
                        {/* Trigger Button */}
                        <div className="flex items-center gap-2 pt-1">
                          <Button
                            variant="secondary" size="sm" className="h-7 text-[11px] px-2.5"
                            disabled={triggeringAgent === agent.name}
                            onClick={() => agentDialogStock && triggerStockAgent(agentDialogStock.id, agent.name)}
                          >
                            {triggeringAgent === agent.name ? (
                              <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                            ) : (
                              <Play className="w-3 h-3" />
                            )}
                            立即分析
                          </Button>
                        </div>
                      </div>
                    )}
                  </div>
                )
              })
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Agent 分析结果弹窗 */}
      <Dialog open={!!agentResultDialog} onOpenChange={open => !open && setAgentResultDialog(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base">{agentResultDialog?.title}</DialogTitle>
            <DialogDescription className="flex items-center gap-2 pt-1">
              {agentResultDialog?.should_alert ? (
                <Badge variant="default" className="text-[10px]">建议关注</Badge>
              ) : (
                <Badge variant="secondary" className="text-[10px]">无需关注</Badge>
              )}
              {agentResultDialog?.notified && (
                <Badge variant="outline" className="text-[10px]">已发送通知</Badge>
              )}
            </DialogDescription>
          </DialogHeader>
          <div className="mt-2 p-3 bg-accent/30 rounded-lg">
            <pre className="text-[13px] whitespace-pre-wrap font-sans leading-relaxed">
              {agentResultDialog?.content}
            </pre>
          </div>
          <div className="flex justify-end mt-2">
            <Button variant="outline" size="sm" onClick={() => setAgentResultDialog(null)}>
              关闭
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 相关资讯弹窗 */}
      <Dialog open={newsDialogOpen} onOpenChange={setNewsDialogOpen}>
        <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Newspaper className="w-5 h-5 text-blue-500" />
              相关资讯
            </DialogTitle>
            <DialogDescription>
              {newsDialogSymbol
                ? `${newsDialogSymbol} 的相关新闻和公告`
                : '自选股相关新闻和公告（近 72 小时）'
              }
            </DialogDescription>
          </DialogHeader>

          {/* 股票筛选器 */}
          <div className="flex items-center gap-2 flex-wrap py-2 border-b">
            <span className="text-[12px] text-muted-foreground">筛选:</span>
            <button
              onClick={() => { setNewsDialogSymbol(''); loadNews() }}
              className={`text-[11px] px-2.5 py-1 rounded-md transition-colors ${
                !newsDialogSymbol
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-accent/50 text-muted-foreground hover:bg-accent'
              }`}
            >
              全部
            </button>
            {stocks.slice(0, 10).map(stock => (
              <button
                key={stock.symbol}
                onClick={() => { setNewsDialogSymbol(stock.name); loadNews(stock.name) }}
                className={`text-[11px] px-2.5 py-1 rounded-md transition-colors ${
                  newsDialogSymbol === stock.name
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-accent/50 text-muted-foreground hover:bg-accent'
                }`}
              >
                {stock.name}
              </button>
            ))}
            {stocks.length > 10 && (
              <span className="text-[10px] text-muted-foreground">+{stocks.length - 10}</span>
            )}
          </div>

          {/* 新闻列表 */}
          <div className="flex-1 overflow-y-auto min-h-0 py-2">
            {newsLoading ? (
              <div className="flex items-center justify-center py-12">
                <span className="w-5 h-5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
                <span className="ml-2 text-[13px] text-muted-foreground">加载中...</span>
              </div>
            ) : news.length === 0 ? (
              <div className="text-center py-12 text-muted-foreground text-[13px]">
                暂无相关资讯
              </div>
            ) : (
              <div className="space-y-2">
                {news.map((item, idx) => (
                  <div
                    key={`${item.source}-${item.external_id}-${idx}`}
                    className="p-3 rounded-lg bg-accent/30 hover:bg-accent/50 transition-colors"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1.5">
                          <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                            item.source === 'eastmoney' ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400' :
                            item.source === 'eastmoney_news' ? 'bg-blue-500/10 text-blue-500' :
                            'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                          }`}>
                            {item.source_label}
                          </span>
                          {item.importance >= 2 && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-rose-500/10 text-rose-500">
                              重要
                            </span>
                          )}
                          <span className="text-[10px] text-muted-foreground">
                            {item.publish_time}
                          </span>
                        </div>
                        <a
                          href={item.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-[13px] font-medium text-foreground hover:text-primary transition-colors block"
                        >
                          {item.title}
                        </a>
                        {item.symbols.length > 0 && (
                          <div className="flex items-center gap-1.5 mt-2">
                            {item.symbols.slice(0, 5).map(sym => {
                              const stockInfo = stocks.find(s => s.symbol === sym)
                              const stockName = stockInfo?.name || sym
                              return (
                                <button
                                  key={sym}
                                  onClick={() => { setNewsDialogSymbol(stockName); loadNews(stockName) }}
                                  className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary font-mono hover:bg-primary/20 transition-colors"
                                >
                                  {stockName}
                                </button>
                              )
                            })}
                            {item.symbols.length > 5 && (
                              <span className="text-[10px] text-muted-foreground">+{item.symbols.length - 5}</span>
                            )}
                          </div>
                        )}
                      </div>
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex-shrink-0 p-1.5 rounded-md hover:bg-accent transition-colors"
                        title="查看原文"
                      >
                        <ExternalLink className="w-4 h-4 text-muted-foreground" />
                      </a>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* 底部刷新按钮 */}
          <div className="flex items-center justify-between pt-2 border-t">
            <span className="text-[11px] text-muted-foreground">
              共 {news.length} 条资讯
            </span>
            <Button variant="secondary" size="sm" onClick={() => loadNews(newsDialogSymbol || undefined)} disabled={newsLoading}>
              <RefreshCw className={`w-3 h-3 ${newsLoading ? 'animate-spin' : ''}`} />
              刷新
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
