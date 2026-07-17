import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { Plus, Trash2, Pencil, Search, X, TrendingUp, Bot, Play, RefreshCw, Wallet, PiggyBank, ArrowUpRight, ArrowDownRight, Building2, ChevronDown, ChevronRight, Cpu, Bell, Clock, Newspaper, ExternalLink, BarChart3, Brain, Banknote } from 'lucide-react'
import { fetchAPI, stocksApi, type AIService, type NotifyChannel } from '@panwatch/api'
import { useLocalStorage } from '@/lib/utils'
import { SuggestionBadge, type SuggestionInfo, type KlineSummary } from '@panwatch/biz-ui/components/suggestion-badge'
import { buildKlineSuggestion } from '@/lib/kline-scorer'
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

interface Position {
  id: number
  stock_id: number
  sort_order?: number
  symbol: string
  name: string
  market: string
  cost_price: number
  quantity: number
  invested_amount: number | null
  trading_style: string  // short: çŸ­çº¿, swing: æ³¢æ®µ, long: é•¿çº¿
  current_price: number | null
  current_price_cny: number | null  // äººæ°‘å¸ä»·æ ¼ï¼ˆæ¸¯è‚¡æ¢ç®—åï¼‰
  change_pct: number | null
  market_value: number | null
  market_value_cny: number | null  // äººæ°‘å¸å¸‚å€¼
  pnl: number | null
  pnl_pct: number | null
  daily_pnl: number | null
  daily_pnl_pct: number | null
  exchange_rate: number | null  // æ±‡ç‡ï¼ˆä»…æ¸¯è‚¡ï¼‰
}

interface AccountSummary {
  id: number
  name: string
  available_funds: number
  total_market_value: number
  total_cost: number
  total_pnl: number
  total_pnl_pct: number
  total_daily_pnl: number
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
    total_daily_pnl: number
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
  execution_mode: string  // batch: æ‰¹é‡åˆ†æ, single: é€åªåˆ†æ
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
  // æœç´¢é€‰ä¸­çš„è‚¡ç¥¨ä¿¡æ¯ï¼ˆæ–°å¢æŒä»“æ—¶ç”¨ï¼‰
  stock_symbol: string
  stock_name: string
  stock_market: string
}

// è‚¡ç¥¨å»ºè®®ä¿¡æ¯ï¼ˆæ¥è‡ªç›˜ä¸­ç›‘æ§ APIï¼‰
interface StockSuggestionData {
  symbol: string
  suggestion: SuggestionInfo | null
  kline: KlineSummary | null
}

// å»ºè®®æ± ä¸­çš„å»ºè®®ï¼ˆåŒ…å«æ¥æºå’Œæ—¶é—´ä¿¡æ¯ï¼‰
interface PoolSuggestion {
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

interface PriceAlertRuleSummary {
  stock_symbol: string
  market: string
  enabled: boolean
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
  let grandDailyPnl = 0

  const accounts = portfolio.accounts.map(account => {
    let accMarketValue = 0
    let accCost = 0
    let accDailyPnl = 0

    const positions = account.positions.map(pos => {
      const quote = quotes[`${pos.market}:${pos.symbol}`]
      const current_price = quote?.current_price ?? pos.current_price ?? null
      const change_pct = quote?.change_pct ?? pos.change_pct ?? null
      const rate = pos.market === 'HK' ? hkdRate : pos.market === 'US' ? usdRate : 1

      const cost = pos.cost_price * pos.quantity * rate
      accCost += cost

      let market_value: number | null = null
      let market_value_cny: number | null = null
      let pnl: number | null = null
      let pnl_pct: number | null = null
      let daily_pnl: number | null = null
      let daily_pnl_pct: number | null = null

      if (current_price != null) {
        market_value = current_price * pos.quantity
        market_value_cny = market_value * rate
        accMarketValue += market_value_cny
        pnl = market_value_cny - cost
        pnl_pct = cost > 0 ? (pnl / cost * 100) : 0
      }

      if (current_price != null && change_pct != null && change_pct !== -100) {
        const prev = current_price / (1 + change_pct / 100)
        if (isFinite(prev) && prev > 0) {
          daily_pnl = round2((current_price - prev) * pos.quantity * rate)
          daily_pnl_pct = round2(change_pct)
          accDailyPnl += daily_pnl
        }
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
        daily_pnl,
        daily_pnl_pct,
        exchange_rate: pos.market === 'HK' || pos.market === 'US' ? rate : null,
      }
    })

    const accPnl = accMarketValue - accCost
    const accPnlPct = accCost > 0 ? (accPnl / accCost * 100) : 0
    const accTotalAssets = accMarketValue + account.available_funds

    grandMarketValue += accMarketValue
    grandCost += accCost
    grandAvailable += account.available_funds
    grandDailyPnl += accDailyPnl

    return {
      ...account,
      total_market_value: round2(accMarketValue),
      total_cost: round2(accCost),
      total_pnl: round2(accPnl),
      total_pnl_pct: round2(accPnlPct),
      total_daily_pnl: round2(accDailyPnl),
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
      total_daily_pnl: round2(grandDailyPnl),
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

  // Auto-refresh (æŒä¹…åŒ–åˆ° localStorage)
  const [autoRefresh, setAutoRefresh] = useLocalStorage('panwatch_stocks_autoRefresh', false)
  const [refreshInterval, setRefreshInterval] = useLocalStorage('panwatch_stocks_refreshInterval', 30)
  const [lastRefreshTime, setLastRefreshTime] = useState<Date | null>(null)
  const refreshTimerRef = useRef<ReturnType<typeof setInterval>>()

  // Alerts / Scanning
  const [scanning, setScanning] = useState(false)

  type ViewTab = 'positions' | 'watchlist'
  const [viewTab, setViewTab] = useLocalStorage<ViewTab>('panwatch_stocks_viewTab', 'positions')

  // è‚¡ç¥¨ AI å»ºè®®ï¼ˆæ¥è‡ªç›˜ä¸­ç›‘æ§ APIï¼‰
  const [suggestions] = useState<Record<string, StockSuggestionData>>({})
  // å»ºè®®æ± å»ºè®®ï¼ˆæ¥è‡ª /suggestions APIï¼‰
  const [poolSuggestions, setPoolSuggestions] = useState<Record<string, PoolSuggestion>>({})
  const [poolSuggestionsLoading, setPoolSuggestionsLoading] = useState(false)
  const [priceAlertSummaryMap, setPriceAlertSummaryMap] = useState<Record<string, { total: number; enabled: number }>>({})

  // News Dialog
  const [newsDialogOpen, setNewsDialogOpen] = useState(false)
  const [newsDialogSymbol, setNewsDialogSymbol] = useState<string>('')  // ç©º=å…¨éƒ¨, å¦åˆ™=æŒ‡å®šè‚¡ç¥¨
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
  // Guard to prevent overlapping Kçº¿åˆ·æ–°ä»»åŠ¡å¯¼è‡´å®é™…å¹¶å‘è¶…é™
  const klineRefreshInFlight = useRef<Promise<void> | null>(null)

  // Stock form
  const [showStockForm, setShowStockForm] = useState(false)
  const [stockForm, setStockForm] = useState<StockForm>(emptyStockForm)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchMarket, setSearchMarket] = useState('')  // æœç´¢å¸‚åœºç­›é€‰
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
  const [positionSearchMarket, setPositionSearchMarket] = useState('')  // æœç´¢å¸‚åœºç­›é€‰
  const [positionSearchResults, setPositionSearchResults] = useState<SearchResult[]>([])
  const [positionSearching, setPositionSearching] = useState(false)
  const [showPositionDropdown, setShowPositionDropdown] = useState(false)
  const positionSearchTimer = useRef<ReturnType<typeof setTimeout>>()
  const positionDropdownRef = useRef<HTMLDivElement>(null)

  // Sell dialog
  const [sellDialogOpen, setSellDialogOpen] = useState(false)
  const [sellTarget, setSellTarget] = useState<Position | null>(null)
  const [sellForm, setSellForm] = useState({ sell_price: '', sell_quantity: '', fee: '0', note: '' })

  // Agent dialog
  const [agentDhå=DÓECB1×¹Ûw!j»(š+myÚ.¶‡öÓ®vŞÚ-jZ]‚ˆX™[¹¢$9§+9.íÏÓX™[‚ˆ[œ]ˆ˜[YO^ÜÜÚ][Û‘›Ü›K˜ÛÜİÜšXÙ_BˆÛÚ[™ÙO^ÙHOˆÙ]ÜÚ][Û‘›Ü›JÈ‹‹œÜÚ][Û‘›Ü›KÛÜİÜšXÙNˆK\™Ù]˜[YHJ_BˆXÙZÛ\HŒŒ‚ˆÛ\ÜÓ˜[YOH™›Û[[Û›È‚ˆ[œ][ÙOH™XÚ[X[‚ˆÏ‚ˆÙ]‚ˆ]‚ˆX™[¹£ y.äù¥l:aãÏÓX™[‚ˆ[œ]ˆ˜[YO^ÜÜÚ][Û‘›Ü›Kœ]X[]_BˆÛÚ[™ÙO^ÙHOˆÙ]ÜÚ][Û‘›Ü›JÈ‹‹œÜÚ][Û‘›Ü›K]X[]NˆK\™Ù]˜[YHJ_BˆXÙZÛ\HŒ‚ˆÛ\ÜÓ˜[YOH™›Û[[Û›È‚ˆ[œ][ÙOH›[Y\šXÈ‚ˆÏ‚ˆÙ]‚ˆÙ]‚ˆ]ˆÛ\ÜÓ˜[YOH™ÜšYÜšYXÛÛËLˆØ\M‚ˆ]‚ˆX™[¹¢¥yaiz-a:aäHÜ[ˆÛ\ÜÓ˜[YOH^[]]YY›Ü™YÜ›İ[™ÍŒ^VÌL\HŠ:`"yhjÊOÜÜ[ÓX™[‚ˆ[œ]ˆ˜[YO^ÜÜÚ][Û‘›Ü›Kš[™\İYØ[[İ[BˆÛÚ[™ÙO^ÙHOˆÙ]ÜÚ][Û‘›Ü›JÈ‹‹œÜÚ][Û‘›Ü›K[™\İYØ[[İ[ˆK\™Ù]˜[YHJ_BˆXÙZÛ\Hº`"yhjÈ‚ˆÛ\ÜÓ˜[YOH™›Û[[Û›È‚ˆ[œ][ÙOH™XÚ[X[‚ˆÏ‚ˆÙ]‚ˆ]‚ˆX™[¹.©9¦$úhã¹¨/Ü[ˆÛ\ÜÓ˜[YOH^[]]YY›Ü™YÜ›İ[™›Û[›Ü›X[Š:`"yhjÊOÜÜ[ÓX™[‚ˆÙ[Xİˆ˜[YO^ÜÜÚ][Û‘›Ü›K˜Y[™×Üİ[_BˆÛ•˜[YPÚ[™ÙO^İ˜[OˆÙ]ÜÚ][Û‘›Ü›JÈ‹‹œÜÚ][Û‘›Ü›K˜Y[™×Üİ[Nˆ˜[OOH	××Û›Û™W×ÉÈÈ	ÉÈˆ˜[J_Bˆ‚ˆÙ[XİšYÙÙ\‚ˆÙ[Xİ˜[YHXÙZÛ\H¹.#z+¯¹ïkˆˆÏ‚ˆÔÙ[XİšYÙÙ\‚ˆÙ[XİÛÛ[‚ˆÙ[Xİ][H˜[YOH—×Û›Û™W×È¹.#z+¯¹ïkÔÙ[Xİ][O‚ˆÙ[Xİ][H˜[YOHœÚÜ¹çëyî¯È
KMyi*JOÔÙ[Xİ][O‚ˆÙ[Xİ][H˜[YOHœİÚ[™È¹¬è¹«­H
KM9dj
OÔÙ[Xİ][O‚ˆÙ[Xİ][H˜[YOH›Û™Èºeoùî¯È
9¥l9§"
OÔÙ[Xİ][O‚ˆÔÙ[XİÛÛ[‚ˆÔÙ[Xİ‚ˆÙ]‚ˆÙ]‚ˆ]ˆÛ\ÜÓ˜[YOH™›^\İYKY[™Ø\LˆLˆ‚ˆ]Ûˆ˜\šX[H™ÚÜİˆÛÛXÚÏ^Ê
HOˆÙ]ÜÚ][Û‘X[ÙÓÜ[Š˜[ÙJ_O¹cå¹­¢Ğ]Û‚ˆ]Û‚ˆÛÛXÚÏ^Ú[™TÜÚ][Û”İX›Z]Bˆ\ØX›Y^È\ÜÚ][Û‘›Ü›K˜ÛÜİÜšXÙH\ÜÚ][Û‘›Ü›Kœ]X[]H
YY]ÜÚ][Û’Y	‰ˆ\ÜÚ][Û‘›Ü›KœİØÚ×ÚY	‰ˆ\ÜÚ][Û‘›Ü›KœİØÚ×ÜŞ[X›Û
_Bˆ‚ˆÙY]ÜÚ][Û’YÈ	ù/çykf	Èˆ	ù­îùb¨	ßBˆĞ]Û‚ˆÙ]‚ˆÙ]‚ˆÑX[ÙĞÛÛ[‚ˆÑX[ÙÏ‚‚ˆËÊˆYÙ[\ÜÚYÛ›Y[X[ÙÈ
‹ßBˆX[ÙÈÜ[^ÈHXYÙ[X[ÙÔİØÚßHÛ“Ü[Ú[™ÙO^ÛÜ[ˆOˆ[Ü[ˆ	‰ˆÙ]YÙ[X[ÙÔİØÚÊ[
_O‚ˆX[ÙĞÛÛ[‚ˆX[ÙÒXY\‚ˆX[ÙÕ]Oºacyïk¹æäy£©ÈYÙ[ÑX[ÙÕ]O‚ˆX[ÙÑ\ØÜš\[Û‚ˆ9..ˆØYÙ[X[ÙÔİØÚÏË›˜[Y_{ï"ØYÙ[X[ÙÔİØÚÏËœŞ[X›Û{ï"z`"y¢êz) yæäy£©ùæ¡YÙ[ˆÑX[ÙÑ\ØÜš\[Û‚ˆÑX[ÙÒXY\‚ˆ]ˆÛ\ÜÓ˜[YOHœÜXÙK^KLÈ]Lˆ‚ˆØYÙ[Ë›[™İOOHÈ
ˆÛ\ÜÓ˜[YOH^VÌLÜH^[]]YY›Ü™YÜ›İ[™KM^XÙ[\ˆ¹¦ ¹¥è9cëùå*YÙ[Ü‚ˆ
Hˆ
ˆYÙ[Ë›X\
YÙ[OˆÂˆÛÛœİİØÚĞYÙ[HYÙ[X[ÙÔİØÚÏË˜YÙ[ÏË™š[™
HOˆK˜YÙ[Û˜[YHOOHYÙ[›˜[YJBˆÛÛœİ\Ğ\ÜÚYÛ™YHH\İØÚĞYÙ[ˆÛÛœİ\Ğ˜]Ú[ÙHHYÙ[™^Xİ][Û—Û[ÙHOOH	Ø˜]Ú	Âˆ™]\›ˆ
ˆ]ˆÙ^O^ØYÙ[›˜[Y_HÛ\ÜÓ˜[YOHœ›İ[™Y^™ËXXØÙ[ÌÌİ™\˜™ËXXØÙ[ÍL˜[œÚ][Û‹XÛÛÜœÈİ™\™›İËZY[ˆ‚ˆ]ˆÛ\ÜÓ˜[YOH™›^][\ËXÙ[\ˆ\İYKX™]ÙY[ˆLËH‚ˆ]ˆÛ\ÜÓ˜[YOH™›^][\ËXÙ[\ˆØ\LÈ‚ˆ]ˆÛ\ÜÓ˜[YO^ØËLˆLˆ›İ[™YY[	ØYÙ[™[˜X›YÈ	Ø™ËY[Y\˜[ML	Èˆ	Ø™ËX›Ü™\‰ßXHÏ‚ˆ]‚ˆ]ˆÛ\ÜÓ˜[YOH™›^][\ËXÙ[\ˆØ\Lˆ‚ˆÜ[ˆÛ\ÜÓ˜[YOH^VÌLÜH›Û[YY][H^Y›Ü™YÜ›İ[™ØYÙ[™\Ü^WÛ˜[Y_OÜÜ[‚ˆ˜YÙH˜\šX[HœÙXÛÛ™\HˆÛ\ÜÓ˜[YOH^VÎ\H‚ˆÚ\Ğ˜]Ú[ÙHÈ	ù¢nzaãÉÈˆ	ú`$9cê‰ßBˆĞ˜YÙO‚ˆÙ]‚ˆÛ\ÜÓ˜[YOH^VÌL\H^[]]YY›Ü™YÜ›İ[™]LHØYÙ[™\ØÜš\[ÛŸOÜ‚ˆÙ]‚ˆÙ]‚ˆİÚ]ÚˆÚXÚÙY^Ú\Ğ\ÜÚYÛ™YBˆÛÚXÚÙYÚ[™ÙO^Ê
HOˆYÙ[X[ÙÔİØÚÈ	‰ˆÙÙÛPYÙ[
YÙ[X[ÙÔİØÚËYÙ[›˜[YJ_Bˆ\ØX›Y^ÈXYÙ[™[˜X›YBˆÏ‚ˆÙ]‚ˆÚ\Ğ\ÜÚYÛ™Y	‰ˆ\Ğ˜]Ú[ÙH	‰ˆ
ˆ]ˆÛ\ÜÓ˜[YOHœLËH‹LËHL‚ˆÛ\ÜÓ˜[YOH^VÌL\H^[]]YY›Ü™YÜ›İ[™‚ˆ:, ùn©¸à PRyª(yg¢øà z`&¹çéy®(:`dú+íùg*H™YH‹ØYÙ[ÈˆÛ\ÜÓ˜[YOH^\š[X\Hİ™\[™\›[™HYÙ[:acyïkØOˆ:hmzgh¹îçù. :+¯¹ïk‚ˆÜ‚ˆÙ]‚ˆ
_BˆÚ\Ğ\ÜÚYÛ™Y	‰ˆZ\Ğ˜]Ú[ÙH	‰ˆ
ˆ]ˆÛ\ÜÓ˜[YOHœLËH‹LËHLÜXÙK^KL‹H‚ˆËÊˆØÚY[KÒ[\˜[Ù[Xİ
‹ßBˆ]ˆÛ\ÜÓ˜[YOH™›^][\ËXÙ[\ˆØ\Lˆ‚ˆÛØÚÈÛ\ÜÓ˜[YOHËLËHLËH^[]]YY›Ü™YÜ›İ[™›^\Úš[šËLˆÏ‚ˆÙ[Xİˆ˜[YO^ÜİØÚĞYÙ[ËœØÚY[H	××ÙY˜][×ÉßBˆÛ•˜[YPÚ[™ÙO^İ˜[OˆYÙ[X[ÙÔİØÚÈ	‰ˆ\]TİØÚĞYÙ[ØÚY[JYÙ[X[ÙÔİØÚËYÙ[›˜[YK˜[OOH	××ÙY˜][×ÉÈÈ	ÉÈˆ˜[
_Bˆ‚ˆÙ[XİšYÙÙ\ˆÛ\ÜÓ˜[YOHšMÈ^VÌL\HËX]]ÈZ[‹]ËVÌMHL‹H™ËXXØÙ[ÍL›Ü™\‹X›Ü™\‹ÍL‚ˆÙ[Xİ˜[YHXÙZÛ\H¹¢iú(c:eí:f¥ˆÏ‚ˆÔÙ[XİšYÙÙ\‚ˆÙ[XİÛÛ[‚ˆÙ[Xİ][H˜[YOH—×ÙY˜][×Èº-çúf£ùaj9l`ÔÙ[Xİ][O‚ˆÙ[Xİ][H˜[YOHŠ‹ÌHKLMH
ˆ
ˆKMH¹«ãÈH9b!ºd§ÏÔÙ[Xİ][O‚ˆÙ[Xİ][H˜[YOHŠ‹ÌÈKLMH
ˆ
ˆKMH¹«ãÈÈ9b!ºd§ÏÔÙ[Xİ][O‚ˆÙ[Xİ][H˜[YOHŠ‹ÍHKLMH
ˆ
ˆKMH¹«ãÈH9b!ºd§ÏÔÙ[Xİ][O‚ˆÙ[Xİ][H˜[YOHŠ‹ÌLKLMH
ˆ
ˆKMH¹«ãÈL9b!ºd§ÏÔÙ[Xİ][O‚ˆÙ[Xİ][H˜[YOHŠ‹ÌMHKLMH
ˆ
ˆKMH¹«ãÈMH9b!ºd§ÏÔÙ[Xİ][O‚ˆÙ[Xİ][H˜[YOHŠ‹ÌÌKLMH
ˆ
ˆKMH¹«ãÈÌ9b!ºd§ÏÔÙ[Xİ][O‚ˆÔÙ[XİÛÛ[‚ˆÔÙ[Xİ‚ˆÜ[ˆÛ\ÜÓ˜[YOH^VÌLH^[]]YY›Ü™YÜ›İ[™¹.©9¦$ù¥í¹«­OÜÜ[‚ˆÙ]‚‚ˆËÊˆØÚY[H™]šY]È
‹ßBˆÊ

HOˆÂˆÛÛœİY™ˆHY™™Xİ]™TØÚY[JYÙ[İØÚĞYÙ[
BˆÛÛœİ\Ñ›ÛİÚ[™ÑÛØ˜[HJİØÚĞYÙ[ËœØÚY[H	ÉÊKš[J
H	‰ˆHJYÙ[œØÚY[H	ÉÊKš[J
BˆÛÛœİ™]šY]ÈHY™ˆÈØÚY[T™]šY]ĞØXÚVÙY™—Hˆ[ˆÛÛœİ\ÓØY[™ÈHY™ˆÈH\ØÚY[T™]šY]ÓØY[™ÖÙY™—Hˆ˜[ÙBˆYˆ
YY™ŠH™]\›ˆ[ˆ™]\›ˆ
ˆ]ˆÛ\ÜÓ˜[YOH›[VÌŒœH›İ[™Y[È›Ü™\ˆ›Ü™\‹X›Ü™\‹Í™ËX˜XÚÙÜ›İ[™ÌÌL‹HKLˆ‚ˆ]ˆÛ\ÜÓ˜[YOH™›^][\ËXÙ[\ˆ\İYKX™]ÙY[ˆ‚ˆ]ˆÛ\ÜÓ˜[YOH^VÌL\H^[]]YY›Ü™YÜ›İ[™‚ˆ9§*¹§iz)é¹cäy¥íºeí:h¡:)âÚ\Ñ›ÛİÚ[™ÑÛØ˜[ÈÜ[ˆÛ\ÜÓ˜[YOH›[LHÜXÚ]KMÌŠ:-çúf£ùaj9l`
OÜÜ[ˆˆ[BˆÙ]‚ˆÚ\ÓØY[™È	‰ˆ
ˆÜ[ˆÛ\ÜÓ˜[YOHËLÈLÈ›Ü™\‹Lˆ›Ü™\‹\š[X\KÌÌ›Ü™\‹]\š[X\H›İ[™YY[[š[X]K\Ü[ˆˆÏ‚ˆ
_BˆÙ]‚ˆÉÙ\œ›Ü‰È[ˆ
™]šY]ÈßJHÈ
ˆ]ˆÛ\ÜÓ˜[YOH›]LH^VÌL\H^[]]YY›Ü™YÜ›İ[™Ê™]šY]È\È[JK™\œ›ÜŸOÙ]‚ˆ
Hˆ
™]šY]È\ÈØÚY[T™]šY]È[™Yš[™Y
OË›™^Ü[œÏË›[™İÈ
ˆ]ˆÛ\ÜÓ˜[YOH›]LH›^›^]Ü˜\][\ËXÙ[\ˆØ\LKH^VÌL\H^[]]YY›Ü™YÜ›İ[™‚ˆÊ™]šY]È\ÈØÚY[T™]šY]ÊK›™^Ü[œË›X\

JHOˆ
ˆÜ[ˆÙ^O^Ú_HÛ\ÜÓ˜[YOHœLKHKLH›İ[™Y›Ü™\ˆ›Ü™\‹X›Ü™\‹ÍŒ™ËXXØÙ[ÌŒ›Û[[Û›Èˆ]O^İO‚ˆÙ›Ü›X]™]šY]Õ[YJ
™]šY]È\ÈØÚY[T™]šY]ÊK[Y^›Û™J_BˆÜÜ[‚ˆ
J_BˆÊ™]šY]È\ÈØÚY[T™]šY]ÊK[Y^›Û™HÈ
ˆÜ[ˆÛ\ÜÓ˜[YOH›ÜXÚ]KMŒŠÊ™]šY]È\ÈØÚY[T™]šY]ÊK[Y^›Û™_JOÜÜ[‚ˆ
Hˆ[BˆÙ]‚ˆ
Hˆ
ˆ]ˆÛ\ÜÓ˜[YOH›]LH^VÌL\H^[]]YY›Ü™YÜ›İ[™¸ %Ù]‚ˆ
_Bˆ]ˆÛ\ÜÓ˜[YOH›]LH^VÌLH^[]]YY›Ü™YÜ›İ[™ÍÌ›Û[[Û›ÈœØÚY[NˆÙY™ŸOÙ]‚ˆÙ]‚ˆ
BˆJJ
_B‚ˆËÊˆRH[Ù[Ù[Xİ
‹ßBˆ]ˆÛ\ÜÓ˜[YOH™›^][\ËXÙ[\ˆØ\Lˆ‚ˆÜHÛ\ÜÓ˜[YOHËLËHLËH^[]]YY›Ü™YÜ›İ[™›^\Úš[šËLˆÏ‚ˆÙ[Xİˆ˜[YO^ÜİØÚĞYÙ[Ë˜ZWÛ[Ù[ÚYËÔİš[™Ê
HÏÈ	××ÙY˜][×ÉßBˆÛ•˜[YPÚ[™ÙO^İ˜[OˆYÙ[X[ÙÔİØÚÈ	‰ˆ\]TİØÚĞYÙ[[Ù[
YÙ[X[ÙÔİØÚËYÙ[›˜[YK˜[OOH	××ÙY˜][×ÉÈÈ[ˆ\œÙR[
˜[
J_Bˆ‚ˆÙ[XİšYÙÙ\ˆÛ\ÜÓ˜[YOHšMÈ^VÌL\HËX]]ÈZ[‹]ËVÌMHL‹H™ËXXØÙ[ÍL›Ü™\‹X›Ü™\‹ÍL‚ˆÙ[Xİ˜[YHÏ‚ˆÔÙ[XİšYÙÙ\‚ˆÙ[XİÛÛ[‚ˆÙ[Xİ][H˜[YOH—×ÙY˜][×È¹ìîùîçúnæ:+©ÔÙ[Xİ][O‚ˆÜÙ\šXÙ\Ë›X\
İ˜ÈOˆ
ˆÙ[XİÜ›İ\Ù^O^Üİ˜ËšYO‚ˆÙ[XİX™[Üİ˜Ë›˜[Y_OÔÙ[XİX™[‚ˆÜİ˜Ë›[Ù[Ë›X\
HOˆ
ˆÙ[Xİ][HÙ^O^ÛKšYH˜[YO^ÛKšYÔİš[™Ê
_O‚ˆÛK›˜[Y_^ÛK›˜[YHOOHK›[Ù[È
	ÛK›[Ù[JXˆ	ÉßBˆÔÙ[Xİ][O‚ˆ
J_BˆÔÙ[XİÜ›İ\‚ˆ
J_BˆÔÙ[XİÛÛ[‚ˆÔÙ[Xİ‚ˆÙ]‚ˆËÊˆ›İYšXØ][ÛˆÚ[›™[È
‹ßBˆØÚ[›™[Ë›[™İˆ	‰ˆ
ˆ]ˆÛ\ÜÓ˜[YOH™›^][\ËXÙ[\ˆØ\Lˆ›^]Ü˜\‚ˆ™[Û\ÜÓ˜[YOHËLËHLËH^[]]YY›Ü™YÜ›İ[™›^\Úš[šËLˆÏ‚ˆØÚ[›™[Ë›X\
ÚOˆÂˆÛÛœİ\ÔÙ[XİYH
İØÚĞYÙ[Ë››İYWØÚ[›™[ÚYÈ×JKš[˜ÛY\ÊÚšY
Bˆ™]\›ˆ
ˆ]Û‚ˆÙ^O^ØÚšYBˆÛÛXÚÏ^Ê
HOˆYÙ[X[ÙÔİØÚÈ	‰ˆÙÙÛTİØÚĞYÙ[Ú[›™[
YÙ[X[ÙÔİØÚËYÙ[›˜[YKÚšY
_BˆÛ\ÜÓ˜[YO^Ø^VÌLHLˆKLH›İ[™Y[Y›Ü™\ˆ˜[œÚ][Û‹XÛÛÜœÈ	Âˆ\ÔÙ[XİYˆÈ	Ø™Ë\š[X\KÌL›Ü™\‹\š[X\KÌÌ^\š[X\H›Û[YY][IÂˆˆ	Ø™ËXXØÙ[ÌÌ›Ü™\‹X›Ü™\‹ÍL^[]]YY›Ü™YÜ›İ[™İ™\˜›Ü™\‹\š[X\KÌÌ	ÂˆXBˆ‚ˆØÚ›˜[Y_BˆØ]Û‚ˆ
BˆJ_BˆÊİØÚĞYÙ[Ë››İYWØÚ[›™[ÚYÈ×JK›[™İOOH	‰ˆ
ˆÜ[ˆÛ\ÜÓ˜[YOH^VÌLH^[]]YY›Ü™YÜ›İ[™¹ìîùîçúnæ:+©ÜÜ[‚ˆ
_BˆÙ]‚ˆ
_BˆËÊˆšYÙÙ\ˆ]Ûˆ
‹ßBˆ]ˆÛ\ÜÓ˜[YOH™›^][\ËXÙ[\ˆØ\LˆLH‚ˆ]Û‚ˆ˜\šX[HœÙXÛÛ™\HˆÚ^™OHœÛHˆÛ\ÜÓ˜[YOHšMÈ^VÌL\HL‹H‚ˆ\ØX›Y^İšYÙÙ\š[™ĞYÙ[OOHYÙ[›˜[Y_BˆÛÛXÚÏ^Ê
HOˆYÙ[X[ÙÔİØÚÈ	‰ˆšYÙÙ\”İØÚĞYÙ[
YÙ[X[ÙÔİØÚËšYYÙ[›˜[YJ_Bˆ‚ˆİšYÙÙ\š[™ĞYÙ[OOHYÙ[›˜[YHÈ
ˆÜ[ˆÛ\ÜÓ˜[YOHËLÈLÈ›Ü™\‹Lˆ›Ü™\‹Xİ\œ™[ÌÌ›Ü™\‹]Xİ\œ™[›İ[™YY[[š[X]K\Ü[ˆˆÏ‚ˆ
Hˆ
ˆ^HÛ\ÜÓ˜[YOHËLÈLÈˆÏ‚ˆ
_Bˆ9êâùclùb!¹§¤ˆĞ]Û‚ˆÙ]‚ˆÙ]‚ˆ
_BˆÙ]‚ˆ
BˆJBˆ
_BˆÙ]‚ˆÑX[ÙĞÛÛ[‚ˆÑX[ÙÏ‚‚ˆËÊˆYÙ[9b!¹§¤9îäù§§9o.yê¥È
‹ßBˆX[ÙÈÜ[^ÈHXYÙ[™\İ[X[ÙßHÛ“Ü[Ú[™ÙO^ÛÜ[ˆOˆ[Ü[ˆ	‰ˆÙ]YÙ[™\İ[X[ÙÊ[
_O‚ˆX[ÙĞÛÛ[Û\ÜÓ˜[YOH›X^]Ë[Y‚ˆX[ÙÒXY\‚ˆX[ÙÕ]HÛ\ÜÓ˜[YOH^X˜\ÙHØYÙ[™\İ[X[ÙÏË]_OÑX[ÙÕ]O‚ˆX[ÙÑ\ØÜš\[ÛˆÛ\ÜÓ˜[YOH™›^][\ËXÙ[\ˆØ\LˆLH‚ˆØYÙ[™\İ[X[ÙÏËœÚİ[Ø[\È
ˆ˜YÙH˜\šX[H™Y˜][ˆÛ\ÜÓ˜[YOH^VÌLH¹nîº+«¹alù¬êĞ˜YÙO‚ˆ
Hˆ
ˆ˜YÙH˜\šX[HœÙXÛÛ™\HˆÛ\ÜÓ˜[YOH^VÌLH¹¥è:g 9alù¬êĞ˜YÙO‚ˆ
_BˆØYÙ[™\İ[X[ÙÏË››İYšYY	‰ˆ
ˆ˜YÙH˜\šX[H›İ][™HˆÛ\ÜÓ˜[YOH^VÌLH¹mì¹cäz` z`&¹çéOĞ˜YÙO‚ˆ
_BˆÑX[ÙÑ\ØÜš\[Û‚ˆÑX[ÙÒXY\‚ˆ]ˆÛ\ÜÓ˜[YOH›]LˆLÈ™ËXXØÙ[ÌÌ›İ[™Y[È‚ˆ™HÛ\ÜÓ˜[YOH^VÌLÜHÚ]\ÜXÙK\™K]Ü˜\›Û\Ø[œÈXY[™Ë\™[^Y‚ˆØYÙ[™\İ[X[ÙÏË˜ÛÛ[BˆÜ™O‚ˆÙ]‚ˆ]ˆÛ\ÜÓ˜[YOH™›^\İYKY[™]Lˆ‚ˆ]Ûˆ˜\šX[H›İ][™HˆÚ^™OHœÛHˆÛÛXÚÏ^Ê
HOˆÙ]YÙ[™\İ[X[ÙÊ[
_O‚ˆ9alúeëBˆĞ]Û‚ˆÙ]‚ˆÑX[ÙĞÛÛ[‚ˆÑX[ÙÏ‚‚ˆËÊˆ9æî9alú-a:+«ùo.yê¥È
‹ßBˆX[ÙÈÜ[^Û™]ÜÑX[ÙÓÜ[ŸHÛ“Ü[Ú[™ÙO^ÜÙ]™]ÜÑX[ÙÓÜ[ŸO‚ˆX[ÙĞÛÛ[Û\ÜÓ˜[YOH›X^]ËLX^ZVÎ]šH›^›^XÛÛ‚ˆX[ÙÒXY\‚ˆX[ÙÕ]HÛ\ÜÓ˜[YOH™›^][\ËXÙ[\ˆØ\Lˆ‚ˆ™]ÜÜ\\ˆÛ\ÜÓ˜[YOHËMHMH^X›YKMLˆÏ‚ˆ9æî9alú-a:+«ÂˆÑX[ÙÕ]O‚ˆX[ÙÑ\ØÜš\[Û‚ˆÛ™]ÜÑX[ÙÔŞ[X›ÛˆÈ	Û™]ÜÑX[ÙÔŞ[X›ÛH9æ¡9æî9alù¥¬:eîùd£9ak9db˜ˆˆ	ú!êº`"z ¨yæî9alù¥¬:eîùd£9ak9db»ï":/äHÌˆ9l#ù¥í»ï"IÂˆBˆÑX[ÙÑ\ØÜš\[Û‚ˆÑX[ÙÒXY\‚‚ˆËÊˆ: ¨yéj9ëfú`"yfj
‹ßBˆ]ˆÛ\ÜÓ˜[YOH™›^][\ËXÙ[\ˆØ\Lˆ›^]Ü˜\KLˆ›Ü™\‹Xˆ‚ˆÜ[ˆÛ\ÜÓ˜[YOH^VÌLœH^[]]YY›Ü™YÜ›İ[™¹ëfú`"NÜÜ[‚ˆ]Û‚ˆÛÛXÚÏ^Ê
HOˆÈÙ]™]ÜÑX[ÙÔŞ[X›Û
	ÉÊNÈØY™]ÜÊ
H_BˆÛ\ÜÓ˜[YO^Ø^VÌL\HL‹HKLH›İ[™Y[Y˜[œÚ][Û‹XÛÛÜœÈ	Âˆ[™]ÜÑX[ÙÔŞ[X›ÛˆÈ	Ø™Ë\š[X\H^\š[X\KY›Ü™YÜ›İ[™	Âˆˆ	Ø™ËXXØÙ[ÍL^[]]YY›Ü™YÜ›İ[™İ™\˜™ËXXØÙ[	ÂˆXBˆ‚ˆ9aj:`êˆØ]Û‚ˆÜİØÚÜËœÛXÙJL
K›X\
İØÚÈOˆ
ˆ]Û‚ˆÙ^O^ÜİØÚËœŞ[X›ÛBˆÛÛXÚÏ^Ê
HOˆÈÙ]™]ÜÑX[ÙÔŞ[X›Û
İØÚË›˜[YJNÈØY™]ÜÊİØÚË›˜[YJH_BˆÛ\ÜÓ˜[YO^Ø^VÌL\HL‹HKLH›İ[™Y[Y˜[œÚ][Û‹XÛÛÜœÈ	Âˆ™]ÜÑX[ÙÔŞ[X›ÛOOHİØÚË›˜[YBˆÈ	Ø™Ë\š[X\H^\š[X\KY›Ü™YÜ›İ[™	Âˆˆ	Ø™ËXXØÙ[ÍL^[]]YY›Ü™YÜ›İ[™İ™\˜™ËXXØÙ[	ÂˆXBˆ‚ˆÜİØÚË›˜[Y_BˆØ]Û‚ˆ
J_BˆÜİØÚÜË›[™İˆL	‰ˆ
ˆÜ[ˆÛ\ÜÓ˜[YOH^VÌLH^[]]YY›Ü™YÜ›İ[™ŠŞÜİØÚÜË›[™İHLOÜÜ[‚ˆ
_BˆÙ]‚‚ˆËÊˆ9¥¬:eîùb%ú(j
‹ßBˆ]ˆÛ\ÜÓ˜[YOH™›^LHİ™\™›İË^KX]]ÈZ[‹ZLKLˆ‚ˆÛ™]ÜÓØY[™ÈÈ
ˆ]ˆÛ\ÜÓ˜[YOH™›^][\ËXÙ[\ˆ\İYKXÙ[\ˆKLLˆ‚ˆÜ[ˆÛ\ÜÓ˜[YOHËMHMH›Ü™\‹Lˆ›Ü™\‹\š[X\KÌÌ›Ü™\‹]\š[X\H›İ[™YY[[š[X]K\Ü[ˆˆÏ‚ˆÜ[ˆÛ\ÜÓ˜[YOH›[Lˆ^VÌLÜH^[]]YY›Ü™YÜ›İ[™¹b¨:/oy.+K‹‹ÜÜ[‚ˆÙ]‚ˆ
Hˆ™]ÜË›[™İOOHÈ
ˆ]ˆÛ\ÜÓ˜[YOH^XÙ[\ˆKLLˆ^[]]YY›Ü™YÜ›İ[™^VÌLÜH‚ˆ9¦ ¹¥è9æî9alú-a:+«ÂˆÙ]‚ˆ
Hˆ
ˆ]ˆÛ\ÜÓ˜[YOHœÜXÙK^KLˆ‚ˆÛ™]ÜË›X\

][KY
HOˆ
ˆ]‚ˆÙ^O^Ø	Ú][KœÛİ\˜Ù_KIÚ][K™^\›˜[ÚYKIÚYXBˆÛ\ÜÓ˜[YOHœLÈ›İ[™Y[È™ËXXØÙ[ÌÌİ™\˜™ËXXØÙ[ÍL˜[œÚ][Û‹XÛÛÜœÈ‚ˆ‚ˆ]ˆÛ\ÜÓ˜[YOH™›^][\Ë\İ\\İYKX™]ÙY[ˆØ\LÈ‚ˆ]ˆÛ\ÜÓ˜[YOH™›^LHZ[‹]ËL‚ˆ]ˆÛ\ÜÓ˜[YOH™›^][\ËXÙ[\ˆØ\LˆX‹LKH‚ˆÜ[ˆÛ\ÜÓ˜[YO^Ø^VÌLHLKHKLH›İ[™Y	Âˆ][KœÛİ\˜ÙHOOH	ÙX\İ[Û™^IÈÈ	Ø™ËX[X™\‹MLÌL^X[X™\‹MŒ\šÎ^X[X™\‹M	È‚ˆ][KœÛİ\˜ÙHOOH	ÙX\İ[Û™^WÛ™]ÜÉÈÈ	Ø™ËX›YKMLÌL^X›YKML	È‚ˆ	Ø™ËY[Y\˜[MLÌL^Y[Y\˜[MŒ\šÎ^Y[Y\˜[M	ÂˆXO‚ˆÚ][KœÛİ\˜ÙWÛX™[BˆÜÜ[‚ˆÚ][Kš[\Ü[˜ÙHHˆ	‰ˆ
ˆÜ[ˆÛ\ÜÓ˜[YOH^VÌLHLKHKLH›İ[™Y™Ë\›ÜÙKMLÌL^\›ÜÙKML‚ˆ:aãz) BˆÜÜ[‚ˆ
_BˆÜ[ˆÛ\ÜÓ˜[YOH^VÌLH^[]]YY›Ü™YÜ›İ[™‚ˆÚ][KœX›\Úİ[Y_BˆÜÜ[‚ˆÙ]‚ˆBˆ™Y^Ú][K\›Bˆ\™Ù]H—Ø›[šÈ‚ˆ™[H››ÛÜ[™\ˆ›Ü™Y™\œ™\ˆ‚ˆÛ\ÜÓ˜[YOH^VÌLÜH›Û[YY][H^Y›Ü™YÜ›İ[™İ™\^\š[X\H˜[œÚ][Û‹XÛÛÜœÈ›ØÚÈ‚ˆ‚ˆÚ][K]_BˆØO‚ˆÚ][KœŞ[X›ÛË›[™İˆ	‰ˆ
ˆ]ˆÛ\ÜÓ˜[YOH™›^][\ËXÙ[\ˆØ\LKH]Lˆ‚ˆÚ][KœŞ[X›ÛËœÛXÙJJK›X\
Ş[HOˆÂˆÛÛœİİØÚÒ[™›ÈHİØÚÜË™š[™
ÈOˆËœŞ[X›ÛOOHŞ[JBˆÛÛœİİØÚÓ˜[YHHİØÚÒ[™›ÏË›˜[YHŞ[Bˆ™]\›ˆ
ˆ]Û‚ˆÙ^O^ÜŞ[_BˆÛÛXÚÏ^Ê
HOˆÈÙ]™]ÜÑX[ÙÔŞ[X›Û
İØÚÓ˜[YJNÈØY™]ÜÊİØÚÓ˜[YJH_BˆÛ\ÜÓ˜[YOH^VÌLHLKHKLH›İ[™Y™Ë\š[X\KÌL^\š[X\H›Û[[Û›Èİ™\˜™Ë\š[X\KÌŒ˜[œÚ][Û‹XÛÛÜœÈ‚ˆ‚ˆÜİØÚÓ˜[Y_BˆØ]Û‚ˆ
BˆJ_BˆÚ][KœŞ[X›ÛË›[™İˆH	‰ˆ
ˆÜ[ˆÛ\ÜÓ˜[YOH^VÌLH^[]]YY›Ü™YÜ›İ[™ŠŞÚ][KœŞ[X›ÛË›[™İH_OÜÜ[‚ˆ
_BˆÙ]‚ˆ
_BˆÙ]‚ˆBˆ™Y^Ú][K\›Bˆ\™Ù]H—Ø›[šÈ‚ˆ™[H››ÛÜ[™\ˆ›Ü™Y™\œ™\ˆ‚ˆÛ\ÜÓ˜[YOH™›^\Úš[šËLLKH›İ[™Y[Yİ™\˜™ËXXØÙ[˜[œÚ][Û‹XÛÛÜœÈ‚ˆ]OH¹§éyç"ùc§ù¥¡È‚ˆ‚ˆ^\›˜[[šÈÛ\ÜÓ˜[YOHËMM^[]]YY›Ü™YÜ›İ[™ˆÏ‚ˆØO‚ˆÙ]‚ˆÙ]‚ˆ
J_BˆÙ]‚ˆ
_BˆÙ]‚‚ˆËÊˆ9n¥z`ê9b-ù¥¬9£"zd«ˆ
‹ßBˆ]ˆÛ\ÜÓ˜[YOH™›^][\ËXÙ[\ˆ\İYKX™]ÙY[ˆLˆ›Ü™\‹]‚ˆÜ[ˆÛ\ÜÓ˜[YOH^VÌL\H^[]]YY›Ü™YÜ›İ[™‚ˆ9alHÛ™]ÜË›[™İH9§hz-a:+«ÂˆÜÜ[‚ˆ]Ûˆ˜\šX[HœÙXÛÛ™\HˆÚ^™OHœÛHˆÛÛXÚÏ^Ê
HOˆØY™]ÜÊ™]ÜÑX[ÙÔŞ[X›Û[™Yš[™Y
_H\ØX›Y^Û™]ÜÓØY[™ßO‚ˆ™Yœ™\ÚİÈÛ\ÜÓ˜[YO^ØËLÈLÈ	Û™]ÜÓØY[™ÈÈ	Ø[š[X]K\Ü[‰Èˆ	ÉßXHÏ‚ˆ9b-ù¥¬ˆĞ]Û‚ˆÙ]‚ˆÑX[ÙĞÛÛ[‚ˆÑX[ÙÏ‚ˆÙ]‚ˆ
BŸB