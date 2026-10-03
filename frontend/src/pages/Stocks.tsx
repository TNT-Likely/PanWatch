import { useConfirm } from '@panwatch/base-ui/components/ui/confirm-dialog'
import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Plus, Trash2, Pencil, Search, X, TrendingUp, Bot, Play, RefreshCw, Wallet, PiggyBank, ArrowUpRight, ArrowDownRight, Building2, ChevronDown, ChevronRight, Cpu, Bell, Clock, Newspaper, ExternalLink, BarChart3, Brain } from 'lucide-react'
import { fetchAPI, stocksApi, type AIService, type NotifyChannel } from '@panwatch/api'
import { klinesApi } from '@panwatch/api/klines'
import { useLocalStorage } from '@/lib/utils'
import {
  buildPortfolioStockKeys,
  loadPortfolioPageBackgroundData,
  loadPortfolioPageCoreData,
  loadPortfolioPageQuoteData,
} from '@/lib/portfolio-page-data'
import { SuggestionBadge, type SuggestionInfo, type KlineSummary } from '@panwatch/biz-ui/components/suggestion-badge'
import { KlineSummaryDialog } from '@panwatch/biz-ui/components/kline-summary-dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Switch } from '@panwatch/base-ui/components/ui/switch'
import { Badge } from '@panwatch/base-ui/components/ui/badge'
import { Skeleton } from '@panwatch/base-ui/components/ui/skeleton'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectGroup, SelectLabel, SelectItem } from '@panwatch/base-ui/components/ui/select'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import StockInsightModal from '@panwatch/biz-ui/components/stock-insight-modal'
import { DeepAnalysisModal } from '@panwatch/biz-ui/components/deep-analysis-modal'
import StockPriceAlertPanel from '@panwatch/biz-ui/components/stock-price-alert-panel'
import { useTranslation } from 'react-i18next'
import { localizeAgentDescription, localizeAgentName } from '@/i18n/agent-labels'
import { getCurrentLocale } from '@/i18n'
import { applyMarketStatuses, mergePortfolioQuotes, toQuoteMap, type Position, type PortfolioSummary, type QuoteResponse, type DisplayQuote } from '@/lib/portfolio-quotes'
import { MarketCalendarStatus, type MarketStatus } from '@/components/MarketCalendarStatus'
import { marketSignTextClass } from '@/lib/market-colors'
import { parseAssistantPortfolioTarget } from '@/lib/assistant-navigation'

interface AgentResult {
  success?: boolean
  message?: string
  title: string
  content: string
  should_alert: boolean
  notified: boolean
  skipped?: boolean
}

interface StockAgentInfo {
  agent_name: string
  display_name?: string
  schedule: string
  ai_model_id: number | null
  notify_channel_ids: number[]
}

interface Stock {
  id: number
  symbol: string
  name: string
  market: string
  sort_order?: number
  agents: StockAgentInfo[]
}

interface Account {
  id: number
  name: string
  available_funds: number
  enabled: boolean
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

// 建议池中的建议（包含来源和时间信息）
interface PoolSuggestion extends Omit<SuggestionInfo, 'should_alert'> {
  id: number
  stock_symbol: string
  stock_market?: string
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

interface PriceAlertRuleSummary {
  stock_symbol: string
  market: string
  enabled: boolean
}

const emptyStockForm: StockForm = { symbol: '', name: '', market: 'CN' }
const emptyAccountForm: AccountForm = { name: '', available_funds: '0' }

const buildQuoteItemsFrom = (stockList: Stock[], portfolio: PortfolioSummary | null): QuoteRequestItem[] => {
  const items: QuoteRequestItem[] = []
  const seen = new Set<string>()
  const add = (symbol: string, market: string) => {
    const key = `${market}:${symbol}`
    if (seen.has(key)) return
    seen.add(key)
    items.push({ symbol, market })
  }

  for (const stock of stockList) add(stock.symbol, stock.market)
  for (const account of portfolio?.accounts || []) {
    for (const pos of account.positions) add(pos.symbol, pos.market)
  }
  return items
}


const toPriceAlertSummaryMap = (rows: PriceAlertRuleSummary[]): Record<string, { total: number; enabled: number }> => {
  const map: Record<string, { total: number; enabled: number }> = {}
  for (const row of rows || []) {
    const key = `${String(row.market || 'CN').toUpperCase()}:${String(row.stock_symbol || '').toUpperCase()}`
    if (!map[key]) map[key] = { total: 0, enabled: 0 }
    map[key].total += 1
    if (row.enabled) map[key].enabled += 1
  }
  return map
}


export default function StocksPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const { t } = useTranslation('configuration')
  const confirmAction = useConfirm()
  const stockT = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const agentName = (name: string, fallback?: string) => localizeAgentName(name, fallback, stockT)
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
  const [quotes, setQuotes] = useState<Record<string, DisplayQuote>>({})
  const [quotesLoading, setQuotesLoading] = useState(false)
  // Keyed by `${market}:${symbol}` to avoid cross-market symbol collisions
  const [klineSummaries, setKlineSummaries] = useState<Record<string, KlineSummary>>({})

  // Auto-refresh (持久化到 localStorage)
  const [autoRefresh, setAutoRefresh] = useLocalStorage('panwatch_stocks_autoRefresh', false)
  const [refreshInterval, setRefreshInterval] = useLocalStorage('panwatch_stocks_refreshInterval', 30)
  const [lastRefreshTime, setLastRefreshTime] = useState<Date | null>(null)
  const refreshTimerRef = useRef<ReturnType<typeof setInterval>>()

  type ViewTab = 'positions' | 'watchlist'
  const [viewTab, setViewTab] = useLocalStorage<ViewTab>('panwatch_stocks_viewTab', 'positions')

  // 建议池建议（来自 /suggestions API）
  const [poolSuggestions, setPoolSuggestions] = useState<Record<string, PoolSuggestion>>({})
  const [priceAlertSummaryMap, setPriceAlertSummaryMap] = useState<Record<string, { total: number; enabled: number }>>({})

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
  const marketStatusRef = useRef<MarketStatus[]>([])
  const previousMarketStatusRef = useRef<MarketStatus[]>([])
  marketStatusRef.current = marketStatus
  // Guard to prevent overlapping K线刷新任务导致实际并发超限
  const klineRefreshInFlight = useRef<Promise<void> | null>(null)
  const initialLoadPromiseRef = useRef<Promise<void> | null>(null)
  const configLoadPromiseRef = useRef<Promise<void> | null>(null)
  const configLoadedRef = useRef(false)

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

  // 深度分析(TradingAgents)弹窗
  const [deepAnalysisTarget, setDeepAnalysisTarget] = useState<{
    stockId: number
    symbol: string
    name: string
  } | null>(null)
  const openDeepAnalysis = useCallback((stockId: number, symbol: string, name: string) => {
    setDeepAnalysisTarget({ stockId, symbol, name })
  }, [])
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
  const [draggingWatchStockId, setDraggingWatchStockId] = useState<number | null>(null)
  const [draggingPositionId, setDraggingPositionId] = useState<number | null>(null)
  const [draggingPositionAccountId, setDraggingPositionAccountId] = useState<number | null>(null)
  const watchDragSnapshotRef = useRef<Stock[] | null>(null)
  const positionDragSnapshotRef = useRef<PortfolioSummary | null>(null)

  const { toast } = useToast()

  const moveById = <T extends { id: number }>(list: T[], fromId: number, toId: number): T[] => {
    const fromIdx = list.findIndex(x => x.id === fromId)
    const toIdx = list.findIndex(x => x.id === toId)
    if (fromIdx < 0 || toIdx < 0 || fromIdx === toIdx) return list
    const next = [...list]
    const [moved] = next.splice(fromIdx, 1)
    next.splice(toIdx, 0, moved)
    return next
  }

  const persistWatchlistOrder = useCallback(async (ordered: Stock[]) => {
    const payload = ordered.map((s, idx) => ({ id: s.id, sort_order: idx + 1 }))
    await fetchAPI('/stocks/reorder', {
      method: 'PUT',
      body: JSON.stringify({ items: payload }),
    })
  }, [])

  const previewWatchlistReorder = useCallback((fromId: number, toId: number) => {
    if (fromId === toId) return
    setStocks(prev => {
      const ordered = [...prev].sort((a, b) => Number(a.sort_order || 0) - Number(b.sort_order || 0) || a.id - b.id)
      const moved = moveById(ordered, fromId, toId)
      return moved.map((s, idx) => ({ ...s, sort_order: idx + 1 }))
    })
  }, [])

  const commitWatchlistReorder = useCallback(async () => {
    const current = stocks
    if (!current || current.length === 0) return
    try {
      await persistWatchlistOrder(current)
    } catch (e) {
      if (watchDragSnapshotRef.current) setStocks(watchDragSnapshotRef.current)
      toast(e instanceof Error ? e.message : stockT('stocksPage.messages.saveWatchOrderFailed'), 'error')
    }
  }, [persistWatchlistOrder, stocks, toast])

  const persistPositionOrder = useCallback(async (ordered: Position[]) => {
    const payload = ordered.map((p, idx) => ({ id: p.id, sort_order: idx + 1 }))
    await fetchAPI('/positions/reorder/batch', {
      method: 'PUT',
      body: JSON.stringify({ items: payload }),
    })
  }, [])

  const previewPositionReorder = useCallback((accountId: number, fromId: number, toId: number) => {
    if (fromId === toId) return
    setPortfolioRaw(prev => {
      if (!prev) return prev
      const accountsNext = prev.accounts.map(acc => {
        if (acc.id !== accountId) return acc
        const moved = moveById(acc.positions || [], fromId, toId).map((p, idx) => ({ ...p, sort_order: idx + 1 }))
        return { ...acc, positions: moved }
      })
      return { ...prev, accounts: accountsNext }
    })
  }, [])

  const commitPositionReorder = useCallback(async (accountId: number) => {
    const acc = portfolioRaw?.accounts?.find(a => a.id === accountId)
    const ordered = acc?.positions || []
    if (!ordered.length) return
    try {
      await persistPositionOrder(ordered)
    } catch (e) {
      if (positionDragSnapshotRef.current) setPortfolioRaw(positionDragSnapshotRef.current)
      toast(e instanceof Error ? e.message : stockT('stocksPage.messages.savePositionOrderFailed'), 'error')
    }
  }, [persistPositionOrder, portfolioRaw, toast])

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

  const buildQuoteItems = useCallback((): QuoteRequestItem[] => {
    return buildQuoteItemsFrom(stocks, portfolioRaw)
  }, [stocks, portfolioRaw])

  const requestQuotes = useCallback(async (items: QuoteRequestItem[], signal?: AbortSignal): Promise<QuoteResponse[]> => {
    if (items.length === 0) return []
    try {
      return await fetchAPI<QuoteResponse[]>('/quotes/batch', {
        method: 'POST',
        body: JSON.stringify({ items }),
        signal,
      })
    } catch (e) {
      console.warn('刷新行情失败:', e)
      return []
    }
  }, [])

  const requestSuggestions = useCallback(async (items: QuoteRequestItem[], signal?: AbortSignal): Promise<Record<string, PoolSuggestion>> => {
    if (items.length === 0) return {}
    try {
      const params = new URLSearchParams({
        include_expired: 'true',
        stock_keys: buildPortfolioStockKeys(items),
      })
      return await fetchAPI<Record<string, PoolSuggestion>>(`/suggestions?${params.toString()}`, { signal })
    } catch (e) {
      console.warn('加载建议池失败:', e)
      return {}
    }
  }, [])

  const requestPriceAlerts = useCallback(async (items: QuoteRequestItem[], signal?: AbortSignal): Promise<PriceAlertRuleSummary[]> => {
    if (items.length === 0) return []
    try {
      return await fetchAPI<PriceAlertRuleSummary[]>('/price-alerts', { signal })
    } catch (e) {
      console.warn('加载提醒摘要失败:', e)
      return []
    }
  }, [])

  const requestKlineSummaries = useCallback(async (items: QuoteRequestItem[], signal?: AbortSignal): Promise<Record<string, KlineSummary>> => {
    if (items.length === 0) return {}
    try {
      const data = await klinesApi.summaryBatch(items, signal)
      const map: Record<string, KlineSummary> = {}
      for (const item of data || []) {
        if (item && item.summary && !('error' in item.summary)) {
          map[`${item.market}:${item.symbol}`] = item.summary as unknown as KlineSummary
        }
      }
      return map
    } catch {
      // 批量请求失败时保留旧摘要，避免技术徽章整体闪断。
      return {}
    }
  }, [])

  const loadNewStockSummary = useCallback(async (item: QuoteRequestItem) => {
    const map = await requestKlineSummaries([item])
    setKlineSummaries(prev => ({ ...prev, ...map }))
  }, [requestKlineSummaries])

  const refreshMarketStatus = useCallback(async (signal?: AbortSignal): Promise<MarketStatus[]> => {
    try {
      const data = await fetchAPI<MarketStatus[]>('/stocks/markets/status', { signal })
      if (!signal?.aborted) setMarketStatus(data)
      return data
    } catch (error) {
      if (!signal?.aborted) console.warn('获取市场状态失败:', error)
      return []
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    const refresh = () => { if (!document.hidden) void refreshMarketStatus(controller.signal) }
    const timer = window.setInterval(refresh, 60_000)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      controller.abort()
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [refreshMarketStatus])

  const refreshQuotes = useCallback(async (automatic = false) => {
    const statuses = marketStatusRef.current
    const items = buildQuoteItems().filter(item => !automatic ||
      !statuses.some(status => status.code === item.market) ||
      statuses.some(status => status.code === item.market && status.is_trading))
    if (items.length === 0) return

    setQuotesLoading(true)
    try {
      const data = await requestQuotes(items)
      if (data.length > 0) {
        setQuotes(previous => ({ ...previous, ...toQuoteMap(data) }))
        setLastRefreshTime(new Date())
      }
    } finally {
      setQuotesLoading(false)
    }
  }, [buildQuoteItems, requestQuotes])

  useEffect(() => {
    if (!portfolioRaw) return
    setPortfolio(mergePortfolioQuotes(portfolioRaw, applyMarketStatuses(quotes, marketStatus)))
  }, [portfolioRaw, quotes, marketStatus])

  // 刷新 K 线摘要（批量接口）；并防止重入
  const refreshKlines = useCallback(async () => {
    if (klineRefreshInFlight.current) return klineRefreshInFlight.current
    const run = (async () => {
      const items = buildQuoteItems()
      if (items.length === 0) return
      const map = await requestKlineSummaries(items)
      // 增量合并：本轮单只失败时保留旧值，避免技术徽章闪断/消失
      setKlineSummaries(prev => ({ ...prev, ...map }))
    })()
    klineRefreshInFlight.current = run
    try { await run } finally { klineRefreshInFlight.current = null }
  }, [buildQuoteItems, requestKlineSummaries])

  // Fetch the closing snapshot once, then stop polling closed-market quotes.
  useEffect(() => {
    const previous = previousMarketStatusRef.current
    previousMarketStatusRef.current = marketStatus
    if (autoRefresh && marketStatus.some(market => market.status === 'after_hours' &&
      previous.some(old => old.code === market.code && old.is_trading))) void refreshQuotes()
  }, [marketStatus, autoRefresh, refreshQuotes])

  // 从建议池加载建议（包含历史建议和多来源建议）
  const loadPoolSuggestions = useCallback(async (itemsOverride?: QuoteRequestItem[]) => {
    const data = await requestSuggestions(itemsOverride || buildQuoteItems())
    setPoolSuggestions(data)
  }, [buildQuoteItems, requestSuggestions])

  const loadPriceAlertSummaries = useCallback(async () => {
    const rows = await requestPriceAlerts(buildQuoteItems())
    setPriceAlertSummaryMap(toPriceAlertSummaryMap(rows))
  }, [buildQuoteItems, requestPriceAlerts])

  const loadConfigAsync = useCallback((signal?: AbortSignal): Promise<void> => {
    if (configLoadedRef.current) return Promise.resolve()
    if (configLoadPromiseRef.current) return configLoadPromiseRef.current

    const run = (async () => {
      try {
        const [agentData, servicesData, channelsData] = await Promise.all([
          fetchAPI<AgentConfig[]>('/agents', { signal }),
          fetchAPI<AIService[]>('/providers/services', { signal }),
          fetchAPI<NotifyChannel[]>('/channels', { signal }),
        ])
        setAgents(agentData)
        setServices(servicesData)
        setChannels(channelsData)
        configLoadedRef.current = true
      } catch (e) {
        console.warn('加载配置数据失败:', e)
      }
    })()
    configLoadPromiseRef.current = run
    void run.finally(() => {
      configLoadPromiseRef.current = null
    })
    return run
  }, [])

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const stockData = await fetchAPI<Stock[]>('/stocks', { signal })
      setStocks(stockData)
    } catch (e) {
      if (!signal?.aborted) console.error(e)
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [])

  const loadPortfolio = useCallback(async () => {
    try {
      // Mutation refresh is local metadata only; render using the quotes already on screen.
      const portfolioData = await fetchAPI<PortfolioSummary>('/portfolio/summary?include_quotes=false&refresh_exchange_rates=false')
      setPortfolioRaw(portfolioData)
      setAccounts(portfolioData.accounts.map(account => ({
        id: account.id,
        name: account.name,
        available_funds: account.available_funds,
        enabled: true,
      })))
      setExpandedAccounts(prev => new Set([...prev, ...portfolioData.accounts.map(account => account.id)]))
      setPortfolio(mergePortfolioQuotes(portfolioData, quotes))

      // New symbols may need a quote; cost/quantity/account edits need no market-data fetch.
      const missing = buildQuoteItemsFrom(stocks, portfolioData).filter(item => !quotes[`${item.market}:${item.symbol}`])
      if (missing.length > 0) {
        void requestQuotes(missing).then(rows => {
          setQuotes(prev => ({ ...prev, ...toQuoteMap(rows) }))
        })
      }
    } catch (e) {
      console.error(e)
    }
  }, [requestQuotes, quotes, stocks])

  const loadInitialData = useCallback((signal: AbortSignal): Promise<void> => {
    if (initialLoadPromiseRef.current) return initialLoadPromiseRef.current
    setLoading(true)
    setPortfolioLoading(true)

    const run = (async () => {
      const coreData = await loadPortfolioPageCoreData({
        loadStocks: requestSignal => fetchAPI<Stock[]>('/stocks', { signal: requestSignal }),
        loadPortfolio: requestSignal => fetchAPI<PortfolioSummary>('/portfolio/summary?include_quotes=false&refresh_exchange_rates=false', { signal: requestSignal }),
      }, signal)

      if (signal.aborted) return

      // Publish local metadata immediately; market-data lanes update independently.
      const quoteMap: Record<string, QuoteResponse> = {}
      setStocks(coreData.stocks)
      setPortfolioRaw(coreData.portfolio)
      setQuotes(quoteMap)
      setKlineSummaries({})
      setPoolSuggestions({})
      setPriceAlertSummaryMap({})
      setPortfolio(mergePortfolioQuotes(coreData.portfolio, quoteMap))
      const nextAccounts = coreData.portfolio.accounts.map(account => ({
        id: account.id,
        name: account.name,
        available_funds: account.available_funds,
        enabled: true,
      }))
      setAccounts(nextAccounts)
      setExpandedAccounts(new Set(nextAccounts.map(account => account.id)))
      setLoading(false)
      setPortfolioLoading(false)

      void loadPortfolioPageQuoteData({
        buildQuoteItems: buildQuoteItemsFrom,
        loadQuotes: requestQuotes,
      }, coreData.stocks, coreData.portfolio, signal).then(data => {
        if (signal.aborted) return
        setQuotes(toQuoteMap(data.quotes))
        if (data.quotes.length > 0) setLastRefreshTime(new Date())
      }).catch(error => {
        if (!signal.aborted) console.warn('Portfolio quotes unavailable:', error)
      })

      // FX refresh also runs independently of the metadata shell and quotes.
      void fetchAPI<PortfolioSummary>('/portfolio/summary?include_quotes=false', { signal }).then(data => {
        if (!signal.aborted) setPortfolioRaw(previous => previous ? { ...previous, exchange_rates: data.exchange_rates } : previous)
      }).catch(error => {
        if (!signal.aborted) console.warn('Portfolio exchange rates unavailable:', error)
      })

      void loadPortfolioPageBackgroundData({
        loadMarketStatus: refreshMarketStatus,
        buildQuoteItems: buildQuoteItemsFrom,
        loadSuggestions: requestSuggestions,
        loadPriceAlerts: requestPriceAlerts,
        loadKlines: requestKlineSummaries,
      }, coreData.stocks, coreData.portfolio, signal, {
        klines: data => setKlineSummaries(prev => ({ ...prev, ...data })),
        suggestions: setPoolSuggestions,
        priceAlerts: data => setPriceAlertSummaryMap(toPriceAlertSummaryMap(data)),
      }).catch(error => {
        if (!signal.aborted) console.warn('加载持仓页后台数据失败:', error)
      })
    })().catch(error => {
      if (!signal.aborted) console.error('加载持仓页面数据失败:', error)
    }).finally(() => {
      if (!signal.aborted) { setLoading(false); setPortfolioLoading(false) }
      initialLoadPromiseRef.current = null
    })

    initialLoadPromiseRef.current = run
    return run
  }, [refreshMarketStatus, requestKlineSummaries, requestPriceAlerts, requestQuotes, requestSuggestions])

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

  const handledAssistantTargetRef = useRef<string | null>(null)
  useEffect(() => {
    const target = parseAssistantPortfolioTarget(searchParams)
    if (!target) {
      handledAssistantTargetRef.current = null
      return
    }
    const key = `${target.market}:${target.symbol}`
    if (handledAssistantTargetRef.current === key) return
    handledAssistantTargetRef.current = key
    openKlineDialog(target.symbol, target.market)
    const next = new URLSearchParams(searchParams)
    next.delete('view')
    next.delete('symbol')
    next.delete('market')
    setSearchParams(next, { replace: true })
  }, [openKlineDialog, searchParams, setSearchParams])

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
      return d.toLocaleString(getCurrentLocale(), {
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
    void refreshKlines()
    await Promise.all([
      refreshMarketStatus(),
      refreshQuotes(),
      loadPoolSuggestions(),
      loadPriceAlertSummaries(),
    ])
  }, [loadPoolSuggestions, loadPriceAlertSummaries, refreshKlines, refreshMarketStatus, refreshQuotes])

  useEffect(() => {
    const controller = new AbortController()
    void loadInitialData(controller.signal)
    return () => {
      controller.abort()
      initialLoadPromiseRef.current = null
    }
  }, [loadInitialData])

  useEffect(() => {
    if (agentDialogStock) void loadConfigAsync()
  }, [agentDialogStock, loadConfigAsync])

  // Agent 配置弹窗：预览未来触发时间（用于自检工作日/周末语义）
  useEffect(() => {
    if (!agentDialogStock) return
    if (!agents || agents.length === 0) return

    const stockAgentMap = new Map((agentDialogStock.agents || []).map(a => [a.agent_name, a]))
    const schedules = new Map<string, { schedule: string; agent: string }>()
    for (const agent of agents) {
      if (agent.execution_mode === 'batch') continue
      const sa = stockAgentMap.get(agent.name)
      if (!sa) continue
      const eff = effectiveSchedule(agent, sa)
      if (eff) schedules.set(`${agent.name}|${agentDialogStock?.market}|${eff}`, { schedule: eff, agent: agent.name })
    }

    const toFetch = Array.from(schedules.keys()).filter(s => !schedulePreviewCache[s] && !schedulePreviewLoading[s])
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
            const plan = schedules.get(s)!
            const p = await fetchAPI<SchedulePreview>(`/agents/schedule/preview?schedule=${encodeURIComponent(plan.schedule)}&agent_name=${encodeURIComponent(plan.agent)}&market=${agentDialogStock.market}&count=5`)
            return [s, p] as const
          } catch (e) {
            const msg = e instanceof Error ? e.message : stockT('stocksPage.messages.previewFailed')
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

  // Auto-refresh timer
  useEffect(() => {
    if (autoRefresh) {
      refreshQuotes(true)
      refreshTimerRef.current = setInterval(() => {
        refreshQuotes(true)
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
  }, [autoRefresh, refreshInterval, refreshQuotes])

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
      toast(stockT('stocksPage.messages.refreshListDone', { count: result.count }), 'success')
      if (searchQuery) {
        doSearch(searchQuery)
      }
    } catch (e) {
      toast(stockT('stocksPage.messages.refreshFailed'), 'error')
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
    try {
      const created = await stocksApi.create(stockForm)
      const item = { symbol: created.symbol, market: created.market }
      void requestQuotes([item]).then(rows => setQuotes(prev => ({ ...prev, ...toQuoteMap(rows) })))
      void loadNewStockSummary(item)
      setStockForm(emptyStockForm)
      setSearchQuery('')
      setShowStockForm(false)
      load()
      toast(stockT('stocksPage.messages.stockAdded'), 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : stockT('stocksPage.messages.addStockFailed'), 'error')
    }
  }

  const hasAnyPositionForStockId = (id: number): boolean => {
    return (portfolio?.accounts || []).some(acc => (acc.positions || []).some(p => p.stock_id === id))
  }

  const removeFromWatchlist = async (stock: Stock) => {
    if (hasAnyPositionForStockId(stock.id)) {
      toast(stockT('stocksPage.messages.stockHasPosition'), 'error')
      return
    }

    setRemovingWatchStock(true)
    try {
      await stocksApi.remove(stock.id)
      toast(stockT('stocksPage.messages.stockDeleted'), 'success')
      setRemoveWatchStock(null)
      load()
      // 价格提醒/关联配置会随股票删除，刷新一次避免 UI 残留。
      loadPortfolio()
    } catch (e) {
      toast(e instanceof Error ? e.message : stockT('stocksPage.messages.deleteFailed'), 'error')
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
    try {
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
      toast(editAccountId ? stockT('stocksPage.messages.accountUpdated') : stockT('stocksPage.messages.accountCreated'), 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : stockT('stocksPage.messages.saveAccountFailed'), 'error')
    }
  }

  const handleDeleteAccount = async (id: number) => {
    if (!(await confirmAction(stockT('stocksPage.messages.deleteAccountConfirm'), { destructive: true }))) return
    try {
      await fetchAPI(`/accounts/${id}`, { method: 'DELETE' })
      load()
      loadPortfolio()
      toast(stockT('stocksPage.messages.accountDeleted'), 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : stockT('stocksPage.messages.deleteAccountFailed'), 'error')
    }
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
    try {
      let stockId = positionForm.stock_id
      let createdStock: QuoteRequestItem | undefined

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
          createdStock = { symbol: newStock.symbol, market: newStock.market }
          load() // 刷新股票列表
        } catch {
          // 股票可能已存在，尝试获取（兼容并发创建/历史数据）。
          try {
            const existingStocks = await fetchAPI<Stock[]>('/stocks')
            const existing = existingStocks.find(s => s.symbol === positionForm.stock_symbol && s.market === positionForm.stock_market)
            if (existing) {
              stockId = existing.id
            } else {
              toast(stockT('stocksPage.messages.addStockFailed'), 'error')
              return
            }
          } catch (e) {
            toast(e instanceof Error ? e.message : stockT('stocksPage.messages.addStockFailed'), 'error')
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
      if (createdStock) void loadNewStockSummary(createdStock)
      loadPortfolio()
      toast(editPositionId ? stockT('stocksPage.messages.positionUpdated') : stockT('stocksPage.messages.positionAdded'), 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : stockT('stocksPage.messages.savePositionFailed'), 'error')
    }
  }

  const handleDeletePosition = async (id: number) => {
    if (!(await confirmAction(stockT('stocksPage.messages.deletePositionConfirm'), { destructive: true }))) return
    try {
      await fetchAPI(`/positions/${id}`, { method: 'DELETE' })
      loadPortfolio()
      toast(stockT('stocksPage.messages.positionDeleted'), 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : stockT('stocksPage.messages.deletePositionFailed'), 'error')
    }
  }

  // ========== Agent handlers ==========
  const toggleAgent = async (stock: Stock, agentName: string) => {
    try {
      const current = stock.agents || []
      const isAssigned = current.some(a => a.agent_name === agentName)
      const newAgents = isAssigned
        ? current.filter(a => a.agent_name !== agentName)
        : [...current, { agent_name: agentName, schedule: '', ai_model_id: null, notify_channel_ids: [] }]
      await fetchAPI(`/stocks/${stock.id}/agents`, { method: 'PUT', body: JSON.stringify({ agents: newAgents }) })
      load()
      setAgentDialogStock(prev => prev ? { ...prev, agents: newAgents } : null)
    } catch (e) {
      toast(e instanceof Error ? e.message : stockT('stocksPage.messages.agentBindingFailed'), 'error')
    }
  }

  const triggerStockAgent = async (stockId: number, agentName: string) => {
    setTriggeringAgent(agentName)
    setRunningAgents(prev => ({ ...prev, [stockId]: agentName }))
    // 触发后立即关闭配置弹窗，避免多层弹窗干扰
    setAgentDialogStock(null)
    try {
      // 手动触发时跳过节流，方便测试
      const resp = await fetchAPI<{ result: AgentResult; success?: boolean; message?: string }>(
        `/stocks/${stockId}/agents/${agentName}/trigger?bypass_throttle=true`,
        { method: 'POST' }
      )
      const result = resp?.result
      if (result) {
        // 仅提示，不再弹出结果弹窗，避免干扰
        if (result.success === false) {
          toast(result.message || result.content || stockT('stocksPage.messages.notExecuted'), 'info')
          return
        }
        const isSkipped = !!result.skipped || /已跳过执行|非交易时段/.test(result.content || '')
        if (isSkipped) {
          toast(result.content || stockT('stocksPage.messages.nonTradingSkipped'), 'info')
        } else {
          toast(result.should_alert ? stockT('stocksPage.messages.suggested') : stockT('stocksPage.messages.noNeedAttention'), result.should_alert ? 'success' : 'info')
        }
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : stockT('stocksPage.messages.triggerFailed')
      if (/非交易时段|跳过执行/.test(msg)) {
        toast(msg, 'info')
      } else {
        toast(msg, 'error')
      }
    } finally {
      setTriggeringAgent(null)
      setRunningAgents(prev => ({ ...prev, [stockId]: null }))
    }
  }

  const updateStockAgentModel = async (stock: Stock, agentName: string, modelId: number | null) => {
    try {
      const newAgents = (stock.agents || []).map(a =>
        a.agent_name === agentName ? { ...a, ai_model_id: modelId } : a
      )
      await fetchAPI(`/stocks/${stock.id}/agents`, { method: 'PUT', body: JSON.stringify({ agents: newAgents }) })
      load()
      setAgentDialogStock(prev => prev ? { ...prev, agents: newAgents } : null)
    } catch (e) {
      toast(e instanceof Error ? e.message : stockT('stocksPage.messages.agentModelFailed'), 'error')
    }
  }

  const toggleStockAgentChannel = async (stock: Stock, agentName: string, channelId: number) => {
    try {
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
    } catch (e) {
      toast(e instanceof Error ? e.message : stockT('stocksPage.messages.agentChannelFailed'), 'error')
    }
  }

  const updateStockAgentSchedule = async (stock: Stock, agentName: string, schedule: string) => {
    try {
      const newAgents = (stock.agents || []).map(a =>
        a.agent_name === agentName ? { ...a, schedule } : a
      )
      await fetchAPI(`/stocks/${stock.id}/agents`, { method: 'PUT', body: JSON.stringify({ agents: newAgents }) })
      load()
      setAgentDialogStock(prev => prev ? { ...prev, agents: newAgents } : null)
    } catch (e) {
      toast(e instanceof Error ? e.message : stockT('stocksPage.messages.agentScheduleFailed'), 'error')
    }
  }

  // ========== Helpers ==========
  const formatMoney = (value: number) => {
    if (Math.abs(value) >= 10000) {
      return `${(value / 10000).toFixed(2)}${stockT('stocksPage.largeUnit')}`
    }
    return value.toFixed(2)
  }

  const marketLabel = (m: string) => m === 'CN' ? stockT('stocksPage.markets.cn') : m === 'HK' ? stockT('stocksPage.markets.hk') : m === 'US' ? stockT('stocksPage.markets.us') : m
  const quoteHint = (quote: { daily_move_status?: string; quote_date?: string | null }) => {
    const status = quote.daily_move_status
    const label = status && status !== 'current' ? stockT(`stocksPage.quoteStatus.${status}`) : ''
    const date = quote.quote_date ? stockT('stocksPage.quoteStatus.asOf', { date: quote.quote_date }) : ''
    return [label, date].filter(Boolean).join(' · ')
  }

  // 市场徽章样式和短标签
  const marketBadge = (m: string) => {
    if (m === 'HK') return { style: 'bg-orange-500/10 text-orange-600', label: stockT('stocksPage.markets.hkShort') }
    if (m === 'US') return { style: 'bg-green-500/10 text-green-600', label: stockT('stocksPage.markets.usShort') }
    return { style: 'bg-blue-500/10 text-blue-600', label: 'A' }
  }

  // 保留原始精度显示价格（不强制截断小数位）
  const formatPrice = (value: number) => {
    // 最多显示4位小数，去除末尾的0
    const formatted = value.toFixed(4).replace(/\.?0+$/, '')
    return formatted
  }

  // 获取股票的行情信息
  const getStockQuote = (quoteKey: string) => {
    return quotes[quoteKey] || null
  }

  const getPriceAlertSummary = (symbol: string, market: string) => {
    const key = `${String(market || 'CN').toUpperCase()}:${String(symbol || '').toUpperCase()}`
    return priceAlertSummaryMap[key] || { total: 0, enabled: 0 }
  }

  // 获取股票的建议信息（优先使用建议池，包含来源和时间信息）
  const getSuggestionForStock = (symbol: string, market: string): { suggestion: SuggestionInfo | null; kline: KlineSummary | null } => {
    const key = `${market || 'CN'}:${symbol}`
    // 优先使用建议池的建议（包含来源和时间信息）
    const poolSug =
      poolSuggestions[key] ||
      (() => {
        const fallback = poolSuggestions[symbol]
        if (!fallback) return null
        const fm = String(fallback.stock_market || '').toUpperCase()
        return fm && fm !== String(market || 'CN').toUpperCase() ? null : fallback
      })()
    if (poolSug) {
      const preloadedKline = klineSummaries[key] || null
      return {
        suggestion: {
          ...poolSug,
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

    const ks = klineSummaries[key]
    if (ks) return { suggestion: null, kline: ks }

    return { suggestion: null, kline: null }
  }

  const unpricedAccountIds = new Set(
    (portfolio?.accounts || []).filter(account => account.positions.some(position => position.current_price == null)).map(account => account.id),
  )
  const portfolioTotalsReady = unpricedAccountIds.size === 0

  const positionRatio = useMemo(() => {
    if (!portfolio || !portfolioTotalsReady) return null
    const mv = portfolio.total.total_market_value || 0
    const assets = portfolio.total.total_assets || 0
    const pct = assets > 0 ? (mv / assets * 100) : 0
    return { mv, assets, pct }
  }, [portfolio, portfolioTotalsReady])

  const positionsCount = useMemo(() => {
    return (portfolio?.accounts || []).reduce((acc, a) => acc + (a.positions?.length || 0), 0)
  }, [portfolio])

  const watchlistCount = useMemo(() => {
    return stocks.length
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
      <div className="flex flex-col gap-2 md:gap-3 mb-5 md:mb-6">
        <div className="flex items-center justify-between gap-2">
          <h1 className="text-[18px] md:text-[22px] font-bold text-foreground tracking-tight shrink-0">{stockT('stocksPage.messages.portfolio')}</h1>
          {/* Desktop buttons + controls */}
          <div className="hidden md:flex items-center gap-3">
            {/* Controls */}
            <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-accent/30">
              <div className="flex items-center gap-1.5">
                <Switch checked={autoRefresh} onCheckedChange={setAutoRefresh} className="scale-90" />
                <span className="text-[11px] text-muted-foreground">{stockT('stocksPage.autoRefresh')}</span>
                {autoRefresh && (
                  <Select value={refreshInterval.toString()} onValueChange={v => setRefreshInterval(parseInt(v))}>
                    <SelectTrigger className="h-6 w-14 text-[10px] px-1.5">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="10">10s</SelectItem>
                      <SelectItem value="30">30s</SelectItem>
                      <SelectItem value="60">{stockT('stocksPage.messages.everyMinutes', { minutes: 1 })}</SelectItem>
                      <SelectItem value="120">{stockT('stocksPage.messages.everyMinutes', { minutes: 2 })}</SelectItem>
                    </SelectContent>
                  </Select>
                )}
              </div>
              {lastRefreshTime && (
                <>
                  <div className="w-px h-4 bg-border" />
                  <span className="text-[10px] text-muted-foreground/60">
                    {lastRefreshTime.toLocaleTimeString(getCurrentLocale(), { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                  </span>
                </>
              )}
            </div>
            {/* Buttons */}
            <Button variant="secondary" onClick={handleRefresh} disabled={quotesLoading}>
              <RefreshCw className={`w-4 h-4 ${quotesLoading ? 'animate-spin' : ''}`} />
              {stockT('stocksPage.messages.refreshed')}
            </Button>
            <Button variant="secondary" onClick={() => openAccountDialog()}>
              <Building2 className="w-4 h-4" /> {stockT('stocksPage.messages.addAccount')}
            </Button>
            <Button onClick={() => { setStockForm(emptyStockForm); setSearchQuery(''); setShowStockForm(true) }}>
              <Plus className="w-4 h-4" /> {stockT('stocksPage.messages.addStock')}
            </Button>
          </div>
          {/* Mobile buttons */}
          <div className="flex md:hidden items-center gap-1.5">
            <Button variant="secondary" size="sm" className="h-8 w-8 p-0" onClick={handleRefresh} disabled={quotesLoading}>
              <RefreshCw className={`w-4 h-4 ${quotesLoading ? 'animate-spin' : ''}`} />
            </Button>
            <Button variant="secondary" size="sm" className="h-8 w-8 p-0" onClick={() => openAccountDialog()}>
              <Building2 className="w-4 h-4" />
            </Button>
            <Button size="sm" className="h-8 w-8 p-0" onClick={() => { setStockForm(emptyStockForm); setSearchQuery(''); setShowStockForm(true) }}>
              <Plus className="w-4 h-4" />
            </Button>
          </div>
        </div>

        {/* 移动端 row 2：市场状态 + 自动刷新 + 时间戳合并到同一行,横向滚动避免换行；桌面端只展示市场 pills (auto-refresh 在桌面顶部已展示) */}
        <div className="flex items-center gap-1.5 overflow-x-auto scrollbar-none -mx-1 px-1 md:flex-wrap md:overflow-visible">
          {marketStatus.length > 0 && <MarketCalendarStatus markets={marketStatus} labelMarket={marketLabel} />}
          {/* 移动端紧凑型自动刷新控件 */}
          <div className="flex md:hidden shrink-0 items-center gap-1 px-2 py-0.5 rounded-full bg-accent/30 ml-1">
            <Switch checked={autoRefresh} onCheckedChange={setAutoRefresh} className="scale-75" />
            {autoRefresh ? (
              <Select value={refreshInterval.toString()} onValueChange={v => setRefreshInterval(parseInt(v))}>
                <SelectTrigger className="h-5 w-12 text-[10px] px-1 border-0 bg-transparent">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="10">10s</SelectItem>
                  <SelectItem value="30">30s</SelectItem>
                  <SelectItem value="60">{stockT('stocksPage.messages.everyMinutes', { minutes: 1 })}</SelectItem>
                  <SelectItem value="120">{stockT('stocksPage.messages.everyMinutes', { minutes: 2 })}</SelectItem>
                </SelectContent>
              </Select>
            ) : (
              <span className="text-[10px] text-muted-foreground">{stockT('stocksPage.autoRefresh')}</span>
            )}
          </div>
          {lastRefreshTime && (
            <span className="md:hidden shrink-0 text-[10px] text-muted-foreground/60 font-mono ml-1">
              {lastRefreshTime.toLocaleTimeString(getCurrentLocale(), { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
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
              <span className="text-[12px]">{stockT('stocksPage.messages.totalMarketValue')}</span>
            </div>
            <div className="text-[20px] font-bold text-foreground font-mono">
              {portfolioTotalsReady ? formatMoney(portfolio.total.total_market_value) : '--'}
            </div>
          </div>
          <div className="card p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              {portfolio.total.total_pnl >= 0 ? (
                <ArrowUpRight className={`w-4 h-4 ${marketSignTextClass(portfolioTotalsReady ? portfolio.total.total_pnl : null)}`} />
              ) : (
                <ArrowDownRight className={`w-4 h-4 ${marketSignTextClass(portfolioTotalsReady ? portfolio.total.total_pnl : null)}`} />
              )}
              <span className="text-[12px]">{stockT('stocksPage.messages.totalPnl')}</span>
            </div>
            <div className={`text-[20px] font-bold font-mono ${marketSignTextClass(portfolioTotalsReady ? portfolio.total.total_pnl : null)}`}>
              {portfolioTotalsReady ? `${portfolio.total.total_pnl >= 0 ? '+' : ''}${formatMoney(portfolio.total.total_pnl)}` : '--'}
              {portfolioTotalsReady && <span className="block text-[13px] md:inline md:ml-1.5">
                ({portfolio.total.total_pnl_pct >= 0 ? '+' : ''}{portfolio.total.total_pnl_pct.toFixed(2)}%)
              </span>}
            </div>
          </div>

          {(() => {
            const dayPnl = portfolio.total.total_daily_pnl
            const totalMv = portfolio.total.total_market_value
            const prevMv = totalMv - dayPnl
            const pct = prevMv > 0 ? (dayPnl / prevMv * 100) : 0
            const isUp = dayPnl >= 0
            return (
              <div className="card p-4">
                <div className="flex items-center gap-2 text-muted-foreground mb-1">
                  {isUp ? (
                    <ArrowUpRight className={`w-4 h-4 ${marketSignTextClass(portfolioTotalsReady ? dayPnl : null)}`} />
                  ) : (
                    <ArrowDownRight className={`w-4 h-4 ${marketSignTextClass(portfolioTotalsReady ? dayPnl : null)}`} />
                  )}
                  <span className="text-[12px]">{stockT('stocksPage.messages.todayPnl')}</span>
                </div>
                <div className={`text-[20px] font-bold font-mono ${marketSignTextClass(portfolioTotalsReady ? dayPnl : null)}`}>
                  {portfolioTotalsReady ? `${isUp ? '+' : ''}${formatMoney(dayPnl)}` : '--'}
                  {portfolioTotalsReady && <span className="block text-[13px] md:inline md:ml-1.5">({pct >= 0 ? '+' : ''}{pct.toFixed(2)}%)</span>}
                </div>
              </div>
            )
          })()}

          <div className="card p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Wallet className="w-4 h-4" />
              <span className="text-[12px]">{stockT('stocksPage.messages.availableFundsShort')}</span>
            </div>
            <div className="text-[20px] font-bold text-foreground font-mono">
              {formatMoney(portfolio.total.available_funds)}
            </div>
          </div>
          <div className="card p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <PiggyBank className="w-4 h-4" />
              <span className="text-[12px]">{stockT('stocksPage.messages.totalAssets')}</span>
            </div>
            <div className="text-[20px] font-bold text-foreground font-mono">
              {portfolioTotalsReady ? formatMoney(portfolio.total.total_assets) : '--'}
            </div>
          </div>

          <div className="card p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Bell className="w-4 h-4" />
              <span className="text-[12px]">{stockT('stocksPage.messages.positionRatio')}</span>
            </div>
            <div className="text-[20px] font-bold text-foreground font-mono">
              {positionRatio ? `${positionRatio.pct.toFixed(1)}%` : '--'}
            </div>
            <div className="mt-1 text-[11px] text-muted-foreground line-clamp-1">
              {positionRatio ? stockT('stocksPage.messages.positionValueSummary', { value: formatMoney(positionRatio.mv), assets: formatMoney(positionRatio.assets) }) : stockT('stocksPage.messages.empty')}
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
            {stockT('stocksPage.messages.portfolio')} <span className="ml-1 font-mono text-[11px] opacity-70">{positionsCount}</span>
          </button>
          <button
            onClick={() => setViewTab('watchlist')}
            className={`px-3 py-1.5 rounded-md text-[12px] transition-colors ${
              viewTab === 'watchlist'
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {stockT('stocksPage.messages.watch')} <span className="ml-1 font-mono text-[11px] opacity-70">{watchlistCount}</span>
          </button>
        </div>
      </div>

      {/* Add Stock Dialog */}
      <Dialog open={showStockForm} onOpenChange={(open) => { setShowStockForm(open); if (!open) { setSearchQuery(''); setSearchMarket('') } }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{stockT('stocksPage.messages.addToWatchlistTitle')}</DialogTitle>
            <DialogDescription>{stockT('stocksPage.messages.addToWatchlistDescription')}</DialogDescription>
          </DialogHeader>
          <form onSubmit={handleStockSubmit}>
            <div className="relative" ref={dropdownRef}>
              <div className="flex items-center gap-2 mb-2">
                <Label className="mb-0">{stockT('stocksPage.messages.searchStock')}</Label>
                <div className="flex items-center gap-1">
                  {[
                    { value: '', label: stockT('stocksPage.markets.all') },
                    { value: 'CN', label: stockT('stocksPage.markets.cn') },
                    { value: 'HK', label: stockT('stocksPage.markets.hk') },
                    { value: 'US', label: stockT('stocksPage.markets.us') },
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
                  title={stockT('stocksPage.messages.searchRefreshHint')}
                >
                  {refreshingStockList ? (
                    <span className="flex items-center gap-1">
                      <RefreshCw className="w-3 h-3 animate-spin" /> {stockT('stocksPage.messages.refreshing')}
                    </span>
                  ) : (
                    <span className="flex items-center gap-1">
                      <RefreshCw className="w-3 h-3" /> {stockT('stocksPage.messages.refreshList')}
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
                  placeholder={stockT('stocksPage.messages.searchPlaceholder', { example: searchMarket === 'HK' ? '00700 or Tencent' : searchMarket === 'US' ? 'AAPL or Apple' : '600519 or Kweichow Moutai' })}
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
              <Button type="button" variant="ghost" onClick={() => { setShowStockForm(false); setSearchQuery('') }}>{stockT('stocksPage.messages.cancel')}</Button>
              <Button type="submit" disabled={!stockForm.symbol}>{stockT('stocksPage.messages.confirmAdd')}</Button>
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
            <p className="text-[15px] font-semibold text-foreground">{stockT('stocksPage.messages.noAccount')}</p>
            <p className="text-[13px] text-muted-foreground mt-1.5">{stockT('stocksPage.messages.createFirstAccount')}</p>
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
                    {stockT('stocksPage.messages.positionCount', { count: account.positions.length })}
                  </span>
                </div>
                <div className="flex items-center justify-between md:justify-end gap-2 md:gap-6 pl-6 md:pl-0">
                  <div className="flex items-center gap-2.5 md:gap-6 min-w-0">
                    <div className="text-left md:text-right">
                      <div className="text-[10px] md:text-[11px] text-muted-foreground">{stockT('stocksPage.messages.marketValue')}</div>
                      <div className="text-[12px] md:text-[13px] font-mono font-medium whitespace-nowrap">{unpricedAccountIds.has(account.id) ? '--' : formatMoney(account.total_market_value)}</div>
                    </div>
                    <div className="text-left md:text-right">
                      <div className="text-[10px] md:text-[11px] text-muted-foreground">{stockT('stocksPage.messages.pnl')}</div>
                      <div className={`text-[12px] md:text-[13px] font-mono font-medium whitespace-nowrap ${marketSignTextClass(unpricedAccountIds.has(account.id) ? null : account.total_pnl)}`}>
                        {unpricedAccountIds.has(account.id) ? '--' : `${account.total_pnl >= 0 ? '+' : ''}${formatMoney(account.total_pnl)}`}
                        {!unpricedAccountIds.has(account.id) && <span className="text-[10px] md:text-[11px] ml-1 hidden md:inline">({account.total_pnl_pct >= 0 ? '+' : ''}{account.total_pnl_pct.toFixed(2)}%)</span>}
                      </div>
                    </div>
                    <div className="text-left md:text-right">
                      <div className="text-[10px] md:text-[11px] text-muted-foreground">{stockT('stocksPage.messages.today')}</div>
                      <div className={`text-[12px] md:text-[13px] font-mono font-medium whitespace-nowrap ${marketSignTextClass(unpricedAccountIds.has(account.id) ? null : account.total_daily_pnl)}`}>
                        {unpricedAccountIds.has(account.id) ? '--' : `${account.total_daily_pnl >= 0 ? '+' : ''}${formatMoney(account.total_daily_pnl)}`}
                      </div>
                    </div>
                    <div className="text-left md:text-right hidden sm:block">
                      <div className="text-[10px] md:text-[11px] text-muted-foreground">{stockT('stocksPage.messages.availableFundsShort')}</div>
                      <div className="text-[12px] md:text-[13px] font-mono whitespace-nowrap">{formatMoney(account.available_funds)}</div>
                    </div>
                  </div>
                  <div className="flex items-center gap-0 md:gap-1 shrink-0" onClick={e => e.stopPropagation()}>
                    <Button variant="ghost" size="icon" className="h-7 w-7 md:h-8 md:w-8" onClick={() => openPositionDialog(account.id)}>
                      <Plus className="w-3 md:w-3.5 h-3 md:h-3.5" />
                    </Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7 md:h-8 md:w-8" aria-label={stockT('stocksPage.messages.editAccount')} onClick={() => openAccountDialog(accounts.find(a => a.id === account.id))}>
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
                    <p className="text-[13px] text-muted-foreground text-center py-8">{stockT('stocksPage.messages.noPositions')}</p>
                  ) : (
                    <>
                      {/* Desktop Table */}
                      <div className="hidden md:block overflow-x-auto scrollbar">
                        <table className="w-full">
                          <thead>
                            <tr className="border-b border-border/30 bg-accent/20">
                              <th className="text-left px-4 py-2 text-[11px] font-semibold text-muted-foreground">{stockT('stocksPage.messages.stock')}</th>
                              <th className="text-right px-4 py-2 text-[11px] font-semibold text-muted-foreground">{stockT('stocksPage.messages.currentPrice')}</th>
                              <th className="text-right px-4 py-2 text-[11px] font-semibold text-muted-foreground">{stockT('stocksPage.messages.change')}</th>
                              <th className="text-right px-4 py-2 text-[11px] font-semibold text-muted-foreground">{stockT('stocksPage.messages.cost')}</th>
                              <th className="text-right px-4 py-2 text-[11px] font-semibold text-muted-foreground">{stockT('stocksPage.messages.holding')}</th>
                              <th className="text-right px-4 py-2 text-[11px] font-semibold text-muted-foreground">{stockT('stocksPage.messages.marketValue')}</th>
                              <th className="text-right px-4 py-2 text-[11px] font-semibold text-muted-foreground">{stockT('stocksPage.messages.pnl')}</th>
                              <th className="text-right px-4 py-2 text-[11px] font-semibold text-muted-foreground">{stockT('stocksPage.messages.today')}</th>
                              <th className="text-center px-4 py-2 text-[11px] font-semibold text-muted-foreground">{stockT('stocksPage.messages.style')}</th>
                              <th className="text-left px-4 py-2 text-[11px] font-semibold text-muted-foreground">{stockT('stocksPage.messages.agent')}</th>
                              <th className="text-center px-4 py-2 text-[11px] font-semibold text-muted-foreground">{stockT('stocksPage.messages.actions')}</th>
                            </tr>
                          </thead>
                          <tbody>
                            {account.positions.map((pos, i) => {
                              const stock = stocks.find(s => s.id === pos.stock_id)
                              const badge = marketBadge(pos.market)
                              const isForeign = pos.market === 'HK' || pos.market === 'US'
                              const changeColor = marketSignTextClass(pos.change_pct)
                              const pnlColor = marketSignTextClass(pos.pnl)
                              return (
                                <tr
                                  key={pos.id}
                                  draggable
                                  onDragStart={(e) => {
                                    positionDragSnapshotRef.current = portfolioRaw ? JSON.parse(JSON.stringify(portfolioRaw)) : null
                                    setDraggingPositionId(pos.id)
                                    setDraggingPositionAccountId(account.id)
                                    e.dataTransfer.effectAllowed = 'move'
                                  }}
                                  onDragOver={(e) => {
                                    e.preventDefault()
                                    e.dataTransfer.dropEffect = 'move'
                                    if (draggingPositionId != null && draggingPositionAccountId === account.id) {
                                      previewPositionReorder(account.id, draggingPositionId, pos.id)
                                    }
                                  }}
                                  onDrop={(e) => {
                                    e.preventDefault()
                                    if (draggingPositionId != null && draggingPositionAccountId === account.id) {
                                      commitPositionReorder(account.id)
                                    }
                                    setDraggingPositionId(null)
                                    setDraggingPositionAccountId(null)
                                    positionDragSnapshotRef.current = null
                                  }}
                                  onDragEnd={() => {
                                    setDraggingPositionId(null)
                                    setDraggingPositionAccountId(null)
                                    positionDragSnapshotRef.current = null
                                  }}
                                  className={`group hover:bg-accent/30 transition-colors ${i > 0 ? 'border-t border-border/20' : ''} ${draggingPositionId === pos.id ? 'opacity-60' : ''}`}
                                >
                                  <td className="px-4 py-2.5">
                                    <span className={`text-[9px] px-1 py-0.5 rounded mr-1.5 ${badge.style}`}>{badge.label}</span>
                                    <span className="font-mono text-[12px] font-semibold text-foreground">
                                      {pos.symbol}
                                    </span>
                                    <button
                                      className="ml-1.5 text-[12px] text-muted-foreground hover:text-primary"
                                      onClick={() => openStockDetail(pos.symbol, pos.market, pos.name, true)}
                                    >
                                      {pos.name}
                                    </button>
                                    {(() => {
                                      const { suggestion, kline } = getSuggestionForStock(pos.symbol, pos.market)
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
                                  <td title={quoteHint(pos)} className={`px-4 py-2.5 text-right font-mono text-[12px] ${changeColor}`}>
                                    {pos.current_price != null ? <span>{pos.current_price.toFixed(2)}{isForeign ? (pos.market === 'HK' ? ' HKD' : ' USD') : ''}</span> : '—'}
                                  </td>
                                  <td title={quoteHint(pos)} className={`px-4 py-2.5 text-right font-mono text-[12px] ${changeColor}`}>
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
                                  <td className={`px-4 py-2.5 text-right font-mono text-[12px] ${marketSignTextClass(pos.daily_pnl)}`}>
                                    {pos.daily_pnl != null ? (
                                      <div className="flex flex-col items-end">
                                        <span>{pos.daily_pnl >= 0 ? '+' : ''}{formatMoney(pos.daily_pnl)}</span>
                                        <span className="text-[10px] opacity-70">{pos.daily_pnl_pct != null ? `${pos.daily_pnl_pct >= 0 ? '+' : ''}${pos.daily_pnl_pct.toFixed(2)}%` : ''}</span>
                                      </div>
                                    ) : '—'}
                                  </td>
                                  <td className="px-4 py-2.5 text-center">
                                    {pos.trading_style ? (
                                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${pos.trading_style === 'short' ? 'bg-rose-500/10 text-rose-600' : pos.trading_style === 'long' ? 'bg-blue-500/10 text-blue-600' : 'bg-amber-500/10 text-amber-600'}`}>
                                        {pos.trading_style === 'short' ? stockT('stocksPage.messages.shortStyle') : pos.trading_style === 'long' ? stockT('stocksPage.messages.longStyle') : stockT('stocksPage.messages.swingStyle')}
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
                                                  <Badge variant="default" className="text-[10px]">{agentName(sa.agent_name, sa.display_name || agent?.display_name)}</Badge>
                                                  {isRunning && (
                                                    <span className="inline-flex items-center gap-1 text-[10px] text-amber-600">
                                                      <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                                                      {stockT('stocksPage.messages.executing')}
                                                    </span>
                                                  )}
                                                </span>
                                              )
                                            })}
                                          </div>
                                        ) : (
                                          <span className="text-[11px] text-muted-foreground/50 flex items-center gap-1"><Bot className="w-3 h-3" /> {stockT('stocksPage.messages.notConfigured')}</span>
                                        )}
                                      </button>
                                    )}
                                  </td>
                                  <td className="px-4 py-2.5 text-center">
                                    <div className="flex items-center justify-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                      {(() => { const { suggestion, kline } = getSuggestionForStock(pos.symbol, pos.market); return (!suggestion && !kline) ? (
                                        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openKlineDialog(pos.symbol, pos.market, pos.name, true)} title={stockT('stocksPage.messages.klineIndicator')}><BarChart3 className="w-3 h-3" /></Button>
                                      ) : null })()}
                                      <StockPriceAlertPanel
                                        mode="icon"
                                        stockId={pos.stock_id}
                                        symbol={pos.symbol}
                                        market={pos.market}
                                        stockName={pos.name}
                                        initialTotal={getPriceAlertSummary(pos.symbol, pos.market).total}
                                        initialEnabled={getPriceAlertSummary(pos.symbol, pos.market).enabled}
                                        onChanged={loadPriceAlertSummaries}
                                      />
                                      <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openNewsDialog(pos.name)} title={stockT('stocksPage.messages.relatedNews')}><Newspaper className="w-3 h-3" /></Button>
                                      <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-primary" title={stockT('stocksPage.messages.deepAnalysis')} onClick={() => openDeepAnalysis(pos.stock_id, pos.symbol, pos.name)}><Brain className="w-3 h-3" /></Button>
                                      <Button variant="ghost" size="icon" className="h-7 w-7" aria-label={stockT('stocksPage.messages.editPosition')} onClick={() => openPositionDialog(account.id, pos)}><Pencil className="w-3 h-3" /></Button>
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
                          const changeColor = marketSignTextClass(pos.change_pct)
                          const pnlColor = marketSignTextClass(pos.pnl)
                          return (
                            <div
                              key={pos.id}
                              draggable
                              onDragStart={(e) => {
                                positionDragSnapshotRef.current = portfolioRaw ? JSON.parse(JSON.stringify(portfolioRaw)) : null
                                setDraggingPositionId(pos.id)
                                setDraggingPositionAccountId(account.id)
                                e.dataTransfer.effectAllowed = 'move'
                              }}
                              onDragOver={(e) => {
                                e.preventDefault()
                                e.dataTransfer.dropEffect = 'move'
                                if (draggingPositionId != null && draggingPositionAccountId === account.id) {
                                  previewPositionReorder(account.id, draggingPositionId, pos.id)
                                }
                              }}
                              onDrop={(e) => {
                                e.preventDefault()
                                if (draggingPositionId != null && draggingPositionAccountId === account.id) {
                                  commitPositionReorder(account.id)
                                }
                                setDraggingPositionId(null)
                                setDraggingPositionAccountId(null)
                                positionDragSnapshotRef.current = null
                              }}
                              onDragEnd={() => {
                                setDraggingPositionId(null)
                                setDraggingPositionAccountId(null)
                                positionDragSnapshotRef.current = null
                              }}
                              className={`p-3 hover:bg-accent/30 transition-colors ${draggingPositionId === pos.id ? 'opacity-60' : ''}`}
                            >
                              {/* Row 1: Stock info + Current price */}
                              <div className="flex items-center justify-between gap-2 mb-2">
                                <div className="flex items-center gap-1.5 min-w-0">
                                  <span className={`shrink-0 text-[9px] px-1 py-0.5 rounded ${badge.style}`}>{badge.label}</span>
                                  <span className="shrink-0 font-mono text-[12px] font-semibold text-foreground">
                                    {pos.symbol}
                                  </span>
                                  <button
                                    className="text-[12px] text-muted-foreground hover:text-primary truncate"
                                    onClick={() => openStockDetail(pos.symbol, pos.market, pos.name, true)}
                                  >
                                    {pos.name}
                                  </button>
                                  {pos.trading_style && (
                                    <span className={`shrink-0 text-[9px] px-1 py-0.5 rounded ${pos.trading_style === 'short' ? 'bg-rose-500/10 text-rose-600' : pos.trading_style === 'long' ? 'bg-blue-500/10 text-blue-600' : 'bg-amber-500/10 text-amber-600'}`}>
                                      {pos.trading_style === 'short' ? stockT('stocksPage.messages.shortStyleLabel') : pos.trading_style === 'long' ? stockT('stocksPage.messages.longStyleLabel') : stockT('stocksPage.messages.swingStyleLabel')}
                                    </span>
                                  )}
                                </div>
                                <div title={quoteHint(pos)} className={`font-mono text-[13px] font-medium whitespace-nowrap shrink-0 ${changeColor}`}>
                                  {pos.current_price?.toFixed(2) || '—'}
                                  <span className="text-[11px] ml-1">{pos.change_pct != null ? `${pos.change_pct >= 0 ? '+' : ''}${pos.change_pct.toFixed(2)}%` : '—'}</span>
                                </div>
                              </div>
                              {/* Row 2 (Suggestion badge, dedicated row to avoid wrapping mess) */}
                              {(() => {
                                const { suggestion, kline } = getSuggestionForStock(pos.symbol, pos.market)
                                return (suggestion || kline) ? (
                                  <div className="mb-2">
                                    <SuggestionBadge
                                      suggestion={suggestion}
                                      stockName={pos.name}
                                      stockSymbol={pos.symbol}
                                      kline={kline}
                                      market={pos.market}
                                      hasPosition={true}
                                    />
                                  </div>
                                ) : null
                              })()}
                              {/* Row 3: Stats grid (4 cols, whitespace-nowrap to prevent "万" wrapping) */}
                              <div className="grid grid-cols-4 gap-2 text-[11px]">
                                <div className="min-w-0">
                                  <div className="text-[10px] text-muted-foreground">{stockT('stocksPage.messages.cost')}</div>
                                  <div className="font-mono text-foreground truncate" title={String(pos.cost_price)}>{formatPrice(pos.cost_price)}</div>
                                </div>
                                <div className="min-w-0">
                                  <div className="text-[10px] text-muted-foreground">{stockT('stocksPage.messages.quantity')}</div>
                                  <div className="font-mono text-foreground truncate" title={String(pos.quantity)}>{pos.quantity}</div>
                                </div>
                                <div className="min-w-0">
                                  <div className="text-[10px] text-muted-foreground">{stockT('stocksPage.messages.pnl')}</div>
                                  <div className={`font-mono whitespace-nowrap ${pnlColor}`}>
                                    {pos.pnl != null ? `${pos.pnl >= 0 ? '+' : ''}${formatMoney(pos.pnl)}` : '—'}
                                  </div>
                                  {pos.pnl_pct != null && (
                                    <div className={`text-[10px] font-mono ${pnlColor} opacity-80`}>
                                      {pos.pnl_pct >= 0 ? '+' : ''}{pos.pnl_pct.toFixed(2)}%
                                    </div>
                                  )}
                                </div>
                                <div className="min-w-0">
                                  <div className="text-[10px] text-muted-foreground">{stockT('stocksPage.messages.today')}</div>
                                  <div className={`font-mono whitespace-nowrap ${marketSignTextClass(pos.daily_pnl)}`}>
                                    {pos.daily_pnl != null ? `${pos.daily_pnl >= 0 ? '+' : ''}${formatMoney(pos.daily_pnl)}` : '—'}
                                  </div>
                                </div>
                              </div>
                              {/* Row 4: Actions */}
                              <div className="flex items-center justify-between mt-2 pt-2 border-t border-border/20">
                                <div>
                                  {stock && stock.agents && stock.agents.length > 0 ? (
                                    <button onClick={() => setAgentDialogStock(stock)} className="flex items-center gap-1">
                                      {stock.agents.slice(0, 2).map(sa => {
                                        const agent = agents.find(a => a.name === sa.agent_name)
                                        const isRunning = runningAgents[stock.id] === sa.agent_name
                                        return (
                                          <span key={sa.agent_name} className="inline-flex items-center gap-1">
                                          <Badge variant="secondary" className="text-[9px]">{agentName(sa.agent_name, sa.display_name || agent?.display_name)}</Badge>
                                            {isRunning && (
                                              <span className="inline-flex items-center gap-1 text-[10px] text-amber-600">
                                                <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                                                {stockT('stocksPage.messages.executing')}
                                              </span>
                                            )}
                                          </span>
                                        )
                                      })}
                                    </button>
                                  ) : (
                                    <button onClick={() => stock && setAgentDialogStock(stock)} className="text-[10px] text-muted-foreground/50 flex items-center gap-1">
                                      <Bot className="w-3 h-3" /> {stockT('stocksPage.messages.agent')}
                                    </button>
                                  )}
                                </div>
                                <div className="flex items-center gap-1">
                                  {(() => { const { suggestion, kline } = getSuggestionForStock(pos.symbol, pos.market); return (!suggestion && !kline) ? (
                                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openKlineDialog(pos.symbol, pos.market, pos.name, true)} title={stockT('stocksPage.messages.klineIndicator')}><BarChart3 className="w-3 h-3" /></Button>
                                  ) : null })()}
                                  <StockPriceAlertPanel
                                    mode="icon"
                                    stockId={pos.stock_id}
                                    symbol={pos.symbol}
                                    market={pos.market}
                                    stockName={pos.name}
                                    initialTotal={getPriceAlertSummary(pos.symbol, pos.market).total}
                                    initialEnabled={getPriceAlertSummary(pos.symbol, pos.market).enabled}
                                    onChanged={loadPriceAlertSummaries}
                                  />
                                  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openNewsDialog(pos.name)}><Newspaper className="w-3 h-3" /></Button>
                                  <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-primary" title={stockT('stocksPage.messages.deepAnalysis')} onClick={() => openDeepAnalysis(pos.stock_id, pos.symbol, pos.name)}><Brain className="w-3 h-3" /></Button>
                                  <Button variant="ghost" size="icon" className="h-7 w-7" aria-label={stockT('stocksPage.messages.editPosition')} onClick={() => openPositionDialog(account.id, pos)}><Pencil className="w-3 h-3" /></Button>
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
            <h3 className="text-[13px] font-semibold text-foreground">{stockT('stocksPage.messages.watchlist')}</h3>
            <div className="flex items-center gap-1">
              {[
                { value: '', label: stockT('stocksPage.markets.all'), count: stocks.length },
                { value: 'CN', label: stockT('stocksPage.markets.cn'), count: stocks.filter(s => s.market === 'CN').length },
                { value: 'HK', label: stockT('stocksPage.markets.hk'), count: stocks.filter(s => s.market === 'HK').length },
                { value: 'US', label: stockT('stocksPage.markets.us'), count: stocks.filter(s => s.market === 'US').length },
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
            <div className="text-[11px] text-muted-foreground">{stockT('stocksPage.messages.filter')}</div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setWatchlistOnlyAlerts(!watchlistOnlyAlerts)}
                className={`text-[11px] px-2.5 py-1 rounded-md border transition-colors ${
                  watchlistOnlyAlerts
                    ? 'bg-rose-500/10 border-rose-500/30 text-rose-600'
                    : 'bg-accent/30 border-border/50 text-muted-foreground hover:border-rose-500/30'
                }`}
              title={stockT('stocksPage.messages.onlyAlerts')}
              >
                {stockT('stocksPage.messages.onlyAlerts')}
              </button>
            </div>
          </div>
          {stocks.length === 0 ? (
            <div className="py-12 text-center">
              <div className="text-[13px] text-muted-foreground">{stockT('stocksPage.messages.noWatchlist')}</div>
              <div className="mt-2 text-[11px] text-muted-foreground/70">{stockT('stocksPage.messages.startAddWatchlist')}</div>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {stocks
                .filter(s => !stockListFilter || s.market === stockListFilter)
                .sort((a, b) => Number(a.sort_order || 0) - Number(b.sort_order || 0) || a.id - b.id)
                .filter(stock => {
                  if (!watchlistOnlyAlerts) return true
                  const { suggestion } = getSuggestionForStock(stock.symbol, stock.market)
                  return !!suggestion?.should_alert
                })
                .map((stock) => {
                const quote = getStockQuote(`${stock.market}:${stock.symbol}`)
                const changeColor = marketSignTextClass(quote?.change_pct)
                const { suggestion, kline } = getSuggestionForStock(stock.symbol, stock.market)
                return (
                  <div
                    key={stock.id}
                    draggable={stockListFilter === '' && !watchlistOnlyAlerts}
                    onDragStart={(e) => {
                      if (stockListFilter !== '' || watchlistOnlyAlerts) return
                      watchDragSnapshotRef.current = stocks
                      setDraggingWatchStockId(stock.id)
                      e.dataTransfer.effectAllowed = 'move'
                    }}
                    onDragOver={(e) => {
                      if (stockListFilter !== '' || watchlistOnlyAlerts) return
                      e.preventDefault()
                      e.dataTransfer.dropEffect = 'move'
                      if (draggingWatchStockId != null) {
                        previewWatchlistReorder(draggingWatchStockId, stock.id)
                      }
                    }}
                    onDrop={(e) => {
                      if (stockListFilter !== '' || watchlistOnlyAlerts) return
                      e.preventDefault()
                      if (draggingWatchStockId != null) commitWatchlistReorder()
                      setDraggingWatchStockId(null)
                      watchDragSnapshotRef.current = null
                    }}
                    onDragEnd={() => {
                      setDraggingWatchStockId(null)
                      watchDragSnapshotRef.current = null
                    }}
                    className={`group rounded-xl border border-border/40 bg-background/30 hover:bg-accent/20 transition-colors p-3 cursor-pointer ${draggingWatchStockId === stock.id ? 'opacity-60' : ''}`}
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
                        <div title={quoteHint(quote || {})} className={`font-mono text-[14px] font-bold leading-tight ${changeColor}`}>
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
                        <div className="text-[11px] text-muted-foreground/70 py-2">{stockT('stocksPage.messages.noAnalysis')}</div>
                      )}
                    </div>

                    <div className="mt-2 pt-2 border-t border-border/30 flex items-center justify-between gap-2">
                      <div className="flex items-center gap-1 flex-wrap">
                        {stock.agents && stock.agents.length > 0 ? (
                          <Badge variant="secondary" className="text-[10px]">{stockT('stocksPage.messages.agentCount', { count: stock.agents.length })}</Badge>
                        ) : (
                          <span className="text-[10px] text-muted-foreground/60">{stockT('stocksPage.messages.notConfigured')}</span>
                        )}
                        {runningAgents[stock.id] && (
                          <span className="inline-flex items-center gap-1 text-[10px] text-amber-600">
                            <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                            {agentName(runningAgents[stock.id] || '', agents.find(a => a.name === runningAgents[stock.id])?.display_name)}
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
                          title={stockT('stocksPage.messages.klineIndicator')}
                        >
                          <BarChart3 className="w-3.5 h-3.5" />
                        </Button>
                        <StockPriceAlertPanel
                          mode="icon"
                          stockId={stock.id}
                          symbol={stock.symbol}
                          market={stock.market}
                          stockName={stock.name}
                          initialTotal={getPriceAlertSummary(stock.symbol, stock.market).total}
                          initialEnabled={getPriceAlertSummary(stock.symbol, stock.market).enabled}
                          onChanged={loadPriceAlertSummaries}
                        />
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => openNewsDialog(stock.name)}
                          title={stockT('stocksPage.messages.relatedNews')}
                        >
                          <Newspaper className="w-3.5 h-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7 hover:text-primary"
                          title={stockT('stocksPage.messages.deepAnalysis')}
                          onClick={() => openDeepAnalysis(stock.id, stock.symbol, stock.name)}
                        >
                          <Brain className="w-3.5 h-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => openStockDetail(stock.symbol, stock.market, stock.name, false)}
                          title={stockT('stocksPage.messages.detail')}
                        >
                          <ExternalLink className="w-3.5 h-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7 hover:text-destructive"
                          onClick={() => setRemoveWatchStock(stock)}
                          title={stockT('stocksPage.messages.deleteStock')}
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
      />

      {/* TradingAgents 深度分析弹窗 */}
      {deepAnalysisTarget && (
        <DeepAnalysisModal
          open={!!deepAnalysisTarget}
          onOpenChange={(open) => { if (!open) setDeepAnalysisTarget(null) }}
          stockId={deepAnalysisTarget.stockId}
          stockSymbol={deepAnalysisTarget.symbol}
          stockName={deepAnalysisTarget.name}
        />
      )}

      {/* Remove Watchlist Dialog */}
      <Dialog open={!!removeWatchStock} onOpenChange={(open) => { if (!open) setRemoveWatchStock(null) }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{stockT('stocksPage.messages.deleteStock')}</DialogTitle>
            <DialogDescription>{stockT('stocksPage.messages.deleteStockDescription')}</DialogDescription>
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
                    ? stockT('stocksPage.messages.deleteStockWithPosition')
                    : stockT('stocksPage.messages.deleteStockWithoutPosition')}
                </div>
              </div>

              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setRemoveWatchStock(null)} disabled={removingWatchStock}>{stockT('stocksPage.messages.cancel')}</Button>
                <Button
                  variant="destructive"
                  onClick={() => removeFromWatchlist(removeWatchStock)}
                  disabled={removingWatchStock || hasAnyPositionForStockId(removeWatchStock.id)}
                >
                  {hasAnyPositionForStockId(removeWatchStock.id) ? stockT('stocksPage.messages.deleteStockFirst') : (removingWatchStock ? stockT('stocksPage.messages.processing') : stockT('stocksPage.messages.deleteStock'))}
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
            <DialogTitle>{editAccountId ? stockT('stocksPage.messages.editAccount') : stockT('stocksPage.messages.addAccountTitle')}</DialogTitle>
            <DialogDescription>{stockT('stocksPage.messages.accountInfo')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label>{stockT('stocksPage.messages.accountName')}</Label>
              <Input
                value={accountForm.name}
                onChange={e => setAccountForm({ ...accountForm, name: e.target.value })}
                placeholder={stockT('stocksPage.messages.accountName')}
              />
            </div>
            <div>
              <Label>{stockT('stocksPage.messages.availableFunds')}</Label>
              <Input
                value={accountForm.available_funds}
                onChange={e => setAccountForm({ ...accountForm, available_funds: e.target.value })}
                placeholder="0"
                className="font-mono"
                inputMode="decimal"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setAccountDialogOpen(false)}>{stockT('stocksPage.messages.cancel')}</Button>
              <Button onClick={handleAccountSubmit} disabled={!accountForm.name}>
                {editAccountId ? stockT('stocksPage.messages.save') : stockT('stocksPage.messages.create')}
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
            <DialogTitle>{editPositionId ? stockT('stocksPage.messages.editPosition') : stockT('stocksPage.messages.addPosition')}</DialogTitle>
            <DialogDescription>
              {accounts.find(a => a.id === positionDialogAccountId)?.name} {stockT('stocksPage.messages.positionInfo')}
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
                  <Label className="mb-0">{stockT('stocksPage.messages.searchStock')}</Label>
                  <div className="flex items-center gap-1">
                    {[
                      { value: '', label: stockT('stocksPage.markets.all') },
                      { value: 'CN', label: stockT('stocksPage.markets.cn') },
                      { value: 'HK', label: stockT('stocksPage.markets.hk') },
                      { value: 'US', label: stockT('stocksPage.markets.us') },
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
                    placeholder={stockT('stocksPage.messages.searchPlaceholder', { example: positionSearchMarket === 'HK' ? '00700 or Tencent' : positionSearchMarket === 'US' ? 'LI or Li Auto' : positionSearchMarket === 'CN' ? '600519 or Kweichow Moutai' : '600519 / 00700 / AAPL' })}
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
                <Label>{stockT('stocksPage.messages.costPrice')}</Label>
                <Input
                  value={positionForm.cost_price}
                  onChange={e => setPositionForm({ ...positionForm, cost_price: e.target.value })}
                  placeholder="0.00"
                  className="font-mono"
                  inputMode="decimal"
                />
              </div>
              <div>
                <Label>{stockT('stocksPage.messages.quantity')}</Label>
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
                <Label>{stockT('stocksPage.messages.investedAmount')} <span className="text-muted-foreground/60 text-[11px]">({stockT('stocksPage.messages.optional')})</span></Label>
                <Input
                  value={positionForm.invested_amount}
                  onChange={e => setPositionForm({ ...positionForm, invested_amount: e.target.value })}
                  placeholder={stockT('stocksPage.messages.optional')}
                  className="font-mono"
                  inputMode="decimal"
                />
              </div>
              <div>
                <Label>{stockT('stocksPage.messages.tradingStyle')} <span className="text-muted-foreground font-normal">({stockT('stocksPage.messages.optional')})</span></Label>
                <Select
                  value={positionForm.trading_style}
                  onValueChange={val => setPositionForm({ ...positionForm, trading_style: val === '__none__' ? '' : val })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder={stockT('stocksPage.messages.unset')} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">{stockT('stocksPage.messages.unset')}</SelectItem>
                    <SelectItem value="short">{stockT('stocksPage.messages.shortStyle')}</SelectItem>
                    <SelectItem value="swing">{stockT('stocksPage.messages.swingStyle')}</SelectItem>
                    <SelectItem value="long">{stockT('stocksPage.messages.longStyle')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setPositionDialogOpen(false)}>{stockT('stocksPage.messages.cancel')}</Button>
              <Button
                onClick={handlePositionSubmit}
                disabled={!positionForm.cost_price || !positionForm.quantity || (!editPositionId && !positionForm.stock_id && !positionForm.stock_symbol)}
              >
                {editPositionId ? stockT('stocksPage.messages.save') : stockT('stocksPage.messages.add')}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Agent Assignment Dialog */}
      <Dialog open={!!agentDialogStock} onOpenChange={open => !open && setAgentDialogStock(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{stockT('stocksPage.messages.configureAgent')}</DialogTitle>
            <DialogDescription>
              {agentDialogStock?.name} ({agentDialogStock?.symbol})
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 mt-2">
            {agents.length === 0 ? (
              <p className="text-[13px] text-muted-foreground py-4 text-center">{stockT('stocksPage.messages.noAvailableAgents')}</p>
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
                            <span className="text-[13px] font-medium text-foreground">{agentName(agent.name, agent.display_name)}</span>
                            <Badge variant="secondary" className="text-[9px]">
                              {isBatchMode ? stockT('stocksPage.messages.batch') : stockT('stocksPage.messages.single')}
                            </Badge>
                          </div>
                          <p className="text-[11px] text-muted-foreground mt-0.5">{localizeAgentDescription(agent.name, agent.description, stockT)}</p>
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
                          {stockT('stocksPage.messages.agentConfigHint')}
                          <a href="/agents" className="text-primary hover:underline">{stockT('stocksPage.messages.configureAgent')}</a>
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
                              <SelectValue placeholder={stockT('stocksPage.messages.executionInterval')} />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="__default__">{stockT('stocksPage.messages.followGlobal')}</SelectItem>
                              <SelectItem value="*/1 9-15 * * 1-5">{stockT('stocksPage.messages.everyMinutes', { minutes: 1 })}</SelectItem>
                              <SelectItem value="*/3 9-15 * * 1-5">{stockT('stocksPage.messages.everyMinutes', { minutes: 3 })}</SelectItem>
                              <SelectItem value="*/5 9-15 * * 1-5">{stockT('stocksPage.messages.everyMinutes', { minutes: 5 })}</SelectItem>
                              <SelectItem value="*/10 9-15 * * 1-5">{stockT('stocksPage.messages.everyMinutes', { minutes: 10 })}</SelectItem>
                              <SelectItem value="*/15 9-15 * * 1-5">{stockT('stocksPage.messages.everyMinutes', { minutes: 15 })}</SelectItem>
                              <SelectItem value="*/30 9-15 * * 1-5">{stockT('stocksPage.messages.everyMinutes', { minutes: 30 })}</SelectItem>
                            </SelectContent>
                          </Select>
                          <span className="text-[10px] text-muted-foreground">{stockT('stocksPage.messages.tradingHours')}</span>
                        </div>

                        {/* Schedule Preview */}
                        {(() => {
                          const eff = effectiveSchedule(agent, stockAgent)
                          const isFollowingGlobal = !(stockAgent?.schedule || '').trim() && !!(agent.schedule || '').trim()
                          const previewKey = `${agent.name}|${agentDialogStock?.market}|${eff}`
                          const preview = eff ? schedulePreviewCache[previewKey] : null
                          const isLoading = eff ? !!schedulePreviewLoading[previewKey] : false
                          if (!eff) return null
                          return (
                            <div className="ml-[22px] rounded-lg border border-border/40 bg-background/30 px-2.5 py-2">
                              <div className="flex items-center justify-between">
                                <div className="text-[11px] text-muted-foreground">
                                  {stockT('stocksPage.messages.nextRunPreview')}{isFollowingGlobal ? <span className="ml-1 opacity-70">({stockT('stocksPage.messages.followGlobalShort')})</span> : null}
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
                              <SelectItem value="__default__">{stockT('stocksPage.messages.systemDefault')}</SelectItem>
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
                              <span className="text-[10px] text-muted-foreground">{stockT('stocksPage.messages.systemDefault')}</span>
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
                              {stockT('stocksPage.messages.deepAnalysis')}
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
                <Badge variant="default" className="text-[10px]">{stockT('stocksPage.messages.suggestedWatch')}</Badge>
              ) : (
                <Badge variant="secondary" className="text-[10px]">{stockT('stocksPage.messages.noAttention')}</Badge>
              )}
              {agentResultDialog?.notified && (
                <Badge variant="outline" className="text-[10px]">{stockT('stocksPage.messages.notificationSent')}</Badge>
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
              {stockT('stocksPage.messages.cancel')}
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
              {stockT('stocksPage.messages.relatedNews')}
            </DialogTitle>
            <DialogDescription>
              {newsDialogSymbol
                ? stockT('stocksPage.messages.newsTitleForStock', { name: newsDialogSymbol })
                : stockT('stocksPage.messages.newsTitleAll')
              }
            </DialogDescription>
          </DialogHeader>

          {/* 股票筛选器 */}
          <div className="flex items-center gap-2 flex-wrap py-2 border-b">
            <span className="text-[12px] text-muted-foreground">{stockT('stocksPage.messages.newsFilter')}</span>
            <button
              onClick={() => { setNewsDialogSymbol(''); loadNews() }}
              className={`text-[11px] px-2.5 py-1 rounded-md transition-colors ${
                !newsDialogSymbol
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-accent/50 text-muted-foreground hover:bg-accent'
              }`}
            >
              {stockT('stocksPage.markets.all')}
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
          <div className="flex-1 overflow-y-auto min-h-0 py-2 scrollbar">
            {newsLoading ? (
              <div className="flex items-center justify-center py-12">
                <span className="w-5 h-5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
                <span className="ml-2 text-[13px] text-muted-foreground">{stockT('stocksPage.messages.loading')}</span>
              </div>
            ) : news.length === 0 ? (
              <div className="text-center py-12 text-muted-foreground text-[13px]">
                {stockT('stocksPage.messages.noAnalysis')}
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
                              {stockT('stocksPage.messages.announcement')}
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
                        title={stockT('stocksPage.messages.viewOriginal')}
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
              {news.length} {stockT('stocksPage.messages.amountUnit')} {stockT('stocksPage.messages.stockNews')}
            </span>
            <Button variant="secondary" size="sm" onClick={() => loadNews(newsDialogSymbol || undefined)} disabled={newsLoading}>
              <RefreshCw className={`w-3 h-3 ${newsLoading ? 'animate-spin' : ''}`} />
              {stockT('stocksPage.messages.refreshed')}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
