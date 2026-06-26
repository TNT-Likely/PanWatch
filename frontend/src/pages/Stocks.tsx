import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { Plus, Minus, Trash2, Pencil, Search, X, TrendingUp, Bot, Play, RefreshCw, Wallet, PiggyBank, ArrowUpRight, ArrowDownRight, Building2, ChevronDown, ChevronRight, Cpu, Bell, Clock, Newspaper, ExternalLink, PieChart, Archive, Landmark, GripVertical, Pin } from 'lucide-react'
import { fetchAPI, stocksApi, positionsApi, tradeDatetimeLocalToIso, tradeDatetimeIsoToLocal, type AIService, type NotifyChannel, type PositionAddResult, type PositionTrade, type PortfolioRecentTrade, type ClosedPosition, type InvestmentProfile, type IndustryChainInfo, type LmdReportSnapshot } from '@panwatch/api'
import { useLocalStorage } from '@/lib/utils'
import { SuggestionBadge, KlineLevelsBrief, type SuggestionInfo, type KlineSummary } from '@panwatch/biz-ui/components/suggestion-badge'
import { StockConceptTags, type StockConceptTagItem } from '@panwatch/biz-ui/components/stock-concept-tags'
import { IndustryChainBadge } from '@panwatch/biz-ui/components/industry-chain-badge'
import { AiChainRotationBanner } from '@panwatch/biz-ui/components/ai-chain-rotation-banner'
import {
  CHAIN_LAYER_LEGACY_MAP,
  CHAIN_LAYER_ORDER,
  formatIndustryChainDisplay,
  watchlistCardChainClass,
} from '@panwatch/biz-ui/lib/ai-chain-rotation'
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
import StockInsightModal, { type InsightTab } from '@panwatch/biz-ui/components/stock-insight-modal'
import { useRestoreStockInsight } from '@/lib/use-restore-stock-insight'
import { EtfOverviewModal } from '@panwatch/biz-ui/components/etf-overview-modal'
import { LmdReportSectionModal } from '@panwatch/biz-ui/components/lmd-report-section-modal'
import { LMD_DISPLAY_NAME } from '@panwatch/biz-ui/lib/lmd-report'
import { ReportMarkdown } from '@panwatch/biz-ui/components/report-markdown'
import type { LmdReportSection } from '@panwatch/biz-ui/lib/report-toc'
import { DeepAnalysisModal } from '@panwatch/biz-ui/components/deep-analysis-modal'
import StockPriceAlertPanel from '@panwatch/biz-ui/components/stock-price-alert-panel'
import { buildRollingCostPlan, buildRollingCostPlanBrief } from '@/lib/rolling-cost-plan'
import LongTermPlanPanel from '@panwatch/biz-ui/components/long-term-plan-panel'
import { WatchlistValuationBrief } from '@panwatch/biz-ui/components/watchlist-valuation-brief'
import { ChanEmotionBrief } from '@panwatch/biz-ui/components/chan-emotion-brief'
import { StockTradingAskButtons } from '@panwatch/biz-ui/components/stock-trading-ask-buttons'
import { insightApi, type ChanEmotionBrief as ChanEmotionBriefData, type AnalysisBriefItem } from '@panwatch/api/insight'
import { formatLmdBriefFromSnapshot } from '@panwatch/biz-ui/lib/analysis-brief'

interface AgentResult {
  success?: boolean
  message?: string
  title: string
  content: string
  should_alert: boolean
  notified: boolean
  skipped?: boolean
}

function sortWatchlistStocks<T extends { id: number; sort_order?: number; is_featured?: boolean }>(list: T[]): T[] {
  return [...list].sort((a, b) => {
    const aFeatured = a.is_featured ? 1 : 0
    const bFeatured = b.is_featured ? 1 : 0
    if (aFeatured !== bFeatured) return bFeatured - aFeatured
    const orderDiff = Number(a.sort_order || 0) - Number(b.sort_order || 0)
    if (orderDiff !== 0) return orderDiff
    return b.id - a.id
  })
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
  security_type?: string
  sort_order?: number
  is_featured?: boolean
  concept_tags?: StockConceptTagItem[]
  concept_tags_auto?: string[]
  concept_tags_manual?: string[]
  industry_chain?: IndustryChainInfo | null
  investment_profile?: InvestmentProfile
  agents: StockAgentInfo[]
}

const LEGACY_CHAIN_LAYER_MAP = CHAIN_LAYER_LEGACY_MAP

function normalizeChainFilterKey(key: string): string {
  const trimmed = (key || '').trim()
  if (!trimmed) return ''
  const sep = trimmed.indexOf(':')
  if (sep <= 0) return trimmed
  const sector = trimmed.slice(0, sep)
  const layer = trimmed.slice(sep + 1)
  return `${sector}:${LEGACY_CHAIN_LAYER_MAP[layer] || layer}`
}

function stockChainFilterKey(chain: IndustryChainInfo | null | undefined): string | null {
  if (!chain?.sector || !chain?.layer) return null
  return normalizeChainFilterKey(`${chain.sector}:${chain.layer}`)
}

interface OtherFundItem {
  label: string
  amount: number
}

interface Account {
  id: number
  name: string
  available_funds: number
  other_funds?: number
  other_fund_items?: OtherFundItem[]
  initial_funds?: number
  base_currency?: 'CNY' | 'HKD' | 'USD' | string
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
  trading_style: string  // short: 短线, swing: 波段, long: 长线
  current_price: number | null
  current_price_cny: number | null  // 人民币价格（港股换算后）
  change_pct: number | null
  market_value: number | null
  market_value_cny: number | null  // 人民币市值
  pnl: number | null
  pnl_pct: number | null
  daily_pnl: number | null
  daily_pnl_pct: number | null
  day_start_qty?: number
  today_trades?: Array<{ side: string; quantity: number; price: number }>
  exchange_rate: number | null  // 汇率（仅港股）
}

interface AccountSummary {
  id: number
  name: string
  available_funds: number
  other_funds?: number
  other_fund_items?: OtherFundItem[]
  initial_funds?: number
  base_currency?: 'CNY' | 'HKD' | 'USD' | string
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
    other_funds?: number
    initial_funds?: number
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
  security_type?: string
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
  pe_ratio?: number | null
}

interface StockForm {
  symbol: string
  name: string
  market: string
  security_type?: string
}

interface OtherFundItemForm {
  label: string
  amount: string
}

interface AccountForm {
  name: string
  base_currency: 'CNY' | 'HKD' | 'USD'
  available_funds: string
  other_fund_items: OtherFundItemForm[]
}

interface PositionForm {
  account_id: number
  stock_id: number
  cost_price: string
  quantity: string
  invested_amount: string
  trading_style: string
  trade_time: string
  // 搜索选中的股票信息（新增持仓时用）
  stock_symbol: string
  stock_name: string
  stock_market: string
}

const DEFAULT_TRADING_STYLE = 'short'

function resolveTradingStyle(style: string | null | undefined): string {
  if (style === 'long' || style === 'swing') return style
  return DEFAULT_TRADING_STYLE
}

function tradingStyleLabel(style: string | null | undefined, compact = false): string {
  const resolved = resolveTradingStyle(style)
  if (resolved === 'long') return compact ? '长' : '长线'
  if (resolved === 'swing') return compact ? '波' : '波段'
  return compact ? '短' : '短线'
}

function tradingStyleClass(style: string | null | undefined): string {
  const resolved = resolveTradingStyle(style)
  if (resolved === 'short') return 'bg-rose-500/10 text-rose-600'
  if (resolved === 'long') return 'bg-blue-500/10 text-blue-600'
  return 'bg-amber-500/10 text-amber-600'
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

const emptyStockForm: StockForm = { symbol: '', name: '', market: 'CN', security_type: 'stock' }
const emptyAccountForm: AccountForm = { name: '', base_currency: 'CNY', available_funds: '0', other_fund_items: [] }

const OTHER_FUND_LABEL_PRESETS = ['理财', '存款', '国债', '货币基金', '其他']

const sumOtherFundItems = (items: OtherFundItem[] | OtherFundItemForm[] | undefined) =>
  (items || []).reduce((sum, item) => sum + (parseFloat(String(item.amount)) || 0), 0)

const formatOtherFundSummary = (
  items: OtherFundItem[] | undefined,
  currency: string | undefined,
  formatAccountFunds: (value: number, currency?: string) => string,
) => {
  if (!items?.length) return ''
  return items.map((item) => `${item.label} ${formatAccountFunds(item.amount, currency)}`).join(' · ')
}

const ACCOUNT_CURRENCY_OPTIONS = [
  { value: 'CNY' as const, label: '人民币 (CNY)' },
  { value: 'HKD' as const, label: '港元 (HKD)' },
  { value: 'USD' as const, label: '美元 (USD)' },
]

const accountCurrencyLabel = (currency?: string) =>
  ACCOUNT_CURRENCY_OPTIONS.find((opt) => opt.value === currency)?.label?.replace(/ \(.*\)$/, '') || '人民币'

const accountFundsToCny = (
  amount: number,
  currency: string | undefined,
  rates: { HKD_CNY?: number; USD_CNY?: number },
) => {
  const cur = currency || 'CNY'
  if (cur === 'HKD') return amount * (rates.HKD_CNY ?? 0.92)
  if (cur === 'USD') return amount * (rates.USD_CNY ?? 7.25)
  return amount
}

const cnyToAccountFunds = (
  amountCny: number,
  currency: string | undefined,
  rates: { HKD_CNY?: number; USD_CNY?: number },
) => {
  const cur = currency || 'CNY'
  if (cur === 'HKD') return amountCny / (rates.HKD_CNY ?? 0.92)
  if (cur === 'USD') return amountCny / (rates.USD_CNY ?? 7.25)
  return amountCny
}

const calcPositionInitialFunds = (costPrice: string, quantity: string) => {
  const cost = parseFloat(costPrice)
  const qty = parseInt(quantity, 10)
  if (!isFinite(cost) || !isFinite(qty) || cost <= 0 || qty <= 0) return ''
  return String(round2(cost * qty))
}

const round2 = (value: number) => Math.round(value * 100) / 100

type TodayTradeLot = { side: string; quantity: number; price: number }

const computePositionDailyPnl = (
  currentPrice: number,
  quantity: number,
  prevClose: number | null,
  todayTrades: TodayTradeLot[],
  dayStartQty: number,
): { daily_pnl: number | null; daily_pnl_pct: number | null } => {
  if (quantity <= 0 || currentPrice <= 0) {
    return { daily_pnl: null, daily_pnl_pct: null }
  }

  if (todayTrades.length === 0) {
    if (prevClose == null || prevClose <= 0) {
      return { daily_pnl: null, daily_pnl_pct: null }
    }
    const daily_pnl = round2((currentPrice - prevClose) * quantity)
    const daily_pnl_pct = round2((currentPrice - prevClose) / prevClose * 100)
    return { daily_pnl, daily_pnl_pct }
  }

  let overnight = Math.max(0, dayStartQty)
  const buyLots: Array<[number, number]> = []
  let realized = 0
  let costBasis = prevClose != null && prevClose > 0 ? overnight * prevClose : 0

  for (const trade of todayTrades) {
    const qty = Math.trunc(trade.quantity)
    const price = trade.price
    if (qty <= 0 || price <= 0) continue

    if (trade.side === 'buy') {
      buyLots.push([qty, price])
      costBasis += qty * price
      continue
    }

    let sellQty = qty
    const sellPrice = price
    const fromOvernight = Math.min(overnight, sellQty)
    if (fromOvernight > 0 && prevClose != null && prevClose > 0) {
      realized += (sellPrice - prevClose) * fromOvernight
      costBasis -= fromOvernight * prevClose
    }
    overnight -= fromOvernight
    sellQty -= fromOvernight

    while (sellQty > 0 && buyLots.length > 0) {
      const [lotQty, lotPrice] = buyLots[0]
      const take = Math.min(lotQty, sellQty)
      realized += (sellPrice - lotPrice) * take
      costBasis -= take * lotPrice
      const remaining = lotQty - take
      sellQty -= take
      if (remaining <= 0) {
        buyLots.shift()
      } else {
        buyLots[0] = [remaining, lotPrice]
      }
    }
  }

  let unrealized = 0
  if (overnight > 0 && prevClose != null && prevClose > 0) {
    unrealized += (currentPrice - prevClose) * overnight
  }
  for (const [lotQty, lotPrice] of buyLots) {
    unrealized += (currentPrice - lotPrice) * lotQty
  }

  const daily_pnl = round2(realized + unrealized)
  const daily_pnl_pct = costBasis > 0 ? round2(daily_pnl / costBasis * 100) : null
  return { daily_pnl, daily_pnl_pct }
}

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
  let grandOtherFunds = 0
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

      if (current_price != null) {
        const dayStartQty = pos.day_start_qty ?? pos.quantity
        const todayTrades = pos.today_trades ?? []
        let prevClose: number | null = null
        if (change_pct != null && change_pct !== -100) {
          const prev = current_price / (1 + change_pct / 100)
          if (isFinite(prev) && prev > 0) prevClose = prev
        }
        const daily = computePositionDailyPnl(
          current_price,
          pos.quantity,
          prevClose,
          todayTrades,
          dayStartQty,
        )
        if (daily.daily_pnl != null) {
          daily_pnl = round2(daily.daily_pnl * rate)
          daily_pnl_pct = daily.daily_pnl_pct
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

    const accCurrency = account.base_currency || 'CNY'
    const accAvailableCny = accountFundsToCny(account.available_funds, accCurrency, portfolio.exchange_rates || {})
    const accOtherFunds = account.other_funds ?? 0
    const accOtherCny = accountFundsToCny(accOtherFunds, accCurrency, portfolio.exchange_rates || {})
    const accPnl = accMarketValue - accCost
    const accTotalAssets = accMarketValue + accAvailableCny + accOtherCny
    const accInitialCny = accTotalAssets - accPnl
    const accPnlPct = accInitialCny > 0 ? (accPnl / accInitialCny * 100) : 0

    grandMarketValue += accMarketValue
    grandCost += accCost
    grandAvailable += accAvailableCny
    grandOtherFunds += accOtherCny
    grandDailyPnl += accDailyPnl

    return {
      ...account,
      base_currency: accCurrency,
      other_funds: round2(accOtherFunds),
      initial_funds: round2(cnyToAccountFunds(accTotalAssets - accPnl, accCurrency, portfolio.exchange_rates || {})),
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
  const grandTotalAssets = grandMarketValue + grandAvailable + grandOtherFunds
  const grandInitialFundsTotal = grandTotalAssets - grandPnl
  const grandPnlPct = grandInitialFundsTotal > 0 ? (grandPnl / grandInitialFundsTotal * 100) : 0

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
      other_funds: round2(grandOtherFunds),
      initial_funds: round2(grandInitialFundsTotal),
      total_assets: round2(grandTotalAssets),
    },
  }
}

const POSITION_ACTION_TONES = {
  default: 'bg-accent/50 text-muted-foreground hover:bg-accent hover:text-foreground',
  kline: 'bg-slate-500/12 text-slate-600 hover:bg-slate-500/20 dark:text-slate-300',
  alert: 'bg-amber-500/12 text-amber-700 hover:bg-amber-500/20 dark:text-amber-300',
  report: 'bg-blue-500/12 text-blue-600 hover:bg-blue-500/20 dark:text-blue-300',
  history: 'bg-cyan-500/12 text-cyan-700 hover:bg-cyan-500/20 dark:text-cyan-300',
  analysis: 'bg-violet-500/12 text-violet-600 hover:bg-violet-500/20 dark:text-violet-300',
  ai: 'bg-indigo-500/12 text-indigo-600 hover:bg-indigo-500/20 dark:text-indigo-300',
  add: 'bg-rose-500/12 text-rose-600 hover:bg-rose-500/20 dark:text-rose-300',
  reduce: 'bg-emerald-500/12 text-emerald-600 hover:bg-emerald-500/20 dark:text-emerald-300',
} as const

type PositionActionTone = keyof typeof POSITION_ACTION_TONES

function PositionActionButton({
  tone = 'default',
  className = '',
  ...props
}: React.ComponentProps<typeof Button> & { tone?: PositionActionTone }) {
  return (
    <Button
      variant="ghost"
      size="sm"
      className={`h-8 px-2.5 text-[12px] font-medium ${POSITION_ACTION_TONES[tone]} ${className}`}
      {...props}
    />
  )
}

function PositionAgentBadges({
  stockAgents,
  agentConfigs,
  runningAgentName,
}: {
  stockAgents?: StockAgentInfo[]
  agentConfigs: AgentConfig[]
  runningAgentName?: string | null
}) {
  const configured = stockAgents || []
  if (configured.length === 0) {
    return (
      <span className="text-[10px] text-muted-foreground/50 inline-flex items-center gap-0.5 whitespace-nowrap">
        <Bot className="w-2.5 h-2.5 shrink-0" /> 未配置
      </span>
    )
  }

  const ordered = runningAgentName
    ? [...configured].sort((a, b) => {
        if (a.agent_name === runningAgentName) return -1
        if (b.agent_name === runningAgentName) return 1
        return 0
      })
    : configured

  return (
    <div className="flex flex-col gap-0.5 leading-tight">
      {ordered.map(sa => {
        const agent = agentConfigs.find(a => a.name === sa.agent_name)
        const isRunning = runningAgentName === sa.agent_name
        const label = agent?.display_name || sa.agent_name
        return (
          <span key={sa.agent_name} className="inline-flex items-center gap-0.5 min-w-0 max-w-full">
            <span
              className="text-[9px] px-1 py-px rounded bg-accent/70 text-muted-foreground truncate max-w-full"
              title={label}
            >
              {label}
            </span>
            {isRunning && (
              <span
                className="w-2 h-2 border border-amber-600/30 border-t-amber-600 rounded-full animate-spin shrink-0"
                title="执行中"
              />
            )}
          </span>
        )
      })}
    </div>
  )
}

function PositionExtraActions({
  onNews,
  onEdit,
  onDelete,
  onAgentConfig,
  btnClass = '',
}: {
  onNews: () => void
  onEdit: () => void
  onDelete: () => void
  onAgentConfig?: () => void
  btnClass?: string
}) {
  return (
    <>
      {onAgentConfig ? (
        <PositionActionButton tone="ai" className={btnClass} title="Agent 配置" onClick={onAgentConfig}>
          Agent
        </PositionActionButton>
      ) : null}
      <PositionActionButton tone="default" className={btnClass} title="相关资讯" onClick={onNews}>
        资讯
      </PositionActionButton>
      <PositionActionButton tone="default" className={btnClass} title="编辑持仓" onClick={onEdit}>
        编辑
      </PositionActionButton>
      <PositionActionButton
        tone="reduce"
        className={`${btnClass} text-destructive hover:bg-destructive/10`}
        title="删除持仓"
        onClick={onDelete}
      >
        删除
      </PositionActionButton>
    </>
  )
}

function PositionRowActions({
  stockId,
  symbol,
  market,
  stockName,
  showKline = false,
  compact = false,
  align = 'center',
  onKline,
  onReports,
  onAnalysis,
  onHistory,
  onAskAI,
  onAdd,
  onReduce,
  onAgentConfig,
  onNews,
  onEdit,
  onDelete,
  onPriceAlertChanged,
  getPriceAlertSummary,
}: {
  stockId: number
  symbol: string
  market: string
  stockName: string
  showKline?: boolean
  compact?: boolean
  align?: 'start' | 'center' | 'end'
  onKline: () => void
  onReports: () => void
  onAnalysis: () => void
  onHistory: () => void
  onAskAI: () => void
  onAdd: () => void
  onReduce: () => void
  onAgentConfig?: () => void
  onNews: () => void
  onEdit: () => void
  onDelete: () => void
  onPriceAlertChanged: () => void
  getPriceAlertSummary: (symbol: string, market: string) => { total: number; enabled: number }
}) {
  const btnClass = compact ? 'h-6 px-1.5 text-[10px]' : 'h-7 px-2 text-[11px]'
  const alertClass = compact
    ? 'h-6 px-1.5 text-[10px] font-medium bg-amber-500/12 text-amber-700 hover:bg-amber-500/20 dark:text-amber-300'
    : 'h-7 px-2 text-[11px] font-medium bg-amber-500/12 text-amber-700 hover:bg-amber-500/20 dark:text-amber-300'
  const alignClass = align === 'end' ? 'justify-end' : align === 'start' ? 'justify-start' : 'justify-center'
  const rowClass = `flex items-center gap-0.5 flex-wrap ${alignClass}`

  const priceAlert = (
    <StockPriceAlertPanel
      mode="text"
      stockId={stockId}
      symbol={symbol}
      market={market}
      stockName={stockName}
      initialTotal={getPriceAlertSummary(symbol, market).total}
      initialEnabled={getPriceAlertSummary(symbol, market).enabled}
      onChanged={onPriceAlertChanged}
      triggerClassName={alertClass}
    />
  )

  return (
    <div className="flex flex-col gap-1">
      <div className={rowClass}>
        {showKline ? (
          <PositionActionButton tone="kline" className={btnClass} onClick={onKline} title="K线指标">
            K线
          </PositionActionButton>
        ) : null}
        {priceAlert}
        <PositionActionButton tone="report" className={btnClass} onClick={onReports} title="查看 Agent 报告">
          报告
        </PositionActionButton>
      </div>
      <div className={rowClass}>
        <PositionActionButton tone="analysis" className={btnClass} onClick={onAnalysis} title="深度分析">
          分析
        </PositionActionButton>
        <PositionActionButton tone="history" className={btnClass} onClick={onHistory} title="历史交易明细">
          历史交易
        </PositionActionButton>
      </div>
      <div className={rowClass}>
        <PositionActionButton tone="ai" className={btnClass} onClick={onAskAI} title="问 AI">
          问AI
        </PositionActionButton>
        <PositionActionButton tone="add" className={btnClass} onClick={onAdd} title="加仓">
          加仓
        </PositionActionButton>
        <PositionActionButton tone="reduce" className={btnClass} onClick={onReduce} title="减仓">
          减仓
        </PositionActionButton>
      </div>
      <div className={rowClass}>
        <PositionExtraActions
          btnClass={btnClass}
          onAgentConfig={onAgentConfig}
          onNews={onNews}
          onEdit={onEdit}
          onDelete={onDelete}
        />
      </div>
    </div>
  )
}

function FeaturedPinButton({
  isFeatured,
  onClick,
  size = 'md',
  className = '',
}: {
  isFeatured: boolean
  onClick: () => void
  size?: 'sm' | 'md'
  className?: string
}) {
  const iconClass = size === 'sm' ? 'w-3 h-3' : 'w-3.5 h-3.5'
  const btnClass = size === 'sm' ? 'h-5 w-5' : 'h-6 w-6'

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation()
        onClick()
      }}
      onMouseDown={(e) => e.stopPropagation()}
      className={`inline-flex items-center justify-center rounded-md transition-colors shrink-0 ${
        isFeatured
          ? 'bg-amber-500/15 text-amber-600 hover:bg-amber-500/25 ring-1 ring-amber-500/30'
          : 'text-muted-foreground/45 hover:text-amber-600 hover:bg-amber-500/10'
      } ${btnClass} ${className}`}
      title={isFeatured ? '取消置顶' : '置顶到列表最前'}
      aria-label={isFeatured ? '取消置顶' : '置顶到列表最前'}
      aria-pressed={isFeatured}
    >
      <Pin className={`${iconClass} ${isFeatured ? 'fill-current rotate-45' : ''}`} />
    </button>
  )
}

function appendKlineSuggestionContext(
  parts: string[],
  suggestion: SuggestionInfo | null,
  kline: KlineSummary | null,
) {
  if (kline) {
    const items = []
    if (kline.trend) items.push(`趋势${kline.trend}`)
    if (kline.macd_status) items.push(`MACD${kline.macd_status}`)
    if (kline.rsi_status) items.push(`RSI${kline.rsi_status}`)
    if (kline.support != null) items.push(`支撑${kline.support}`)
    if (kline.resistance != null) items.push(`压力${kline.resistance}`)
    if (items.length) parts.push(`技术面：${items.join('，')}`)
  }
  if (suggestion) {
    parts.push(`技术评分：${suggestion.action_label}(score=${suggestion.score})，信号：${suggestion.signal || '中性'}`)
  }
}

function WatchlistRowActions({
  stock,
  isHolding,
  compact = false,
  onKline,
  onReports,
  onValuation,
  onFundamentals,
  onAnalysis,
  onAskAI,
  onBuy,
  onLongTermPlan,
  onEtfOverview,
  onAgentConfig,
  onNews,
  onDelete,
  onPriceAlertChanged,
  getPriceAlertSummary,
}: {
  stock: Stock
  isHolding: boolean
  compact?: boolean
  onKline: () => void
  onReports: () => void
  onValuation: () => void
  onFundamentals: () => void
  onAnalysis: () => void
  onAskAI: () => void
  onBuy: () => void
  onLongTermPlan: () => void
  onEtfOverview?: () => void
  onAgentConfig: () => void
  onNews: () => void
  onDelete: () => void
  onPriceAlertChanged: () => void
  getPriceAlertSummary: (symbol: string, market: string) => { total: number; enabled: number }
}) {
  const btnClass = compact ? 'h-6 px-1.5 text-[10px]' : ''
  const alertClass = compact
    ? 'h-6 px-1.5 text-[10px] font-medium bg-amber-500/12 text-amber-700 hover:bg-amber-500/20 dark:text-amber-300'
    : 'h-8 px-2.5 text-[12px] font-medium bg-amber-500/12 text-amber-700 hover:bg-amber-500/20 dark:text-amber-300'
  const showEtf = stock.security_type === 'etf'

  return (
    <div
      className={`flex items-center gap-0.5 flex-wrap ${compact ? 'justify-start' : 'justify-end'}`}
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <PositionActionButton tone="kline" className={btnClass} onClick={onKline} title="K线指标">K线</PositionActionButton>
      <StockPriceAlertPanel
        mode="text"
        stockId={stock.id}
        symbol={stock.symbol}
        market={stock.market}
        stockName={stock.name}
        initialTotal={getPriceAlertSummary(stock.symbol, stock.market).total}
        initialEnabled={getPriceAlertSummary(stock.symbol, stock.market).enabled}
        onChanged={onPriceAlertChanged}
        triggerClassName={alertClass}
      />
      <PositionActionButton tone="report" className={btnClass} onClick={onReports} title="查看 Agent 报告">报告</PositionActionButton>
      <PositionActionButton tone="default" className={btnClass} onClick={onValuation} title={`${LMD_DISPLAY_NAME} · 估值`}>估值</PositionActionButton>
      <PositionActionButton tone="default" className={btnClass} onClick={onFundamentals} title={`${LMD_DISPLAY_NAME} · 基本面`}>基本面</PositionActionButton>
      <PositionActionButton tone="analysis" className={btnClass} onClick={onAnalysis} title="深度分析">分析</PositionActionButton>
      <PositionActionButton tone="ai" className={btnClass} onClick={onAskAI} title="问 AI">问AI</PositionActionButton>
      {!isHolding ? (
        <PositionActionButton tone="add" className={btnClass} onClick={onBuy} title="买入建仓">买入</PositionActionButton>
      ) : null}
      <PositionActionButton tone="default" className={btnClass} onClick={onLongTermPlan} title="长线计划">长线</PositionActionButton>
      {showEtf && onEtfOverview ? (
        <PositionActionButton tone="default" className={btnClass} onClick={onEtfOverview} title="ETF 详情">ETF</PositionActionButton>
      ) : null}
      <PositionActionButton tone="ai" className={btnClass} onClick={onAgentConfig} title="Agent 配置">Agent</PositionActionButton>
      <PositionActionButton tone="default" className={btnClass} onClick={onNews} title="相关资讯">资讯</PositionActionButton>
      <PositionActionButton
        tone="reduce"
        className={`${btnClass} ${isHolding ? 'opacity-40 cursor-not-allowed' : 'text-destructive hover:bg-destructive/10'}`}
        onClick={onDelete}
        disabled={isHolding}
        title={isHolding ? '持仓中的股票无法删除' : '删除股票'}
      >
        删除
      </PositionActionButton>
    </div>
  )
}

export default function StocksPage({ mode }: { mode?: 'positions' | 'watchlist' }) {
  const navigate = useNavigate()
  const location = useLocation()
  const pageMode: 'positions' | 'watchlist' =
    mode ?? (location.pathname.startsWith('/watchlist') ? 'watchlist' : 'positions')
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
  const [quotes, setQuotes] = useState<Record<string, { current_price: number | null; change_pct: number | null; pe_ratio?: number | null }>>({})
  const [lmdSnapshots, setLmdSnapshots] = useState<Record<string, LmdReportSnapshot>>({})
  const [quotesLoading, setQuotesLoading] = useState(false)
  // Keyed by `${market}:${symbol}` to avoid cross-market symbol collisions
  const [klineSummaries, setKlineSummaries] = useState<Record<string, KlineSummary>>({})
  const [chanEmotionMap, setChanEmotionMap] = useState<Record<string, ChanEmotionBriefData>>({})
  const [chanEmotionLoadingKeys, setChanEmotionLoadingKeys] = useState<Record<string, boolean>>({})
  const [analysisBriefMap, setAnalysisBriefMap] = useState<Record<string, AnalysisBriefItem>>({})

  // Auto-refresh (持久化到 localStorage)
  const [autoRefresh, setAutoRefresh] = useLocalStorage('panwatch_stocks_autoRefresh', false)
  const [refreshInterval, setRefreshInterval] = useLocalStorage('panwatch_stocks_refreshInterval', 30)
  const [lastRefreshTime, setLastRefreshTime] = useState<Date | null>(null)
  const refreshTimerRef = useRef<ReturnType<typeof setInterval>>()

  // Alerts / Scanning
  const [scanning, setScanning] = useState(false)

  // 股票 AI 建议（来自盘中监控 API）
  const [suggestions] = useState<Record<string, StockSuggestionData>>({})
  // 建议池建议（来自 /suggestions API）
  const [poolSuggestions, setPoolSuggestions] = useState<Record<string, PoolSuggestion>>({})
  const [poolSuggestionsLoading, setPoolSuggestionsLoading] = useState(false)
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
  const [etfOverviewOpen, setEtfOverviewOpen] = useState(false)
  const [etfOverviewCode, setEtfOverviewCode] = useState('')
  const [etfOverviewName, setEtfOverviewName] = useState<string | undefined>(undefined)
  const [lmdSectionModal, setLmdSectionModal] = useState<{
    symbol: string
    market: string
    name?: string
    section: LmdReportSection
  } | null>(null)
  const [insightOpen, setInsightOpen] = useState(false)
  const [insightSymbol, setInsightSymbol] = useState('')
  const [insightMarket, setInsightMarket] = useState('CN')
  const [insightName, setInsightName] = useState<string | undefined>(undefined)
  const [insightHasPosition, setInsightHasPosition] = useState(false)
  const [insightInitialTab, setInsightInitialTab] = useState<InsightTab | undefined>(undefined)
  const [insightExpandAddPosition, setInsightExpandAddPosition] = useState(false)
  const [insightExpandReducePosition, setInsightExpandReducePosition] = useState(false)
  const [positionRecentTrades, setPositionRecentTrades] = useState<Record<number, PortfolioRecentTrade>>({})

  // Market status
  const [marketStatus, setMarketStatus] = useState<MarketStatus[]>([])
  // Guard to prevent overlapping K线刷新任务导致实际并发超限
  const klineRefreshInFlight = useRef<Promise<void> | null>(null)
  const chanEmotionInFlightRef = useRef<Set<string>>(new Set())

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
  const [positionForm, setPositionForm] = useState<PositionForm>({ account_id: 0, stock_id: 0, cost_price: '', quantity: '', invested_amount: '', trading_style: DEFAULT_TRADING_STYLE, trade_time: '', stock_symbol: '', stock_name: '', stock_market: 'CN' })
  const [editPositionId, setEditPositionId] = useState<number | null>(null)
  const [editPositionOriginal, setEditPositionOriginal] = useState<{ quantity: number; cost_price: number } | null>(null)
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
  const [longTermPlanStock, setLongTermPlanStock] = useState<Stock | null>(null)

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
  const [watchlistFeaturedOnly, setWatchlistFeaturedOnly] = useLocalStorage<boolean>('panwatch_watchlist_featured_only', false)
  const [watchlistTagFilter, setWatchlistTagFilter] = useLocalStorage<string>('panwatch_watchlist_tag_filter', '')
  const [watchlistChainFilter, setWatchlistChainFilter] = useLocalStorage<string>('panwatch_watchlist_chain_filter', '')

  // Remove watchlist modal
  const [removeWatchStock, setRemoveWatchStock] = useState<Stock | null>(null)
  const [removingWatchStock, setRemovingWatchStock] = useState(false)
  const [draggingWatchStockId, setDraggingWatchStockId] = useState<number | null>(null)
  const [draggingPositionId, setDraggingPositionId] = useState<number | null>(null)
  const [draggingPositionAccountId, setDraggingPositionAccountId] = useState<number | null>(null)

  // 已清仓记录
  const [closedPositions, setClosedPositions] = useState<ClosedPosition[]>([])
  const [closedPositionsLoading, setClosedPositionsLoading] = useState(false)
  const [closedTradesDialog, setClosedTradesDialog] = useState<ClosedPosition | null>(null)
  const [positionTradesDialog, setPositionTradesDialog] = useState<{
    positionId: number
    symbol: string
    name: string
    accountName: string
    market: string
  } | null>(null)
  const [positionTrades, setPositionTrades] = useState<PositionTrade[]>([])
  const [positionTradesLoading, setPositionTradesLoading] = useState(false)
  const [editingTradeId, setEditingTradeId] = useState<number | null>(null)
  const [tradeEditForm, setTradeEditForm] = useState({
    side: 'buy' as 'buy' | 'sell',
    price: '',
    quantity: '',
    traded_at: '',
    note: '',
  })
  const [tradeEditSaving, setTradeEditSaving] = useState(false)
  const watchDragSnapshotRef = useRef<Stock[] | null>(null)
  const pendingWatchOrderRef = useRef<Stock[] | null>(null)
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
      const ordered = sortWatchlistStocks(prev)
      const moved = moveById(ordered, fromId, toId).map((s, idx) => ({ ...s, sort_order: idx + 1 }))
      pendingWatchOrderRef.current = moved
      return moved
    })
  }, [])

  const commitWatchlistReorder = useCallback(async () => {
    const current = pendingWatchOrderRef.current ?? sortWatchlistStocks(stocks)
    pendingWatchOrderRef.current = null
    if (!current || current.length === 0) return
    try {
      await persistWatchlistOrder(current)
    } catch (e) {
      if (watchDragSnapshotRef.current) setStocks(watchDragSnapshotRef.current)
      toast(e instanceof Error ? e.message : '保存关注排序失败', 'error')
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
      toast(e instanceof Error ? e.message : '保存持仓排序失败', 'error')
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
      if (stockData.some(s => s.market === 'CN' && !(s.concept_tags_auto || []).length)
        || stockData.some(s => !s.industry_chain?.layer)) {
        window.setTimeout(async () => {
          try {
            const refreshed = await fetchAPI<Stock[]>('/stocks')
            setStocks(refreshed)
          } catch {
            // ignore background refresh errors
          }
        }, 8000)
      }
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

  const loadPositionRecentTrades = useCallback(async () => {
    try {
      const rows = await positionsApi.recentTrades(80)
      const map: Record<number, PortfolioRecentTrade> = {}
      for (const row of rows || []) {
        if (!map[row.position_id]) map[row.position_id] = row
      }
      setPositionRecentTrades(map)
    } catch {
      setPositionRecentTrades({})
    }
  }, [])

  const loadClosedPositions = useCallback(async () => {
    setClosedPositionsLoading(true)
    try {
      const rows = await positionsApi.closedPositions(100)
      setClosedPositions(rows || [])
    } catch {
      setClosedPositions([])
    } finally {
      setClosedPositionsLoading(false)
    }
  }, [])

  const handlePortfolioChanged = useCallback(
    (result?: PositionAddResult) => {
      if (result?.position) {
        setPortfolioRaw((prev) => {
          if (!prev) return prev
          return {
            ...prev,
            accounts: prev.accounts.map((acc) => ({
              ...acc,
              positions: acc.positions.map((p) =>
                p.id === result.position.id
                  ? {
                      ...p,
                      cost_price: result.position.cost_price,
                      quantity: result.position.quantity,
                      invested_amount: result.position.invested_amount,
                    }
                  : p,
              ),
            })),
          }
        })
        setPositionRecentTrades((prev) => ({
          ...prev,
          [result.position.id]: {
            ...result.trade,
            account_name: result.position.account_name || '',
            symbol: result.position.stock_symbol || '',
            market: '',
            stock_name: result.position.stock_name || '',
          },
        }))
      }
      void loadPortfolio()
      void loadPositionRecentTrades()
      void loadClosedPositions()
      if (pageMode === 'watchlist' && result?.position) {
        toast('已建仓，可在持仓页查看', 'success')
      }
    },
    [loadPositionRecentTrades, loadClosedPositions, pageMode, toast],
  )

  const buildQuoteItems = useCallback((): QuoteRequestItem[] => {
    const items: QuoteRequestItem[] = []
    const seen = new Set<string>()

    for (const stock of stocks) {
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
      const map: Record<string, { current_price: number | null; change_pct: number | null; pe_ratio?: number | null }> = {}
      for (const item of data) {
        map[`${item.market}:${item.symbol}`] = {
          current_price: item.current_price ?? null,
          change_pct: item.change_pct ?? null,
          pe_ratio: item.pe_ratio ?? null,
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

  const refreshLmdSnapshots = useCallback(async () => {
    if (pageMode !== 'watchlist' || stocks.length === 0) return
    try {
      const data = await stocksApi.lmdSnapshotsBatch(stocks.map((s) => s.symbol))
      const map: Record<string, LmdReportSnapshot> = {}
      for (const item of data || []) {
        map[`${item.market}:${item.symbol}`] = item
      }
      setLmdSnapshots(map)
    } catch (e) {
      console.warn(`加载${LMD_DISPLAY_NAME}快照失败:`, e)
    }
  }, [pageMode, stocks])

  useEffect(() => {
    if (!portfolioRaw) return
    setPortfolio(mergePortfolioQuotes(portfolioRaw, quotes))
  }, [portfolioRaw, quotes])

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
      // 增量合并：本轮单只失败时保留旧值，避免技术徽章闪断/消失
      setKlineSummaries(prev => ({ ...prev, ...map }))
    })()
    klineRefreshInFlight.current = run
    try { await run } finally { klineRefreshInFlight.current = null }
  }, [buildQuoteItems])

  const refreshAnalysisBriefs = useCallback(async () => {
    const items = buildQuoteItems()
    if (items.length === 0) return
    try {
      const data = await insightApi.analysisBriefBatch(items)
      const map: Record<string, AnalysisBriefItem> = {}
      for (const row of data || []) {
        map[`${row.market}:${row.symbol}`] = row
      }
      setAnalysisBriefMap((prev) => ({ ...prev, ...map }))
    } catch {
      // 无报告或接口失败时忽略，不阻塞问 AI
    }
  }, [buildQuoteItems])

  const fetchChanEmotionForStock = useCallback(async (symbol: string, market: string, holding = false) => {
    const key = `${market || 'CN'}:${symbol}`
    if (chanEmotionInFlightRef.current.has(key)) return
    let skip = false
    setChanEmotionMap((prev) => {
      if (prev[key]) skip = true
      return prev
    })
    if (skip) return
    chanEmotionInFlightRef.current.add(key)
    setChanEmotionLoadingKeys((prev) => ({ ...prev, [key]: true }))
    try {
      const res = await insightApi.chanEmotionStrategy(symbol, { market, holding })
      setChanEmotionMap((prev) => ({
        ...prev,
        [key]: {
          action: res.action,
          action_label: res.action_label,
          win_rate: res.win_rate,
          emotion_phase: res.emotion_phase,
          emotion_label: res.emotion_label,
          reason: res.reason,
        },
      }))
    } catch {
      // ignore single failure
    } finally {
      chanEmotionInFlightRef.current.delete(key)
      setChanEmotionLoadingKeys((prev) => {
        const next = { ...prev }
        delete next[key]
        return next
      })
    }
  }, [])

  useEffect(() => {
    if (stocks.length === 0 && (!portfolioRaw || portfolioRaw.accounts.length === 0)) return
    refreshQuotes()
    ;(async () => {
      try { await refreshKlines() } catch {}
      try { await refreshAnalysisBriefs() } catch {}
    })()
    if (pageMode === 'watchlist') {
      refreshLmdSnapshots().catch(() => undefined)
    }
  }, [stocks, portfolioRaw, refreshQuotes, pageMode, refreshLmdSnapshots, refreshKlines, refreshAnalysisBriefs])

  // 从建议池加载建议（包含历史建议和多来源建议）
  const loadPoolSuggestions = useCallback(async () => {
    setPoolSuggestionsLoading(true)
    try {
      const data = await fetchAPI<Record<string, PoolSuggestion>>('/suggestions?include_expired=true')
      setPoolSuggestions(data)
    } catch (e) {
      console.warn('加载建议池失败:', e)
    } finally {
      setPoolSuggestionsLoading(false)
    }
  }, [])

  const loadPriceAlertSummaries = useCallback(async () => {
    try {
      const rows = await fetchAPI<PriceAlertRuleSummary[]>('/price-alerts')
      const map: Record<string, { total: number; enabled: number }> = {}
      for (const r of rows || []) {
        const key = `${String(r.market || 'CN').toUpperCase()}:${String(r.stock_symbol || '').toUpperCase()}`
        if (!map[key]) map[key] = { total: 0, enabled: 0 }
        map[key].total += 1
        if (r.enabled) map[key].enabled += 1
      }
      setPriceAlertSummaryMap(map)
    } catch (e) {
      console.warn('加载提醒摘要失败:', e)
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

  const openEtfOverview = useCallback((symbol: string, name?: string) => {
    setEtfOverviewCode(symbol)
    setEtfOverviewName(name)
    setEtfOverviewOpen(true)
  }, [])

  // Open news dialog - pass stock name for more stable search
  const openNewsDialog = useCallback((stockName?: string) => {
    setNewsDialogSymbol(stockName || '')  // 存储名称用于 UI 显示
    setNewsDialogOpen(true)
    loadNews(stockName)
  }, [loadNews])

  const openStockDetail = useCallback((stockSymbol: string, stockMarket: string, stockName?: string, hasPosition?: boolean) => {
    setInsightExpandAddPosition(false)
    setInsightExpandReducePosition(false)
    setInsightSymbol(stockSymbol)
    setInsightMarket(stockMarket || 'CN')
    setInsightName(stockName)
    setInsightHasPosition(!!hasPosition)
    setInsightInitialTab(undefined)
    setInsightOpen(true)
  }, [])

  const openStockDetailAddPosition = useCallback((stockSymbol: string, stockMarket: string, stockName?: string) => {
    setInsightSymbol(stockSymbol)
    setInsightMarket(stockMarket || 'CN')
    setInsightName(stockName)
    setInsightHasPosition(true)
    setInsightExpandReducePosition(false)
    setInsightExpandAddPosition(true)
    setInsightInitialTab(undefined)
    setInsightOpen(true)
  }, [])

  const openStockDetailReducePosition = useCallback((stockSymbol: string, stockMarket: string, stockName?: string) => {
    setInsightSymbol(stockSymbol)
    setInsightMarket(stockMarket || 'CN')
    setInsightName(stockName)
    setInsightHasPosition(true)
    setInsightExpandAddPosition(false)
    setInsightExpandReducePosition(true)
    setInsightInitialTab(undefined)
    setInsightOpen(true)
  }, [])

  const openStockDetailReports = useCallback((stockSymbol: string, stockMarket: string, stockName?: string, hasPosition = true) => {
    setInsightExpandAddPosition(false)
    setInsightExpandReducePosition(false)
    setInsightSymbol(stockSymbol)
    setInsightMarket(stockMarket || 'CN')
    setInsightName(stockName)
    setInsightHasPosition(hasPosition)
    setInsightInitialTab('reports')
    setInsightOpen(true)
  }, [])

  const openStockDetailDeep = useCallback((stockSymbol: string, stockMarket: string, stockName?: string, hasPosition = true) => {
    setInsightExpandAddPosition(false)
    setInsightExpandReducePosition(false)
    setInsightSymbol(stockSymbol)
    setInsightMarket(stockMarket || 'CN')
    setInsightName(stockName)
    setInsightHasPosition(hasPosition)
    setInsightInitialTab('deep')
    setInsightOpen(true)
  }, [])

  const openLmdReportSection = useCallback((
    stockSymbol: string,
    stockMarket: string,
    stockName: string | undefined,
    section: LmdReportSection,
  ) => {
    setLmdSectionModal({
      symbol: stockSymbol,
      market: stockMarket || 'CN',
      name: stockName,
      section,
    })
  }, [])

  const openWatchlistBuy = useCallback((stockSymbol: string, stockMarket: string, stockName?: string) => {
    setInsightSymbol(stockSymbol)
    setInsightMarket(stockMarket || 'CN')
    setInsightName(stockName)
    setInsightHasPosition(false)
    setInsightExpandReducePosition(false)
    setInsightExpandAddPosition(true)
    setInsightInitialTab(undefined)
    setInsightOpen(true)
  }, [])

  useRestoreStockInsight(useCallback((payload) => {
    setInsightExpandAddPosition(false)
    setInsightExpandReducePosition(false)
    setInsightSymbol(payload.symbol)
    setInsightMarket(payload.market || 'CN')
    setInsightName(payload.name)
    setInsightHasPosition(!!payload.hasPosition)
    setInsightInitialTab(payload.tab || 'reports')
    setInsightOpen(true)
  }, []))

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
      refreshAnalysisBriefs(),
    ])
  }, [refreshQuotes, loadPoolSuggestions, refreshKlines, refreshAnalysisBriefs])

  useEffect(() => {
    load()
    loadPortfolio()
    loadPositionRecentTrades()
    loadClosedPositions()
    loadPoolSuggestions()
    loadPriceAlertSummaries()
    refreshKlines()
    refreshAnalysisBriefs()
  }, [])

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
    if (!stocks || stocks.length === 0) return
    const now = Date.now()
    const retryGapMs = 2 * 60 * 1000
    const missing = stocks.filter(s => {
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
      const url = '/agents/intraday/scan?analyze=true'
      await fetchAPI(url, { method: 'POST' })
      await loadPoolSuggestions()
      await refreshKlines()
      await refreshAnalysisBriefs()
      setLastRefreshTime(new Date())
    } catch (e) {
      console.error('扫描失败:', e)
      toast(e instanceof Error ? e.message : '扫描失败', 'error')
    } finally {
      setScanning(false)
    }
  }, [loadPoolSuggestions, refreshKlines, refreshAnalysisBriefs, toast])

  // 首次加载后，按需刷新 K 线摘要与建议池
  const initialKlineDone = useRef(false)
  useEffect(() => {
    if (portfolio && portfolio.accounts.length > 0 && !initialKlineDone.current) {
      initialKlineDone.current = true
      refreshKlines()
      refreshAnalysisBriefs()
      loadPoolSuggestions()
    }
  }, [portfolio, refreshKlines, refreshAnalysisBriefs, loadPoolSuggestions])

  // Auto-refresh timer
  useEffect(() => {
    if (autoRefresh) {
      refreshQuotes()
      refreshKlines()
      refreshAnalysisBriefs()
      loadPoolSuggestions()
      refreshTimerRef.current = setInterval(() => {
        refreshQuotes()
        refreshKlines()
        refreshAnalysisBriefs()
        loadPoolSuggestions()
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
  }, [autoRefresh, refreshInterval, refreshQuotes, refreshKlines, refreshAnalysisBriefs, loadPoolSuggestions])

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
    setStockForm({
      symbol: item.symbol,
      name: item.name,
      market: item.market,
      security_type: item.security_type || 'stock',
    })
    setSearchQuery(`${item.symbol} ${item.name}`)
    setShowDropdown(false)
  }

  const handleStockSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      await stocksApi.create(stockForm)
      setStockForm(emptyStockForm)
      setSearchQuery('')
      setShowStockForm(false)
      load()
      toast('股票已添加', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '添加股票失败', 'error')
    }
  }

  const hasAnyPositionForStockId = (id: number): boolean => {
    return (portfolio?.accounts || []).some(acc => (acc.positions || []).some(p => p.stock_id === id))
  }

  const watchlistStocks = useMemo(() => stocks, [stocks])

  const watchlistConceptTagOptions = useMemo(() => {
    const counts = new Map<string, number>()
    for (const stock of watchlistStocks) {
      for (const tag of stock.concept_tags || []) {
        const name = (tag.name || '').trim()
        if (!name) continue
        counts.set(name, (counts.get(name) || 0) + 1)
      }
    }
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'zh-CN'))
      .map(([name, count]) => ({ name, count }))
  }, [watchlistStocks])

  const watchlistChainOptions = useMemo(() => {
    const counts = new Map<string, { display: string; layer: string; count: number }>()
    for (const stock of watchlistStocks) {
      const chain = stock.industry_chain
      const key = stockChainFilterKey(chain)
      if (!key || !chain) continue
      const layer = LEGACY_CHAIN_LAYER_MAP[chain.layer] || chain.layer
      const prev = counts.get(key)
      counts.set(key, {
        display: formatIndustryChainDisplay(chain),
        layer,
        count: (prev?.count || 0) + 1,
      })
    }
    const layerOrder = CHAIN_LAYER_ORDER
    return Array.from(counts.entries())
      .map(([key, value]) => ({ key, ...value }))
      .sort((a, b) => (layerOrder[a.layer] ?? 99) - (layerOrder[b.layer] ?? 99) || a.display.localeCompare(b.display, 'zh-CN'))
  }, [watchlistStocks])

  // 产业链分层改版后，清除 localStorage 中已失效的筛选键
  useEffect(() => {
    if (!watchlistChainFilter) return
    const normalized = normalizeChainFilterKey(watchlistChainFilter)
    if (normalized !== watchlistChainFilter) {
      setWatchlistChainFilter(normalized)
      return
    }
    if (watchlistChainOptions.length === 0) {
      setWatchlistChainFilter('')
      return
    }
    const valid = new Set(watchlistChainOptions.map((opt) => opt.key))
    if (!valid.has(watchlistChainFilter)) {
      setWatchlistChainFilter('')
    }
  }, [watchlistChainFilter, watchlistChainOptions, setWatchlistChainFilter])

  useEffect(() => {
    if (!watchlistTagFilter) return
    if (watchlistConceptTagOptions.length === 0) {
      setWatchlistTagFilter('')
      return
    }
    const valid = new Set(watchlistConceptTagOptions.map((opt) => opt.name))
    if (!valid.has(watchlistTagFilter)) {
      setWatchlistTagFilter('')
    }
  }, [watchlistTagFilter, watchlistConceptTagOptions, setWatchlistTagFilter])

  const watchlistReorderDisabled = stockListFilter !== '' || watchlistOnlyAlerts || watchlistFeaturedOnly || watchlistTagFilter !== '' || watchlistChainFilter !== ''

  const toggleWatchlistFeatured = useCallback(async (stock: Stock) => {
    const next = !stock.is_featured
    setStocks(prev => prev.map(s => (
      s.id === stock.id ? { ...s, is_featured: next } : s
    )))
    try {
      const updated = await stocksApi.setFeatured(stock.id, next)
      setStocks(prev => prev.map(s => (
        s.id === updated.id ? { ...s, ...updated } : s
      )))
      toast(next ? '已置顶' : '已取消置顶', 'success')
    } catch (e) {
      setStocks(prev => prev.map(s => (
        s.id === stock.id ? { ...s, is_featured: stock.is_featured } : s
      )))
      toast(e instanceof Error ? e.message : '更新置顶状态失败', 'error')
    }
  }, [toast])

  const toggleWatchlistTagFilter = useCallback((name: string) => {
    setWatchlistTagFilter((prev) => (prev === name ? '' : name))
  }, [setWatchlistTagFilter])

  const toggleWatchlistChainFilter = useCallback((key: string) => {
    setWatchlistChainFilter((prev) => (prev === key ? '' : key))
  }, [setWatchlistChainFilter])

  const removeFromWatchlist = async (stock: Stock) => {
    if (hasAnyPositionForStockId(stock.id)) {
      toast('该股票存在持仓，请先删除持仓后再删除股票', 'error')
      return
    }

    setRemovingWatchStock(true)
    try {
      await stocksApi.remove(stock.id)
      toast('股票已删除', 'success')
      setRemoveWatchStock(null)
      load()
      // 价格提醒/关联配置会随股票删除，刷新一次避免 UI 残留。
      loadPortfolio()
    } catch (e) {
      toast(e instanceof Error ? e.message : '删除失败', 'error')
    } finally {
      setRemovingWatchStock(false)
    }
  }

  // ========== Account handlers ==========
  const openAccountDialog = (account?: Account) => {
    if (account) {
      setAccountForm({
        name: account.name,
        base_currency: (account.base_currency || 'CNY') as AccountForm['base_currency'],
        available_funds: account.available_funds.toString(),
        other_fund_items: (account.other_fund_items?.length
          ? account.other_fund_items
          : (account.other_funds ?? 0) > 0
            ? [{ label: '其他', amount: account.other_funds ?? 0 }]
            : []
        ).map(item => ({ label: item.label, amount: String(item.amount) })),
      })
      setEditAccountId(account.id)
    } else {
      setAccountForm(emptyAccountForm)
      setEditAccountId(null)
    }
    setAccountDialogOpen(true)
  }

  const handleAccountSubmit = async () => {
    try {
      const availableFunds = parseFloat(accountForm.available_funds) || 0
      const otherFundItems = accountForm.other_fund_items
        .map(item => ({
          label: item.label.trim(),
          amount: parseFloat(item.amount) || 0,
        }))
        .filter(item => item.label)
      const payload = {
        name: accountForm.name,
        base_currency: accountForm.base_currency,
        available_funds: availableFunds,
        other_fund_items: otherFundItems,
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
    } catch (e) {
      toast(e instanceof Error ? e.message : '保存账户失败', 'error')
    }
  }

  const handleDeleteAccount = async (id: number) => {
    if (!confirm('确定删除该账户？这将同时删除该账户的所有持仓记录')) return
    try {
      await fetchAPI(`/accounts/${id}`, { method: 'DELETE' })
      load()
      loadPortfolio()
      toast('账户已删除', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '删除账户失败', 'error')
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
        invested_amount: position.invested_amount?.toString()
          || calcPositionInitialFunds(position.cost_price.toString(), position.quantity.toString()),
        trading_style: position.trading_style || DEFAULT_TRADING_STYLE,
        trade_time: '',
        stock_symbol: position.symbol,
        stock_name: position.name,
        stock_market: position.market,
      })
      setEditPositionId(position.id)
      setEditPositionOriginal({ quantity: position.quantity, cost_price: position.cost_price })
    } else {
      setPositionForm({
        account_id: accountId,
        stock_id: 0,
        cost_price: '',
        quantity: '',
        invested_amount: '',
        trading_style: DEFAULT_TRADING_STYLE,
        trade_time: '',
        stock_symbol: '',
        stock_name: '',
        stock_market: 'CN',
      })
      setEditPositionId(null)
      setEditPositionOriginal(null)
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
        } catch {
          // 股票可能已存在，尝试获取（兼容并发创建/历史数据）。
          try {
            const existingStocks = await fetchAPI<Stock[]>('/stocks')
            const existing = existingStocks.find(s => s.symbol === positionForm.stock_symbol && s.market === positionForm.stock_market)
            if (existing) {
              stockId = existing.id
            } else {
              toast('添加股票失败', 'error')
              return
            }
          } catch (e) {
            toast(e instanceof Error ? e.message : '添加股票失败', 'error')
            return
          }
        }
      }

      const tradedAt = tradeDatetimeLocalToIso(positionForm.trade_time)
      const costPrice = parseFloat(positionForm.cost_price)
      const quantity = parseInt(positionForm.quantity)
      const payload = {
        account_id: positionForm.account_id,
        stock_id: stockId,
        cost_price: costPrice,
        quantity,
        trading_style: positionForm.trading_style,  // 空字符串表示清空
        ...(tradedAt ? { traded_at: tradedAt } : {}),
      }
      if (editPositionId) {
        const newQty = payload.quantity
        const oldQty = editPositionOriginal?.quantity
        const sym = positionForm.stock_symbol
        const quote = sym ? quotes[sym] : undefined
        const tradePrice = quote?.current_price && quote.current_price > 0
          ? quote.current_price
          : payload.cost_price

        if (oldQty != null && newQty !== oldQty && tradePrice > 0) {
          const diff = newQty - oldQty
          const tradeBody = {
            price: tradePrice,
            quantity: Math.abs(diff),
            ...(tradedAt ? { traded_at: tradedAt } : {}),
          }
          if (diff > 0) {
            await positionsApi.add(editPositionId, { ...tradeBody, note: '手动加仓' })
          } else {
            await positionsApi.reduce(editPositionId, { ...tradeBody, note: '手动减仓' })
          }
          if (payload.trading_style !== undefined) {
            await fetchAPI(`/positions/${editPositionId}`, {
              method: 'PUT',
              body: JSON.stringify({
                trading_style: payload.trading_style,
              }),
            })
          }
        } else {
          await fetchAPI(`/positions/${editPositionId}`, { method: 'PUT', body: JSON.stringify(payload) })
        }
      } else {
        await fetchAPI('/positions', { method: 'POST', body: JSON.stringify(payload) })
      }
      setPositionDialogOpen(false)
      setEditPositionOriginal(null)
      loadPortfolio()
      void loadPositionRecentTrades()
      if (!editPositionId && pageMode === 'watchlist') {
        toast('已建仓，可在持仓页查看', 'success')
      } else {
        toast(editPositionId ? '持仓已更新' : '持仓已添加', 'success')
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : '保存持仓失败', 'error')
    }
  }

  const handleDeletePosition = async (id: number) => {
    if (!confirm('确定删除该持仓？')) return
    try {
      await fetchAPI(`/positions/${id}`, { method: 'DELETE' })
      loadPortfolio()
      toast('持仓已删除', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '删除持仓失败', 'error')
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
      toast(e instanceof Error ? e.message : '更新 Agent 绑定失败', 'error')
    }
  }

  const triggerStockAgent = async (stockId: number, agentName: string) => {
    setTriggeringAgent(agentName)
    setRunningAgents(prev => ({ ...prev, [stockId]: agentName }))
    // 触发后立即关闭配置弹窗，避免多层弹窗干扰
    setAgentDialogStock(null)
    // 产业周期视角：Hermes 联网研究，同步等待（约 2–5 分钟），完成后弹窗展示
    const syncWait = agentName === 'lmd_outlook'
    const query = syncWait
      ? '?bypass_throttle=true&wait=true'
      : '?bypass_throttle=true'
    try {
      const resp = await fetchAPI<{
        result?: AgentResult
        queued?: boolean
        deduplicated?: boolean
        message?: string
        trace_id?: string
        success?: boolean
      }>(
        `/stocks/${stockId}/agents/${agentName}/trigger${query}`,
        { method: 'POST', timeoutMs: syncWait ? 720_000 : undefined },
      )

      if (resp?.deduplicated) {
        toast(resp.message || '报告生成中，请稍候', 'info')
        return
      }

      if (resp?.queued) {
        toast(
          resp.message || '已提交后台执行，约 1–2 分钟后可在侧边栏「历史」查看',
          'info',
        )
        return
      }

      const result = resp?.result
      if (result) {
        if (result.success === false) {
          toast(result.message || result.content || '执行未通过', 'info')
          return
        }
        const isSkipped = !!result.skipped || /已跳过执行|非交易时段/.test(result.content || '')
        if (isSkipped) {
          toast(result.content || '当前非交易时段，已跳过执行', 'info')
          return
        }
        if (agentName === 'lmd_outlook') {
          setAgentResultDialog({
            title: result.title || LMD_DISPLAY_NAME,
            content: result.content || '',
            should_alert: !!result.should_alert,
            notified: !!result.notified,
          })
          toast(`${LMD_DISPLAY_NAME}报告已生成，也可在「历史」中查看`, 'success')
          return
        }
        toast(result.should_alert ? 'AI 建议关注' : 'AI 判断无需关注', result.should_alert ? 'success' : 'info')
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : '触发失败'
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
      toast(e instanceof Error ? e.message : '更新 Agent 模型失败', 'error')
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
      toast(e instanceof Error ? e.message : '更新 Agent 通知配置失败', 'error')
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
      toast(e instanceof Error ? e.message : '更新 Agent 调度失败', 'error')
    }
  }

  // ========== Helpers ==========
  const formatMoney = (value: number) => {
    if (Math.abs(value) >= 10000) {
      return `${(value / 10000).toFixed(2)}万`
    }
    return value.toFixed(2)
  }

  const formatAccountFunds = (value: number, currency?: string) => {
    const suffix = currency && currency !== 'CNY' ? ` ${currency}` : ''
    return `${formatMoney(value)}${suffix}`
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

  const formatRecentTrade = (trade?: PortfolioRecentTrade) => {
    if (!trade) return null
    const isSell = trade.side === 'sell'
    const label = isSell ? '减仓' : '加仓'
    const sign = isSell ? '-' : '+'
    return `最近${label} ${sign}${trade.quantity} @ ${formatPrice(trade.price)}`
  }

  const formatCurrentCostPrice = (
    currentPrice: number | null | undefined,
    costPrice: number,
    changeColor: string,
    isForeign: boolean,
    market: string,
    quantity?: number,
  ) => {
    const suffix = isForeign ? (market === 'HK' ? ' HKD' : ' USD') : ''
    return (
      <span className="font-mono text-[12px]">
        {currentPrice != null ? (
          <>
            <span className={changeColor}>{formatPrice(currentPrice)}{suffix}</span>
            <span className="text-muted-foreground"> / {formatPrice(costPrice)}</span>
          </>
        ) : (
          <span className="text-muted-foreground">— / {formatPrice(costPrice)}</span>
        )}
        {quantity != null ? (
          <span className="text-muted-foreground"> / {quantity}</span>
        ) : null}
      </span>
    )
  }

  const getPositionInvestedAmount = (pos: Position) =>
    pos.invested_amount ?? pos.cost_price * pos.quantity

  const formatMarketValueInvested = (pos: Position, isForeign: boolean) => {
    const invested = getPositionInvestedAmount(pos)
    const suffix = isForeign ? (pos.market === 'HK' ? ' HKD' : ' USD') : ''
    const investedText = `${formatMoney(invested)}${suffix}`
    const marketValue = pos.market_value

    return (
      <div className="flex flex-col items-end font-mono text-[12px]">
        <span>
          {marketValue != null ? (
            <>
              <span className="text-foreground">{formatMoney(marketValue)}{suffix}</span>
              <span className="text-muted-foreground"> / {investedText}</span>
            </>
          ) : (
            <span className="text-muted-foreground">— / {investedText}</span>
          )}
        </span>
        {isForeign && pos.exchange_rate && marketValue != null && pos.market_value_cny != null ? (
          <span className="text-[10px] text-muted-foreground/60">
            ≈{formatMoney(pos.market_value_cny)} / {formatMoney(invested * pos.exchange_rate)}
          </span>
        ) : null}
      </div>
    )
  }

  const openPositionTradesDialog = async (pos: Position, accountName: string) => {
    setPositionTradesDialog({
      positionId: pos.id,
      symbol: pos.symbol,
      name: pos.name,
      accountName,
      market: pos.market,
    })
    setEditingTradeId(null)
    setPositionTradesLoading(true)
    setPositionTrades([])
    try {
      const rows = await positionsApi.trades(pos.id, 100)
      setPositionTrades(rows)
    } catch {
      toast('加载交易记录失败', 'error')
    } finally {
      setPositionTradesLoading(false)
    }
  }

  const startEditTrade = (trade: PositionTrade) => {
    setEditingTradeId(trade.id)
    setTradeEditForm({
      side: trade.side === 'sell' ? 'sell' : 'buy',
      price: String(trade.price),
      quantity: String(trade.quantity),
      traded_at: tradeDatetimeIsoToLocal(trade.traded_at),
      note: trade.note || '',
    })
  }

  const cancelEditTrade = () => {
    setEditingTradeId(null)
  }

  const saveEditTrade = async () => {
    if (editingTradeId == null) return
    const price = Number(tradeEditForm.price)
    const quantity = Number.parseInt(tradeEditForm.quantity, 10)
    if (!Number.isFinite(price) || price <= 0) {
      toast('请输入有效成交价格', 'error')
      return
    }
    if (!Number.isFinite(quantity) || quantity <= 0) {
      toast('请输入有效股数', 'error')
      return
    }
    setTradeEditSaving(true)
    try {
      const res = await positionsApi.updateTrade(editingTradeId, {
        side: tradeEditForm.side,
        price,
        quantity,
        traded_at: tradeDatetimeLocalToIso(tradeEditForm.traded_at),
        note: tradeEditForm.note.trim() || undefined,
      })
      setPositionTrades(res.trades)
      setEditingTradeId(null)
      loadPortfolio()
      void loadPositionRecentTrades()
      toast('交易记录已更新', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '保存失败', 'error')
    } finally {
      setTradeEditSaving(false)
    }
  }

  const openPositionChat = (
    pos: Position,
    accountName: string,
    suggestion: SuggestionInfo | null,
    kline: KlineSummary | null,
    initialMessage?: string,
  ) => {
    const parts: string[] = ['来源：持仓页']
    parts.push(`账户：${accountName}`)
    const styleLabel = tradingStyleLabel(pos.trading_style)
    const holdingItems = [
      `持仓 ${pos.quantity} 股`,
      `成本 ${formatPrice(pos.cost_price)}`,
      pos.current_price != null ? `现价 ${formatPrice(pos.current_price)}` : null,
      pos.change_pct != null ? `涨跌幅 ${pos.change_pct >= 0 ? '+' : ''}${pos.change_pct.toFixed(2)}%` : null,
      pos.market_value != null ? `市值 ${formatMoney(pos.market_value)}` : null,
      pos.pnl != null ? `浮动盈亏 ${pos.pnl >= 0 ? '+' : ''}${formatMoney(pos.pnl)}${pos.pnl_pct != null ? ` (${pos.pnl_pct >= 0 ? '+' : ''}${pos.pnl_pct.toFixed(2)}%)` : ''}` : null,
      `风格 ${styleLabel}`,
    ].filter(Boolean)
    parts.push(`持仓信息：${holdingItems.join('，')}`)
    appendKlineSuggestionContext(parts, suggestion, kline)
    appendChanEmotionContext(parts, pos.symbol, pos.market)
    appendAnalysisBriefContext(parts, pos.symbol, pos.market)
    dispatchStockChat({
      symbol: pos.symbol,
      market: pos.market,
      stockName: pos.name,
      pageContext: parts.join('\n'),
      initialMessage,
    })
  }

  const openWatchlistChat = (
    stock: Stock,
    isHolding: boolean,
    suggestion: SuggestionInfo | null,
    kline: KlineSummary | null,
    initialMessage?: string,
  ) => {
    const parts: string[] = ['来源：自选页']
    if (isHolding) parts.push('状态：已持仓')
    const quote = getStockQuote(`${stock.market}:${stock.symbol}`)
    if (quote?.current_price != null) {
      const quoteItems = [`现价 ${quote.current_price.toFixed(2)}`]
      if (quote.change_pct != null) {
        quoteItems.push(`涨跌幅 ${quote.change_pct >= 0 ? '+' : ''}${quote.change_pct.toFixed(2)}%`)
      }
      parts.push(`行情：${quoteItems.join('，')}`)
    }
    appendKlineSuggestionContext(parts, suggestion, kline)
    appendChanEmotionContext(parts, stock.symbol, stock.market)
    appendAnalysisBriefContext(parts, stock.symbol, stock.market)
    dispatchStockChat({
      symbol: stock.symbol,
      market: stock.market,
      stockName: stock.name,
      pageContext: parts.join('\n'),
      initialMessage,
    })
  }

  const appendChanEmotionContext = (parts: string[], symbol: string, market: string) => {
    const chan = getChanEmotionForStock(symbol, market)
    if (!chan) return
    parts.push(`缠论结论：${chan.action_label}，赢面 ${chan.win_rate}%，${chan.emotion_label.split('（')[0]}`)
  }

  const appendAnalysisBriefContext = (parts: string[], symbol: string, market: string) => {
    const key = `${market || 'CN'}:${symbol}`
    const brief = analysisBriefMap[key]
    if (brief?.lmd_brief) {
      parts.push(brief.lmd_brief)
    } else {
      const lmdFallback = formatLmdBriefFromSnapshot(lmdSnapshots[key])
      if (lmdFallback) parts.push(lmdFallback)
    }
    if (brief?.deep_brief) parts.push(brief.deep_brief)
  }

  const dispatchStockChat = (detail: {
    symbol: string
    market: string
    stockName: string
    pageContext: string
    initialMessage?: string
  }) => {
    window.dispatchEvent(new CustomEvent('panwatch-open-chat', { detail }))
  }

  // 获取股票的行情信息
  const getStockQuote = (quoteKey: string) => {
    return quotes[quoteKey] || null
  }

  const getChanEmotionForStock = (symbol: string, market: string) => {
    return chanEmotionMap[`${market || 'CN'}:${symbol}`] || null
  }

  const isChanEmotionLoading = (symbol: string, market: string) => {
    return !!chanEmotionLoadingKeys[`${market || 'CN'}:${symbol}`]
  }

  const getPriceAlertSummary = (symbol: string, market: string) => {
    const key = `${String(market || 'CN').toUpperCase()}:${String(symbol || '').toUpperCase()}`
    return priceAlertSummaryMap[key] || { total: 0, enabled: 0 }
  }

  // 获取股票的建议信息（优先使用建议池，包含来源和时间信息）
  const getSuggestionForStock = (symbol: string, market: string, hasPosition?: boolean): { suggestion: SuggestionInfo | null; kline: KlineSummary | null } => {
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

  const rollingCostBriefMap = useMemo(() => {
    const out = new Map<number, string>()
    for (const account of portfolio?.accounts || []) {
      for (const position of account.positions || []) {
        const key = `${position.market || 'CN'}:${position.symbol}`
        const kline = klineSummaries[key] || (suggestions[position.symbol]?.kline as KlineSummary | null) || null
        const brief = buildRollingCostPlanBrief(buildRollingCostPlan({
          market: position.market,
          currentQuantity: Number(position.quantity || 0),
          currentCost: Number(position.cost_price || 0),
          currentPrice: position.current_price,
          kline,
          baseRatio: 0.5,
          tranches: 3,
          reboundPct: 5,
        }))
        if (brief) out.set(position.id, brief)
      }
    }
    return out
  }, [klineSummaries, portfolio?.accounts, suggestions])

  const watchlistCount = useMemo(() => watchlistStocks.length, [watchlistStocks])

  const positionRatio = useMemo(() => {
    if (!portfolio) return null
    const mv = portfolio.total.total_market_value || 0
    const assets = portfolio.total.total_assets || 0
    const pct = assets > 0 ? (mv / assets * 100) : 0
    return { mv, assets, pct }
  }, [portfolio])

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
          <h1 className="text-[18px] md:text-[22px] font-bold text-foreground tracking-tight shrink-0">
            {pageMode === 'watchlist' ? '关注' : '持仓'}
          </h1>
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
              {(poolSuggestionsLoading || Object.keys(poolSuggestions).length > 0) && (
                <>
                  <div className="w-px h-4 bg-border" />
                  <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                    {poolSuggestionsLoading && (
                      <span className="w-3 h-3 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
                    )}
                    {!poolSuggestionsLoading && Object.keys(poolSuggestions).length > 0 && (
                      <span className="text-[10px] text-primary">
                        {Object.keys(poolSuggestions).length}
                      </span>
                    )}
                  </div>
                </>
              )}
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
            {pageMode === 'positions' ? (
              <Button variant="secondary" onClick={() => openAccountDialog()}>
                <Building2 className="w-4 h-4" /> 添加账户
              </Button>
            ) : null}
            {pageMode === 'watchlist' ? (
              <Button onClick={() => { setStockForm(emptyStockForm); setSearchQuery(''); setShowStockForm(true) }}>
                <Plus className="w-4 h-4" /> 添加股票
              </Button>
            ) : null}
          </div>
          {/* Mobile buttons */}
          <div className="flex md:hidden items-center gap-1.5">
            <Button variant="secondary" size="sm" className="h-8 w-8 p-0" onClick={handleRefresh} disabled={quotesLoading}>
              <RefreshCw className={`w-4 h-4 ${quotesLoading ? 'animate-spin' : ''}`} />
            </Button>
            <Button variant="secondary" size="sm" className="h-8 w-8 p-0" onClick={scanAndReload} disabled={scanning}>
              <Bot className="w-4 h-4" />
            </Button>
            {pageMode === 'positions' ? (
              <Button variant="secondary" size="sm" className="h-8 w-8 p-0" onClick={() => openAccountDialog()}>
                <Building2 className="w-4 h-4" />
              </Button>
            ) : null}
            {pageMode === 'watchlist' ? (
              <Button size="sm" className="h-8 w-8 p-0" onClick={() => { setStockForm(emptyStockForm); setSearchQuery(''); setShowStockForm(true) }}>
                <Plus className="w-4 h-4" />
              </Button>
            ) : null}
          </div>
        </div>

        {/* 移动端 row 2：市场状态 + 自动刷新 + 时间戳合并到同一行,横向滚动避免换行；桌面端只展示市场 pills (auto-refresh 在桌面顶部已展示) */}
        <div className="flex items-center gap-1.5 overflow-x-auto scrollbar-none -mx-1 px-1 md:flex-wrap md:overflow-visible">
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
                className="shrink-0 flex items-center gap-1 md:gap-1.5"
                title={`${m.sessions.join(', ')} (${m.local_time}) · ${m.status_text}`}
              >
                <span className={`w-1.5 h-1.5 rounded-full ${statusColors[m.status] || 'bg-slate-400'}`} />
                <span className="text-[11px] text-muted-foreground">{m.name}</span>
                <span className={`text-[10px] ${m.is_trading ? 'text-emerald-600' : 'text-muted-foreground/60'} hidden sm:inline`}>
                  {m.status_text}
                </span>
              </div>
            )
          })}
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
                  <SelectItem value="60">1分钟</SelectItem>
                  <SelectItem value="120">2分钟</SelectItem>
                </SelectContent>
              </Select>
            ) : (
              <span className="text-[10px] text-muted-foreground">自动刷新</span>
            )}
            {poolSuggestionsLoading && (
              <span className="w-2.5 h-2.5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
            )}
          </div>
          {lastRefreshTime && (
            <span className="md:hidden shrink-0 text-[10px] text-muted-foreground/60 font-mono ml-1">
              {lastRefreshTime.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
          )}
        </div>
      </div>

      {/* Portfolio Total Summary */}
      {pageMode === 'positions' && (portfolioLoading && !portfolio ? (
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
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-8 gap-4 mb-6">
          <div className="card p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Landmark className="w-4 h-4" />
              <span className="text-[12px]">初始资金</span>
            </div>
            <div className="text-[20px] font-bold text-foreground font-mono">
              {formatMoney(portfolio.total.initial_funds ?? (portfolio.total.total_assets - portfolio.total.total_pnl))}
            </div>
            <div className="mt-1 text-[11px] text-muted-foreground">折合人民币</div>
          </div>
          <div className="card p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <PiggyBank className="w-4 h-4" />
              <span className="text-[12px]">总资产</span>
            </div>
            <div className="text-[20px] font-bold text-foreground font-mono">
              {formatMoney(portfolio.total.total_assets)}
            </div>
            <div className="mt-1 text-[11px] text-muted-foreground line-clamp-1">
              {(portfolio.total.other_funds ?? 0) > 0
                ? `含其他资产 ${formatMoney(portfolio.total.other_funds ?? 0)}`
                : '折合人民币'}
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
            <div className="mt-1 text-[11px] text-muted-foreground">相对初始资金</div>
          </div>
          <div className="card p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <TrendingUp className="w-4 h-4" />
              <span className="text-[12px]">总市值</span>
            </div>
            <div className="text-[20px] font-bold text-foreground font-mono">
              {formatMoney(portfolio.total.total_market_value)}
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
              <span className="text-[12px]">股票现金</span>
            </div>
            <div className="text-[20px] font-bold text-foreground font-mono">
              {formatMoney(portfolio.total.available_funds)}
            </div>
            <div className="mt-1 text-[11px] text-muted-foreground">可用于买股票，折合人民币</div>
          </div>
          {(portfolio.total.other_funds ?? 0) > 0 && (
            <div className="card p-4">
              <div className="flex items-center gap-2 text-muted-foreground mb-1">
                <PieChart className="w-4 h-4" />
                <span className="text-[12px]">其他资产</span>
              </div>
              <div className="text-[20px] font-bold text-foreground font-mono">
                {formatMoney(portfolio.total.other_funds ?? 0)}
              </div>
              <div className="mt-1 text-[11px] text-muted-foreground">折合人民币</div>
            </div>
          )}
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
      ) : null)}

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
                      {item.security_type === 'etf' && (
                        <Badge variant="outline" className="text-[10px]">ETF</Badge>
                      )}
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
      {pageMode === 'positions' && (
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
                <div className="flex items-center justify-between md:justify-end gap-2 md:gap-6 pl-6 md:pl-0">
                  <div className="flex items-center gap-2.5 md:gap-6 min-w-0">
                    <div className="text-left md:text-right">
                      <div className="text-[10px] md:text-[11px] text-muted-foreground">市值</div>
                      <div className="text-[12px] md:text-[13px] font-mono font-medium whitespace-nowrap">{formatMoney(account.total_market_value)}</div>
                    </div>
                    <div className="text-left md:text-right">
                      <div className="text-[10px] md:text-[11px] text-muted-foreground">初始</div>
                      <div className="text-[12px] md:text-[13px] font-mono whitespace-nowrap">
                        {formatAccountFunds(account.initial_funds ?? 0, account.base_currency)}
                      </div>
                    </div>
                    <div className="text-left md:text-right">
                      <div className="text-[10px] md:text-[11px] text-muted-foreground">盈亏</div>
                      <div className={`text-[12px] md:text-[13px] font-mono font-medium whitespace-nowrap ${account.total_pnl >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                        {account.total_pnl >= 0 ? '+' : ''}{formatMoney(account.total_pnl)}
                        <span className="text-[10px] md:text-[11px] ml-1 hidden md:inline">({account.total_pnl_pct >= 0 ? '+' : ''}{account.total_pnl_pct.toFixed(2)}%)</span>
                      </div>
                    </div>
                    <div className="text-left md:text-right">
                      <div className="text-[10px] md:text-[11px] text-muted-foreground">今日</div>
                      <div className={`text-[12px] md:text-[13px] font-mono font-medium whitespace-nowrap ${account.total_daily_pnl >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                        {account.total_daily_pnl >= 0 ? '+' : ''}{formatMoney(account.total_daily_pnl)}
                      </div>
                    </div>
                    <div className="text-left md:text-right hidden sm:block">
                      <div className="text-[10px] md:text-[11px] text-muted-foreground">股票现金</div>
                      <div className="text-[12px] md:text-[13px] font-mono whitespace-nowrap">
                        {formatAccountFunds(account.available_funds, account.base_currency)}
                      </div>
                    </div>
                    {(account.other_funds ?? 0) > 0 && (
                      <div className="text-left md:text-right hidden md:block">
                        <div className="text-[10px] md:text-[11px] text-muted-foreground">其他</div>
                        <div
                          className="text-[12px] md:text-[13px] font-mono whitespace-nowrap"
                          title={formatOtherFundSummary(account.other_fund_items, account.base_currency, formatAccountFunds)}
                        >
                          {formatAccountFunds(account.other_funds ?? 0, account.base_currency)}
                        </div>
                        {account.other_fund_items && account.other_fund_items.length > 0 && (
                          <div className="text-[9px] text-muted-foreground/80 truncate max-w-[120px]">
                            {account.other_fund_items.map(item => item.label).join('、')}
                          </div>
                        )}
                      </div>
                    )}
                    <div className="text-left md:text-right">
                      <div className="text-[10px] md:text-[11px] text-muted-foreground">总资产</div>
                      <div className="text-[12px] md:text-[13px] font-mono font-medium whitespace-nowrap">{formatMoney(account.total_assets)}</div>
                    </div>
                  </div>
                  <div className="flex items-center gap-0 md:gap-1 shrink-0" onClick={e => e.stopPropagation()}>
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
                              <th className="text-right px-4 py-2 text-[11px] font-semibold text-muted-foreground">现价/成本/持仓</th>
                              <th className="text-right px-4 py-2 text-[11px] font-semibold text-muted-foreground">市值/总资金</th>
                              <th className="text-right px-4 py-2 text-[11px] font-semibold text-muted-foreground">总盈亏</th>
                              <th className="text-right px-4 py-2 text-[11px] font-semibold text-muted-foreground">今日盈亏</th>
                              <th className="text-center px-4 py-2 text-[11px] font-semibold text-muted-foreground">风格</th>
                              <th className="text-left px-4 py-2 text-[11px] font-semibold text-muted-foreground min-w-[8rem]">Agent</th>
                              <th className="text-center px-4 py-2 text-[11px] font-semibold text-muted-foreground">操作</th>
                            </tr>
                          </thead>
                          <tbody>
                            {account.positions.map((pos, i) => {
                              const stock = stocks.find(s => s.id === pos.stock_id)
                              const { suggestion, kline } = getSuggestionForStock(pos.symbol, pos.market, true)
                              const rollingBrief = rollingCostBriefMap.get(pos.id) || null
                              const badge = marketBadge(pos.market)
                              const isForeign = pos.market === 'HK' || pos.market === 'US'
                              const changeColor = pos.change_pct != null
                                ? (pos.change_pct > 0 ? 'text-rose-500' : pos.change_pct < 0 ? 'text-emerald-500' : 'text-muted-foreground')
                                : 'text-muted-foreground'
                              const pnlColor = pos.pnl != null
                                ? (pos.pnl > 0 ? 'text-rose-500' : pos.pnl < 0 ? 'text-emerald-500' : 'text-muted-foreground')
                                : 'text-muted-foreground'
                              const recentTrade = positionRecentTrades[pos.id]
                              const recentTradeLabel = formatRecentTrade(recentTrade)
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
                                    {(suggestion || kline) ? (
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
                                    ) : null}
                                    <div className="mt-1">
                                      <ChanEmotionBrief
                                        data={getChanEmotionForStock(pos.symbol, pos.market)}
                                        loading={isChanEmotionLoading(pos.symbol, pos.market)}
                                        onRequestLoad={() => fetchChanEmotionForStock(pos.symbol, pos.market, true)}
                                        onClick={() => openStockDetail(pos.symbol, pos.market, pos.name, true)}
                                      />
                                    </div>
                                    <div className="mt-1">
                                      <StockTradingAskButtons
                                        stockName={pos.name}
                                        hasPosition
                                        onAsk={(question) => openPositionChat(pos, account.name, suggestion, kline, question)}
                                      />
                                    </div>
                                    {rollingBrief && (
                                      <button
                                        type="button"
                                        className="mt-1 block max-w-[320px] truncate text-left text-[10px] text-muted-foreground hover:text-primary"
                                        title={rollingBrief}
                                        onClick={() => openStockDetail(pos.symbol, pos.market, pos.name, true)}
                                      >
                                        {rollingBrief}
                                      </button>
                                    )}
                                  </td>
                                  <td className="px-4 py-2.5 text-right">
                                    {formatCurrentCostPrice(pos.current_price, pos.cost_price, changeColor, isForeign, pos.market, pos.quantity)}
                                    {recentTradeLabel ? (
                                      <span className="block text-[9px] text-primary/80 font-sans mt-0.5">{recentTradeLabel}</span>
                                    ) : null}
                                  </td>
                                  <td className="px-4 py-2.5 text-right">
                                    {formatMarketValueInvested(pos, isForeign)}
                                  </td>
                                  <td className={`px-4 py-2.5 text-right font-mono text-[12px] ${pnlColor}`}>
                                    {pos.pnl != null ? (
                                      <div className="flex flex-col items-end">
                                        <span>{pos.pnl >= 0 ? '+' : ''}{formatMoney(pos.pnl)}</span>
                                        <span className="text-[10px] opacity-70">{pos.pnl_pct != null ? `${pos.pnl_pct >= 0 ? '+' : ''}${pos.pnl_pct.toFixed(2)}%` : ''}{isForeign && ' CNY'}</span>
                                      </div>
                                    ) : '—'}
                                  </td>
                                  <td className={`px-4 py-2.5 text-right font-mono text-[12px] ${pos.daily_pnl != null ? (pos.daily_pnl >= 0 ? 'text-rose-500' : 'text-emerald-500') : ''}`}>
                                    {pos.daily_pnl != null ? (
                                      <div className="flex flex-col items-end">
                                        <span>{pos.daily_pnl >= 0 ? '+' : ''}{formatMoney(pos.daily_pnl)}</span>
                                        <span className="text-[10px] opacity-70">{pos.daily_pnl_pct != null ? `${pos.daily_pnl_pct >= 0 ? '+' : ''}${pos.daily_pnl_pct.toFixed(2)}%` : ''}</span>
                                      </div>
                                    ) : '—'}
                                  </td>
                                  <td className="px-4 py-2.5 text-center">
                                    <span className={`text-[10px] px-1.5 py-0.5 rounded ${tradingStyleClass(pos.trading_style)}`}>
                                      {tradingStyleLabel(pos.trading_style)}
                                    </span>
                                  </td>
                                  <td className="px-4 py-2.5 align-top min-w-[8rem]">
                                    {stock ? (
                                      <PositionAgentBadges
                                        stockAgents={stock.agents}
                                        agentConfigs={agents}
                                        runningAgentName={runningAgents[stock.id]}
                                      />
                                    ) : null}
                                  </td>
                                  <td className="px-3 py-2 text-center align-top min-w-[11rem]">
                                    <PositionRowActions
                                      stockId={pos.stock_id}
                                      symbol={pos.symbol}
                                      market={pos.market}
                                      stockName={pos.name}
                                      showKline={!suggestion && !kline}
                                      onKline={() => openKlineDialog(pos.symbol, pos.market, pos.name, true)}
                                      onReports={() => openStockDetailReports(pos.symbol, pos.market, pos.name)}
                                      onAnalysis={() => openStockDetailDeep(pos.symbol, pos.market, pos.name)}
                                      onHistory={() => openPositionTradesDialog(pos, account.name)}
                                      onAskAI={() => openPositionChat(pos, account.name, suggestion, kline)}
                                      onAdd={() => openStockDetailAddPosition(pos.symbol, pos.market, pos.name)}
                                      onReduce={() => openStockDetailReducePosition(pos.symbol, pos.market, pos.name)}
                                      onAgentConfig={stock ? () => setAgentDialogStock(stock) : undefined}
                                      onNews={() => openNewsDialog(pos.name)}
                                      onEdit={() => openPositionDialog(account.id, pos)}
                                      onDelete={() => handleDeletePosition(pos.id)}
                                      onPriceAlertChanged={loadPriceAlertSummaries}
                                      getPriceAlertSummary={getPriceAlertSummary}
                                    />
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
                          const { suggestion, kline } = getSuggestionForStock(pos.symbol, pos.market, true)
                          const rollingBrief = rollingCostBriefMap.get(pos.id) || null
                          const badge = marketBadge(pos.market)
                          const isForeign = pos.market === 'HK' || pos.market === 'US'
                          const changeColor = pos.change_pct != null
                            ? (pos.change_pct > 0 ? 'text-rose-500' : pos.change_pct < 0 ? 'text-emerald-500' : 'text-muted-foreground')
                            : 'text-muted-foreground'
                          const pnlColor = pos.pnl != null
                            ? (pos.pnl > 0 ? 'text-rose-500' : pos.pnl < 0 ? 'text-emerald-500' : 'text-muted-foreground')
                            : 'text-muted-foreground'
                          const recentTradeLabel = formatRecentTrade(positionRecentTrades[pos.id])
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
                                  <span className={`shrink-0 text-[9px] px-1 py-0.5 rounded ${tradingStyleClass(pos.trading_style)}`}>
                                    {tradingStyleLabel(pos.trading_style, true)}
                                  </span>
                                </div>
                                <div className={`font-mono text-[13px] font-medium whitespace-nowrap shrink-0 text-right ${changeColor}`}>
                                  <div>
                                    {pos.current_price != null ? (
                                      <>
                                        <span>{formatPrice(pos.current_price)}</span>
                                        <span className="text-muted-foreground"> / {formatPrice(pos.cost_price)} / {pos.quantity}</span>
                                      </>
                                    ) : (
                                      <span className="text-muted-foreground">— / {formatPrice(pos.cost_price)} / {pos.quantity}</span>
                                    )}
                                  </div>
                                  {recentTradeLabel ? (
                                    <div className="text-[9px] text-primary/80 font-sans">{recentTradeLabel}</div>
                                  ) : null}
                                  <KlineLevelsBrief kline={kline} align="right" className="mt-0.5" />
                                </div>
                              </div>
                              {/* Row 2 (Suggestion badge, dedicated row to avoid wrapping mess) */}
                              {(suggestion || kline) ? (
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
                              ) : null}
                              <div className="mb-2">
                                <ChanEmotionBrief
                                  data={getChanEmotionForStock(pos.symbol, pos.market)}
                                  loading={isChanEmotionLoading(pos.symbol, pos.market)}
                                  onRequestLoad={() => fetchChanEmotionForStock(pos.symbol, pos.market, true)}
                                  onClick={() => openStockDetail(pos.symbol, pos.market, pos.name, true)}
                                />
                              </div>
                              <div className="mb-2">
                                <StockTradingAskButtons
                                  stockName={pos.name}
                                  hasPosition
                                  onAsk={(question) => openPositionChat(pos, account.name, suggestion, kline, question)}
                                />
                              </div>
                              {rollingBrief && (
                                <button
                                  type="button"
                                  className="mb-2 w-full rounded bg-accent/15 px-2 py-1 text-left text-[10px] text-muted-foreground"
                                  onClick={() => openStockDetail(pos.symbol, pos.market, pos.name, true)}
                                >
                                  {rollingBrief}
                                </button>
                              )}
                              {/* Row 3: Stats grid */}
                              <div className="grid grid-cols-2 gap-2 text-[11px]">
                                <div className="min-w-0 col-span-2">
                                  <div className="text-[10px] text-muted-foreground">市值/总资金</div>
                                  <div className="font-mono text-foreground whitespace-nowrap text-[11px]">
                                    {pos.market_value != null ? (
                                      <>
                                        {formatMoney(pos.market_value)}
                                        {isForeign ? (pos.market === 'HK' ? ' HKD' : ' USD') : ''}
                                        <span className="text-muted-foreground"> / {formatMoney(getPositionInvestedAmount(pos))}{isForeign ? (pos.market === 'HK' ? ' HKD' : ' USD') : ''}</span>
                                      </>
                                    ) : (
                                      <span className="text-muted-foreground">— / {formatMoney(getPositionInvestedAmount(pos))}{isForeign ? (pos.market === 'HK' ? ' HKD' : ' USD') : ''}</span>
                                    )}
                                  </div>
                                  {isForeign && pos.exchange_rate && pos.market_value_cny != null && (
                                    <div className="text-[9px] text-muted-foreground">
                                      ≈{formatMoney(pos.market_value_cny)} / {formatMoney(getPositionInvestedAmount(pos) * pos.exchange_rate)}
                                    </div>
                                  )}
                                </div>
                                <div className="min-w-0">
                                  <div className="text-[10px] text-muted-foreground">总盈亏</div>
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
                                  <div className="text-[10px] text-muted-foreground">今日盈亏</div>
                                  <div className={`font-mono whitespace-nowrap ${pos.daily_pnl != null ? (pos.daily_pnl >= 0 ? 'text-rose-500' : 'text-emerald-500') : 'text-muted-foreground'}`}>
                                    {pos.daily_pnl != null ? `${pos.daily_pnl >= 0 ? '+' : ''}${formatMoney(pos.daily_pnl)}` : '—'}
                                  </div>
                                </div>
                              </div>
                              {/* Row 4: Actions */}
                              <div className="mt-2 pt-2 border-t border-border/20 space-y-2">
                                {stock ? (
                                  <PositionAgentBadges
                                    stockAgents={stock.agents}
                                    agentConfigs={agents}
                                    runningAgentName={runningAgents[stock.id]}
                                  />
                                ) : null}
                                <PositionRowActions
                                  compact
                                  align="end"
                                  stockId={pos.stock_id}
                                  symbol={pos.symbol}
                                  market={pos.market}
                                  stockName={pos.name}
                                  showKline={!suggestion && !kline}
                                  onKline={() => openKlineDialog(pos.symbol, pos.market, pos.name, true)}
                                  onReports={() => openStockDetailReports(pos.symbol, pos.market, pos.name)}
                                  onAnalysis={() => openStockDetailDeep(pos.symbol, pos.market, pos.name)}
                                  onHistory={() => openPositionTradesDialog(pos, account.name)}
                                  onAskAI={() => openPositionChat(pos, account.name, suggestion, kline)}
                                  onAdd={() => openStockDetailAddPosition(pos.symbol, pos.market, pos.name)}
                                  onReduce={() => openStockDetailReducePosition(pos.symbol, pos.market, pos.name)}
                                  onAgentConfig={stock ? () => setAgentDialogStock(stock) : undefined}
                                  onNews={() => openNewsDialog(pos.name)}
                                  onEdit={() => openPositionDialog(account.id, pos)}
                                  onDelete={() => handleDeletePosition(pos.id)}
                                  onPriceAlertChanged={loadPriceAlertSummaries}
                                  getPriceAlertSummary={getPriceAlertSummary}
                                />
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

          {/* 已清仓记录 */}
          {closedPositions.length > 0 && (
            <div className="card overflow-hidden">
              <div className="flex items-center justify-between p-3 md:p-4 border-b border-border/50">
                <div className="flex items-center gap-2">
                  <Archive className="w-4 h-4 text-muted-foreground" />
                  <span className="text-[14px] md:text-[15px] font-semibold text-foreground">已清仓</span>
                  <span className="text-[11px] md:text-[12px] text-muted-foreground">{closedPositions.length} 只</span>
                </div>
                <Button variant="ghost" size="sm" className="h-7 px-2 text-[11px] text-muted-foreground" onClick={() => loadClosedPositions()}>
                  <RefreshCw className={`w-3 h-3 mr-1 ${closedPositionsLoading ? 'animate-spin' : ''}`} />
                  刷新
                </Button>
              </div>
              <div className="divide-y divide-border/40">
                {closedPositions.map(pos => {
                  const pnl = pos.realized_pnl || 0
                  const pnlPct = pos.invested_amount && pos.invested_amount > 0 ? (pnl / pos.invested_amount) * 100 : 0
                  return (
                    <div key={pos.id} className="flex items-center justify-between gap-3 p-3 md:p-4 hover:bg-accent/20 transition-colors">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="text-[13px] md:text-[14px] font-medium text-foreground truncate">{pos.stock_name || pos.stock_symbol}</span>
                          <Badge variant="secondary" className="text-[10px]">{marketLabel(pos.market || 'CN')}</Badge>
                          <span className="text-[11px] text-muted-foreground shrink-0">{pos.account_name}</span>
                        </div>
                        <div className="text-[11px] text-muted-foreground mt-1">
                          清仓于 {pos.closed_at ? new Date(pos.closed_at).toLocaleDateString('zh-CN') : '-'}
                          <span className="ml-2">建仓成本 {pos.cost_price.toFixed(4)}</span>
                          <span className="ml-2">{pos.trades.length} 笔成交</span>
                        </div>
                      </div>
                      <div className="flex items-center gap-3 shrink-0">
                        <div className="text-right">
                          <div className="text-[11px] text-muted-foreground">实现盈亏</div>
                          <div className={`text-[13px] md:text-[14px] font-mono font-medium ${pnl >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                            {pnl >= 0 ? '+' : ''}{formatMoney(pnl)}
                            <span className="text-[10px] ml-1">({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%)</span>
                          </div>
                        </div>
                        <Button variant="outline" size="sm" className="h-7 px-2 text-[11px]" onClick={() => setClosedTradesDialog(pos)}>
                          成交明细
                        </Button>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
        )
      )}

      {/* 已清仓成交明细弹窗 */}
      <Dialog open={!!closedTradesDialog} onOpenChange={(open) => { if (!open) setClosedTradesDialog(null) }}>
        <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>
              {closedTradesDialog?.stock_name || closedTradesDialog?.stock_symbol} · 历史成交明细
            </DialogTitle>
            <DialogDescription>
              {closedTradesDialog?.account_name} · 共 {closedTradesDialog?.trades.length || 0} 笔成交
              {closedTradesDialog && closedTradesDialog.realized_pnl !== 0 && (
                <span className="ml-2">
                  实现盈亏
                  <span className={`ml-1 font-medium ${closedTradesDialog.realized_pnl >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                    {closedTradesDialog.realized_pnl >= 0 ? '+' : ''}{formatMoney(closedTradesDialog.realized_pnl)}
                  </span>
                </span>
              )}
            </DialogDescription>
          </DialogHeader>
          {closedTradesDialog && closedTradesDialog.trades.length === 0 ? (
            <div className="text-center text-muted-foreground text-sm py-8">暂无成交记录</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-muted-foreground text-xs">
                    <th className="text-left py-2 pr-3">时间</th>
                    <th className="text-left py-2 px-2">方向</th>
                    <th className="text-right py-2 px-2">股数</th>
                    <th className="text-right py-2 px-2">价格</th>
                    <th className="text-right py-2 px-2">金额</th>
                    <th className="text-right py-2 pl-2">持仓变化</th>
                  </tr>
                </thead>
                <tbody>
                  {closedTradesDialog?.trades.map(t => (
                    <tr key={t.id} className="border-b border-border/40">
                      <td className="py-2 pr-3 text-xs text-muted-foreground">
                        {t.traded_at ? new Date(t.traded_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) : '-'}
                      </td>
                      <td className="py-2 px-2">
                        <span className={`text-xs font-medium ${t.side === 'sell' ? 'text-emerald-500' : 'text-rose-500'}`}>
                          {t.side === 'sell' ? '卖出' : '买入'}
                        </span>
                      </td>
                      <td className="text-right py-2 px-2 font-mono">{t.quantity}</td>
                      <td className="text-right py-2 px-2 font-mono">{Number(t.price).toFixed(4)}</td>
                      <td className="text-right py-2 px-2 font-mono">{formatMoney(t.amount)}</td>
                      <td className="text-right py-2 pl-2 text-xs text-muted-foreground font-mono">
                        {t.qty_before != null && t.qty_after != null ? `${t.qty_before}→${t.qty_after}` : '-'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* 持仓历史交易弹窗 */}
      <Dialog open={!!positionTradesDialog} onOpenChange={(open) => { if (!open) { setPositionTradesDialog(null); setPositionTrades([]); setEditingTradeId(null) } }}>
        <DialogContent className="max-w-3xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>
              {positionTradesDialog?.name || positionTradesDialog?.symbol} · 历史交易
            </DialogTitle>
            <DialogDescription>
              {positionTradesDialog?.accountName} · {positionTradesDialog?.symbol}
              {positionTradesDialog?.market ? ` · ${marketLabel(positionTradesDialog.market)}` : ''}
              {positionTrades.length > 0 ? ` · 共 ${positionTrades.length} 笔` : ''}
            </DialogDescription>
          </DialogHeader>
          {positionTradesLoading ? (
            <div className="text-center text-muted-foreground text-sm py-8">加载中…</div>
          ) : positionTrades.length === 0 ? (
            <div className="text-center text-muted-foreground text-sm py-8">暂无交易记录</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-muted-foreground text-xs">
                    <th className="text-left py-2 pr-3">时间</th>
                    <th className="text-left py-2 px-2">方向</th>
                    <th className="text-right py-2 px-2">股数</th>
                    <th className="text-right py-2 px-2">价格</th>
                    <th className="text-right py-2 px-2">金额</th>
                    <th className="text-right py-2 px-2">成本变化</th>
                    <th className="text-right py-2 px-2">持仓变化</th>
                    <th className="text-left py-2 px-2">备注</th>
                    <th className="text-center py-2 pl-2">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {positionTrades.map(t => {
                    const isEditing = editingTradeId === t.id
                    return (
                    <tr key={t.id} className={`border-b border-border/40 ${isEditing ? 'bg-accent/40' : 'hover:bg-accent/30'}`}>
                      {isEditing ? (
                        <>
                          <td className="py-2 pr-3">
                            <Input
                              type="datetime-local"
                              className="h-8 text-xs w-[160px]"
                              value={tradeEditForm.traded_at}
                              onChange={e => setTradeEditForm(f => ({ ...f, traded_at: e.target.value }))}
                            />
                          </td>
                          <td className="py-2 px-2">
                            <Select
                              value={tradeEditForm.side}
                              onValueChange={v => setTradeEditForm(f => ({ ...f, side: v as 'buy' | 'sell' }))}
                            >
                              <SelectTrigger className="h-8 w-[72px] text-xs">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="buy">买入</SelectItem>
                                <SelectItem value="sell">卖出</SelectItem>
                              </SelectContent>
                            </Select>
                          </td>
                          <td className="py-2 px-2">
                            <Input
                              className="h-8 text-xs w-[80px] text-right font-mono"
                              inputMode="numeric"
                              value={tradeEditForm.quantity}
                              onChange={e => setTradeEditForm(f => ({ ...f, quantity: e.target.value }))}
                            />
                          </td>
                          <td className="py-2 px-2">
                            <Input
                              className="h-8 text-xs w-[88px] text-right font-mono"
                              inputMode="decimal"
                              value={tradeEditForm.price}
                              onChange={e => setTradeEditForm(f => ({ ...f, price: e.target.value }))}
                            />
                          </td>
                          <td className="text-right py-2 px-2 font-mono text-xs text-muted-foreground">
                            {tradeEditForm.price && tradeEditForm.quantity
                              ? formatMoney(Number(tradeEditForm.price) * (Number.parseInt(tradeEditForm.quantity, 10) || 0))
                              : '—'}
                          </td>
                          <td className="py-2 px-2 text-xs text-muted-foreground" colSpan={2}>
                            保存后将自动重算成本与持仓
                          </td>
                          <td className="py-2 px-2">
                            <Input
                              className="h-8 text-xs"
                              value={tradeEditForm.note}
                              onChange={e => setTradeEditForm(f => ({ ...f, note: e.target.value }))}
                              placeholder="备注"
                            />
                          </td>
                          <td className="py-2 pl-2 text-center whitespace-nowrap">
                            <Button size="sm" className="h-7 px-2 text-xs mr-1" disabled={tradeEditSaving} onClick={saveEditTrade}>
                              {tradeEditSaving ? '保存中…' : '保存'}
                            </Button>
                            <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" disabled={tradeEditSaving} onClick={cancelEditTrade}>
                              取消
                            </Button>
                          </td>
                        </>
                      ) : (
                        <>
                      <td className="py-2 pr-3 text-xs text-muted-foreground whitespace-nowrap">
                        {t.traded_at ? new Date(t.traded_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) : '-'}
                      </td>
                      <td className="py-2 px-2">
                        <span className={`text-xs font-medium ${t.side === 'sell' ? 'text-emerald-500' : 'text-rose-500'}`}>
                          {t.side === 'sell' ? '卖出' : '买入'}
                        </span>
                      </td>
                      <td className="text-right py-2 px-2 font-mono">{t.side === 'sell' ? '-' : '+'}{t.quantity}</td>
                      <td className="text-right py-2 px-2 font-mono">{formatPrice(t.price)}</td>
                      <td className="text-right py-2 px-2 font-mono">{formatMoney(t.amount)}</td>
                      <td className="text-right py-2 px-2 text-xs text-muted-foreground font-mono whitespace-nowrap">
                        {t.cost_before != null && t.cost_after != null
                          ? `${formatPrice(t.cost_before)} → ${formatPrice(t.cost_after)}`
                          : '-'}
                      </td>
                      <td className="text-right py-2 px-2 text-xs text-muted-foreground font-mono whitespace-nowrap">
                        {t.qty_before != null && t.qty_after != null ? `${t.qty_before} → ${t.qty_after}` : '-'}
                      </td>
                      <td className="py-2 px-2 text-xs text-muted-foreground max-w-[120px] truncate" title={t.note || ''}>
                        {t.note || '-'}
                      </td>
                      <td className="py-2 pl-2 text-center">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 w-7 p-0"
                          title="编辑"
                          disabled={editingTradeId != null}
                          onClick={() => startEditTrade(t)}
                        >
                          <Pencil className="w-3.5 h-3.5" />
                        </Button>
                      </td>
                        </>
                      )}
                    </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Watchlist */}
      {pageMode === 'watchlist' && (
        <div className="card p-0">
          <div className="sticky top-[calc(env(safe-area-inset-top,0px)+4.25rem)] md:top-[5.25rem] z-40 border-b border-border/60 bg-card/95 backdrop-blur-sm supports-[backdrop-filter]:bg-card/85 px-4 pt-3 pb-3 space-y-2.5 shadow-[0_8px_24px_-20px_hsl(var(--foreground)/0.25)]">
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-[13px] font-semibold text-foreground shrink-0">
                关注列表 <span className="ml-1 font-mono text-[11px] text-muted-foreground font-normal">{watchlistCount}</span>
              </h3>
              <div className="flex items-center gap-1.5 shrink-0">
                <Button
                  size="sm"
                  className="h-7 text-[11px] px-2.5"
                  onClick={() => { setStockForm(emptyStockForm); setSearchQuery(''); setShowStockForm(true) }}
                >
                  <Plus className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline">添加股票</span>
                  <span className="sm:hidden">添加</span>
                </Button>
              </div>
            </div>

            <div className="flex items-center gap-1 overflow-x-auto scrollbar-none -mx-1 px-1">
              {[
                { value: '', label: '全部', count: watchlistStocks.length },
                { value: 'CN', label: 'A股', count: watchlistStocks.filter(s => s.market === 'CN').length },
                { value: 'HK', label: '港股', count: watchlistStocks.filter(s => s.market === 'HK').length },
                { value: 'US', label: '美股', count: watchlistStocks.filter(s => s.market === 'US').length },
              ].map(opt => (
                <button
                  key={opt.value}
                  onClick={() => setStockListFilter(opt.value)}
                  className={`shrink-0 text-[11px] px-2 py-0.5 rounded transition-colors ${
                    stockListFilter === opt.value
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-accent/50 text-muted-foreground hover:bg-accent'
                  }`}
                >
                  {opt.label} ({opt.count})
                </button>
              ))}
            </div>

            <div className="flex items-center justify-between gap-2">
              <div className="text-[11px] text-muted-foreground shrink-0">
                筛选
                {!watchlistReorderDisabled && (
                  <span className="ml-2 text-muted-foreground/70 hidden sm:inline">· 拖左侧手柄排序</span>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => setWatchlistFeaturedOnly(!watchlistFeaturedOnly)}
                  className={`inline-flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-md border transition-colors ${
                    watchlistFeaturedOnly
                      ? 'bg-amber-500 border-amber-600 text-amber-950 font-semibold shadow-sm shadow-amber-500/20'
                      : 'bg-accent/30 border-border/50 text-muted-foreground hover:border-amber-500/40 hover:text-amber-600'
                  }`}
                  title="只显示置顶股票"
                >
                  <Pin className={`w-3 h-3 ${watchlistFeaturedOnly ? 'fill-current rotate-45' : ''}`} />
                  置顶
                </button>
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
          </div>

          <div className="px-4 pt-3 pb-3 space-y-2.5 border-b border-border/40">
            <div className="pt-1">
              <AiChainRotationBanner
                layerCounts={watchlistChainOptions}
                activeFilterKey={watchlistChainFilter}
                onToggleFilter={toggleWatchlistChainFilter}
              />
              {watchlistChainFilter ? (
                <div className="mt-1.5 flex justify-end">
                  <button
                    type="button"
                    onClick={() => setWatchlistChainFilter('')}
                    className="text-[10px] text-muted-foreground hover:text-foreground transition-colors"
                  >
                    清除轮动筛选
                  </button>
                </div>
              ) : null}
            </div>

            {watchlistConceptTagOptions.length > 0 && (
              <div>
                <div className="flex items-center justify-between gap-2 mb-1.5">
                  <div className="text-[11px] text-muted-foreground">标签</div>
                  {watchlistTagFilter && (
                    <button
                      type="button"
                      onClick={() => setWatchlistTagFilter('')}
                      className="text-[10px] text-muted-foreground hover:text-foreground transition-colors"
                    >
                      清除标签筛选
                    </button>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-1 max-h-16 overflow-y-auto scrollbar-none">
                  {watchlistConceptTagOptions.slice(0, 14).map((opt) => (
                    <button
                      key={opt.name}
                      type="button"
                      onClick={() => toggleWatchlistTagFilter(opt.name)}
                      className={`text-[11px] px-2 py-0.5 rounded transition-colors ${
                        watchlistTagFilter === opt.name
                          ? 'bg-primary text-primary-foreground'
                          : 'bg-accent/50 text-muted-foreground hover:bg-accent'
                      }`}
                      title={`筛选「${opt.name}」标签`}
                    >
                      {opt.name} ({opt.count})
                    </button>
                  ))}
                  {watchlistConceptTagOptions.length > 14 && (
                    <Select
                      value={watchlistConceptTagOptions.some((opt) => opt.name === watchlistTagFilter) ? watchlistTagFilter : ''}
                      onValueChange={(value) => setWatchlistTagFilter(value === '__all__' ? '' : value)}
                    >
                      <SelectTrigger className="h-6 w-[108px] text-[11px] px-2">
                        <SelectValue placeholder="更多标签" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="__all__">全部标签</SelectItem>
                        {watchlistConceptTagOptions.slice(14).map((opt) => (
                          <SelectItem key={opt.name} value={opt.name}>
                            {opt.name} ({opt.count})
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                </div>
              </div>
            )}
          </div>

          <div className="p-4 pt-3">
          {watchlistStocks.length === 0 ? (
            <div className="py-12 text-center">
              <div className="text-[13px] text-muted-foreground">还没有添加关注股票</div>
              <div className="mt-2 text-[11px] text-muted-foreground/70">点击上方「添加股票」开始</div>
            </div>
          ) : (
            (() => {
              const visibleWatchlistStocks = sortWatchlistStocks(watchlistStocks)
                .filter(s => !stockListFilter || s.market === stockListFilter)
                .filter(s => {
                  if (!watchlistTagFilter) return true
                  return (s.concept_tags || []).some((tag) => tag.name === watchlistTagFilter)
                })
                .filter(s => {
                  if (!watchlistChainFilter) return true
                  const key = stockChainFilterKey(s.industry_chain)
                  if (!key) return false
                  return key === normalizeChainFilterKey(watchlistChainFilter)
                })
                .filter(s => !watchlistFeaturedOnly || s.is_featured)
                .filter(stock => {
                  if (!watchlistOnlyAlerts) return true
                  const { suggestion } = getSuggestionForStock(stock.symbol, stock.market, false)
                  return !!suggestion?.should_alert
                })

              if (visibleWatchlistStocks.length === 0) {
                const hasActiveWatchlistFilters = stockListFilter !== ''
                  || watchlistOnlyAlerts
                  || watchlistFeaturedOnly
                  || watchlistTagFilter !== ''
                  || watchlistChainFilter !== ''
                return (
                  <div className="py-10 text-center">
                    <div className="text-[13px] text-muted-foreground">没有符合当前筛选条件的股票</div>
                    {watchlistTagFilter && (
                      <button
                        type="button"
                        onClick={() => setWatchlistTagFilter('')}
                        className="mt-2 text-[11px] text-primary hover:underline"
                      >
                        清除标签「{watchlistTagFilter}」
                      </button>
                    )}
                    {watchlistChainFilter && (
                      <button
                        type="button"
                        onClick={() => setWatchlistChainFilter('')}
                        className="mt-2 ml-2 text-[11px] text-primary hover:underline"
                      >
                        清除轮动筛选
                      </button>
                    )}
                    {hasActiveWatchlistFilters && (
                      <button
                        type="button"
                        onClick={() => {
                          setStockListFilter('')
                          setWatchlistOnlyAlerts(false)
                          setWatchlistFeaturedOnly(false)
                          setWatchlistTagFilter('')
                          setWatchlistChainFilter('')
                        }}
                        className="mt-2 block mx-auto text-[11px] text-primary hover:underline"
                      >
                        重置全部筛选
                      </button>
                    )}
                  </div>
                )
              }

              const renderWatchlistCard = (stock: Stock) => {
                const isHolding = hasAnyPositionForStockId(stock.id)
                const quote = getStockQuote(`${stock.market}:${stock.symbol}`)
                const changeColor = quote?.change_pct != null
                  ? (quote.change_pct > 0 ? 'text-rose-500' : quote.change_pct < 0 ? 'text-emerald-500' : 'text-muted-foreground')
                  : 'text-muted-foreground'
                const { suggestion, kline } = getSuggestionForStock(stock.symbol, stock.market, isHolding)
                const badge = marketBadge(stock.market)
                const chainFilterKey = stockChainFilterKey(stock.industry_chain)
                const quoteKey = `${stock.market}:${stock.symbol}`
                const lmdSnapshot = lmdSnapshots[quoteKey] || null

                return (
                  <div
                    key={stock.id}
                    onDragOver={(e) => {
                      if (watchlistReorderDisabled) return
                      e.preventDefault()
                      e.dataTransfer.dropEffect = 'move'
                      if (draggingWatchStockId != null) {
                        previewWatchlistReorder(draggingWatchStockId, stock.id)
                      }
                    }}
                    onDrop={(e) => {
                      if (watchlistReorderDisabled) return
                      e.preventDefault()
                      if (draggingWatchStockId != null) void commitWatchlistReorder()
                      setDraggingWatchStockId(null)
                      watchDragSnapshotRef.current = null
                    }}
                    className={`group rounded-lg border transition-colors p-2 cursor-pointer ${
                      watchlistCardChainClass(stock.industry_chain?.layer, !!stock.is_featured)
                    } ${draggingWatchStockId === stock.id ? 'opacity-60 scale-[0.98]' : ''}`}
                    onClick={() => {
                      if (isSuppressCardClick()) return
                      openStockDetail(stock.symbol, stock.market, stock.name, isHolding)
                    }}
                  >
                    <div className="flex items-start gap-1 mb-1">
                      <div
                        draggable={!watchlistReorderDisabled}
                        onDragStart={(e) => {
                          if (watchlistReorderDisabled) return
                          watchDragSnapshotRef.current = stocks
                          pendingWatchOrderRef.current = null
                          setDraggingWatchStockId(stock.id)
                          e.dataTransfer.effectAllowed = 'move'
                          e.stopPropagation()
                        }}
                        onDragEnd={() => {
                          setDraggingWatchStockId(null)
                          watchDragSnapshotRef.current = null
                          pendingWatchOrderRef.current = null
                        }}
                        onClick={(e) => e.stopPropagation()}
                        onMouseDown={(e) => e.stopPropagation()}
                        className={`shrink-0 pt-0.5 touch-none ${
                          watchlistReorderDisabled
                            ? 'text-muted-foreground/20 cursor-not-allowed'
                            : 'text-muted-foreground/35 hover:text-amber-600 cursor-grab active:cursor-grabbing'
                        }`}
                        title={watchlistReorderDisabled ? '清除筛选后可拖动排序' : '拖动排序'}
                      >
                        <GripVertical className="w-3.5 h-3.5" />
                      </div>
                      <div className="min-w-0 flex-1 flex items-start justify-between gap-1.5">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1 flex-wrap">
                          <span className={`text-[8px] px-1 py-px rounded ${badge.style}`}>{badge.label}</span>
                          <FeaturedPinButton
                            size="sm"
                            isFeatured={!!stock.is_featured}
                            onClick={() => toggleWatchlistFeatured(stock)}
                          />
                          {isHolding && (
                            <span className="text-[8px] px-1 py-px rounded bg-emerald-500/15 text-emerald-500">持</span>
                          )}
                          <span className="font-mono text-[11px] font-semibold text-foreground">{stock.symbol}</span>
                          {stock.industry_chain?.layer ? (
                            <IndustryChainBadge
                              compact
                              chain={stock.industry_chain}
                              onClick={() => {
                                if (chainFilterKey) toggleWatchlistChainFilter(chainFilterKey)
                              }}
                            />
                          ) : null}
                        </div>
                        <div className="text-[10px] text-muted-foreground truncate leading-tight mt-0.5" title={stock.name}>
                          {stock.name}
                        </div>
                        <div className="mt-1 pl-0">
                          <WatchlistValuationBrief
                            snapshot={lmdSnapshot}
                            quotePe={quote?.pe_ratio}
                            onClick={() => openLmdReportSection(stock.symbol, stock.market, stock.name, 'valuation')}
                            onEnsureReport={() => {
                              stocksApi.ensureLmdReport(stock.id)
                                .then((resp) => {
                                  toast(resp.message || `${LMD_DISPLAY_NAME}报告生成中`, 'info')
                                  refreshLmdSnapshots().catch(() => undefined)
                                })
                                .catch((e) => toast(e instanceof Error ? e.message : '触发生成失败', 'error'))
                            }}
                          />
                        </div>
                      </div>
                      <div className={`font-mono text-right shrink-0 leading-tight ${changeColor}`}>
                        <div className="text-[12px] font-semibold">
                          {quote?.current_price != null ? quote.current_price.toFixed(2) : '--'}
                        </div>
                        <div className="text-[10px]">
                          {quote?.change_pct != null ? `${quote.change_pct >= 0 ? '+' : ''}${quote.change_pct.toFixed(2)}%` : '--'}
                        </div>
                        <KlineLevelsBrief kline={kline} align="right" />
                      </div>
                      </div>
                    </div>

                    {(stock.concept_tags?.length ?? 0) > 0 ? (
                      <div className="mb-1 pl-[18px]">
                        <StockConceptTags
                          tags={stock.concept_tags || []}
                          market={stock.market}
                          compact
                          maxVisible={3}
                          activeTag={watchlistTagFilter}
                          onTagClick={toggleWatchlistTagFilter}
                        />
                      </div>
                    ) : null}

                    {(suggestion || kline) ? (
                      <div className="mb-1 [&_*]:text-[10px]">
                        <SuggestionBadge
                          suggestion={suggestion}
                          stockName={stock.name}
                          stockSymbol={stock.symbol}
                          kline={kline}
                          market={stock.market}
                          hasPosition={isHolding}
                        />
                      </div>
                    ) : null}

                    <div className="mb-1 pl-[18px]">
                      <ChanEmotionBrief
                        data={getChanEmotionForStock(stock.symbol, stock.market)}
                        loading={isChanEmotionLoading(stock.symbol, stock.market)}
                        onRequestLoad={() => fetchChanEmotionForStock(stock.symbol, stock.market, isHolding)}
                        onClick={() => openStockDetail(stock.symbol, stock.market, stock.name, isHolding)}
                      />
                    </div>

                    <div className="mb-1 pl-[18px]">
                      <StockTradingAskButtons
                        stockName={stock.name}
                        hasPosition={isHolding}
                        onAsk={(question) => openWatchlistChat(stock, isHolding, suggestion, kline, question)}
                      />
                    </div>

                    <div className="pt-1 border-t border-border/25">
                      <WatchlistRowActions
                        compact
                        stock={stock}
                        isHolding={isHolding}
                        onKline={() => openKlineDialog(stock.symbol, stock.market, stock.name, isHolding)}
                        onReports={() => openStockDetailReports(stock.symbol, stock.market, stock.name, isHolding)}
                        onValuation={() => openLmdReportSection(stock.symbol, stock.market, stock.name, 'valuation')}
                        onFundamentals={() => openLmdReportSection(stock.symbol, stock.market, stock.name, 'fundamentals')}
                        onAnalysis={() => openStockDetailDeep(stock.symbol, stock.market, stock.name, isHolding)}
                        onAskAI={() => openWatchlistChat(stock, isHolding, suggestion, kline)}
                        onBuy={() => openWatchlistBuy(stock.symbol, stock.market, stock.name)}
                        onLongTermPlan={() => setLongTermPlanStock(stock)}
                        onEtfOverview={stock.security_type === 'etf' ? () => openEtfOverview(stock.symbol, stock.name) : undefined}
                        onAgentConfig={() => setAgentDialogStock(stock)}
                        onNews={() => openNewsDialog(stock.name)}
                        onDelete={() => setRemoveWatchStock(stock)}
                        onPriceAlertChanged={loadPriceAlertSummaries}
                        getPriceAlertSummary={getPriceAlertSummary}
                      />
                    </div>
                  </div>
                )
              }

              return (
                <div className="grid grid-cols-1 min-[480px]:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-2">
                  {visibleWatchlistStocks.map((stock) => renderWatchlistCard(stock))}
                </div>
              )
            })()
          )}
          </div>
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

      <EtfOverviewModal
        code={etfOverviewCode}
        name={etfOverviewName}
        open={etfOverviewOpen}
        onOpenChange={setEtfOverviewOpen}
      />

      <LmdReportSectionModal
        open={!!lmdSectionModal}
        onOpenChange={(open) => { if (!open) setLmdSectionModal(null) }}
        symbol={lmdSectionModal?.symbol || ''}
        market={lmdSectionModal?.market || 'CN'}
        stockName={lmdSectionModal?.name}
        section={lmdSectionModal?.section ?? null}
      />

      <StockInsightModal
        open={insightOpen}
        onOpenChange={(open) => {
          setInsightOpen(open)
          if (!open) {
            setInsightExpandAddPosition(false)
            setInsightExpandReducePosition(false)
            setInsightInitialTab(undefined)
          }
        }}
        symbol={insightSymbol}
        market={insightMarket}
        stockName={insightName}
        hasPosition={insightHasPosition}
        initialTab={insightInitialTab}
        initialExpandAddPosition={insightExpandAddPosition}
        initialExpandReducePosition={insightExpandReducePosition}
        onPortfolioChanged={handlePortfolioChanged}
        onStockUpdated={(updated) => {
          setStocks(prev => prev.map(s => (
            s.id === updated.id
              ? {
                  ...s,
                  concept_tags: updated.concept_tags,
                  concept_tags_auto: updated.concept_tags_auto,
                  concept_tags_manual: updated.concept_tags_manual,
                  is_featured: updated.is_featured,
                }
              : s
          )))
        }}
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
            <DialogTitle>删除股票</DialogTitle>
            <DialogDescription>删除后将从系统中移除该股票及其关注配置</DialogDescription>
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
                    ? '该股票存在持仓，不能直接删除。请先在持仓页删除持仓记录。'
                    : '删除后将不再出现在关注列表，同时会清理该股票关联的价格提醒。'}
                </div>
              </div>

              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setRemoveWatchStock(null)} disabled={removingWatchStock}>取消</Button>
                <Button
                  variant="destructive"
                  onClick={() => removeFromWatchlist(removeWatchStock)}
                  disabled={removingWatchStock || hasAnyPositionForStockId(removeWatchStock.id)}
                >
                  {hasAnyPositionForStockId(removeWatchStock.id) ? '请先删除持仓' : (removingWatchStock ? '处理中…' : '删除股票')}
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
              <Label>资金币种</Label>
              <Select
                value={accountForm.base_currency}
                onValueChange={(value) =>
                  setAccountForm({ ...accountForm, base_currency: value as AccountForm['base_currency'] })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ACCOUNT_CURRENCY_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="mt-1 text-[11px] text-muted-foreground">股票现金与其他资产均按此币种填写；切换币种不会自动换算</p>
            </div>
            <div>
              <Label>股票现金（{accountCurrencyLabel(accountForm.base_currency)}）</Label>
              <Input
                value={accountForm.available_funds}
                onChange={e => setAccountForm({ ...accountForm, available_funds: e.target.value })}
                placeholder="0"
                className="font-mono"
                inputMode="decimal"
              />
              <p className="mt-1 text-[11px] text-muted-foreground">可用于买股票的现金，加仓从此扣减、减仓回款计入此处</p>
            </div>
            <div>
              <div className="flex items-center justify-between gap-2">
                <Label>其他资产（{accountCurrencyLabel(accountForm.base_currency)}）</Label>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  className="h-7 px-2 text-[11px]"
                  onClick={() =>
                    setAccountForm({
                      ...accountForm,
                      other_fund_items: [...accountForm.other_fund_items, { label: '', amount: '' }],
                    })
                  }
                >
                  <Plus className="w-3.5 h-3.5 mr-1" />
                  添加分类
                </Button>
              </div>
              <div className="flex flex-wrap gap-1.5 mt-2">
                {OTHER_FUND_LABEL_PRESETS.map((label) => (
                  <button
                    key={label}
                    type="button"
                    className="text-[11px] px-2 py-0.5 rounded bg-accent/60 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
                    onClick={() =>
                      setAccountForm({
                        ...accountForm,
                        other_fund_items: [...accountForm.other_fund_items, { label, amount: '' }],
                      })
                    }
                  >
                    + {label}
                  </button>
                ))}
              </div>
              {accountForm.other_fund_items.length === 0 ? (
                <p className="mt-2 text-[11px] text-muted-foreground">如理财、存款等，点击上方标签快速添加</p>
              ) : (
                <div className="space-y-2 mt-2">
                  {accountForm.other_fund_items.map((item, index) => (
                    <div key={`${item.label}-${index}`} className="flex items-center gap-2">
                      <Input
                        value={item.label}
                        onChange={(e) => {
                          const next = [...accountForm.other_fund_items]
                          next[index] = { ...next[index], label: e.target.value }
                          setAccountForm({ ...accountForm, other_fund_items: next })
                        }}
                        placeholder="分类名称"
                        className="flex-1"
                      />
                      <Input
                        value={item.amount}
                        onChange={(e) => {
                          const next = [...accountForm.other_fund_items]
                          next[index] = { ...next[index], amount: e.target.value }
                          setAccountForm({ ...accountForm, other_fund_items: next })
                        }}
                        placeholder="0"
                        className="w-[120px] font-mono"
                        inputMode="decimal"
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 shrink-0 hover:text-destructive"
                        onClick={() =>
                          setAccountForm({
                            ...accountForm,
                            other_fund_items: accountForm.other_fund_items.filter((_, i) => i !== index),
                          })
                        }
                      >
                        <X className="w-4 h-4" />
                      </Button>
                    </div>
                  ))}
                </div>
              )}
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
                  onChange={e => {
                    const cost_price = e.target.value
                    setPositionForm(prev => ({
                      ...prev,
                      cost_price,
                      invested_amount: calcPositionInitialFunds(cost_price, prev.quantity) || prev.invested_amount,
                    }))
                  }}
                  placeholder="0.00"
                  className="font-mono"
                  inputMode="decimal"
                />
              </div>
              <div>
                <Label>持仓数量</Label>
                <Input
                  value={positionForm.quantity}
                  onChange={e => {
                    const quantity = e.target.value
                    setPositionForm(prev => ({
                      ...prev,
                      quantity,
                      invested_amount: calcPositionInitialFunds(prev.cost_price, quantity) || prev.invested_amount,
                    }))
                  }}
                  placeholder="0"
                  className="font-mono"
                  inputMode="numeric"
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label>交易风格</Label>
                <Select
                  value={positionForm.trading_style || DEFAULT_TRADING_STYLE}
                  onValueChange={val => setPositionForm({ ...positionForm, trading_style: val === '__none__' ? '' : val })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="短线" />
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
            <div>
              <Label>
                {editPositionId ? '交易时间' : '买入时间'}
                <span className="text-muted-foreground/60 text-[11px] font-normal ml-1">
                  {editPositionId ? '（改股数时记录流水，选填）' : '（选填，补录历史请填实际时间）'}
                </span>
              </Label>
              <Input
                type="datetime-local"
                value={positionForm.trade_time}
                onChange={e => setPositionForm({ ...positionForm, trade_time: e.target.value })}
                className="font-mono text-[13px]"
              />
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

      <Dialog open={!!longTermPlanStock} onOpenChange={open => !open && setLongTermPlanStock(null)}>
        <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>长线投资计划</DialogTitle>
            <DialogDescription>
              {longTermPlanStock?.name}（{longTermPlanStock?.symbol}）
            </DialogDescription>
          </DialogHeader>
          {longTermPlanStock && (
            <LongTermPlanPanel
              stock={longTermPlanStock}
              onSaved={profile => {
                setStocks(prev => prev.map(s => (
                  s.id === longTermPlanStock.id ? { ...s, investment_profile: profile } : s
                )))
                setLongTermPlanStock(prev => prev ? { ...prev, investment_profile: profile } : null)
              }}
            />
          )}
        </DialogContent>
      </Dialog>

      {/* Agent 分析结果弹窗 */}
      <Dialog open={!!agentResultDialog} onOpenChange={open => !open && setAgentResultDialog(null)}>
        <DialogContent className="max-w-3xl max-h-[85vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="text-base">{agentResultDialog?.title}</DialogTitle>
            <DialogDescription className="flex items-center gap-2 pt-1">
              {agentResultDialog?.should_alert ? (
                <Badge variant="default" className="text-[10px]">建议关注</Badge>
              ) : (
                <Badge variant="secondary" className="text-[10px]">分析完成</Badge>
              )}
              {agentResultDialog?.notified && (
                <Badge variant="outline" className="text-[10px]">已发送通知</Badge>
              )}
            </DialogDescription>
          </DialogHeader>
          <div className="mt-2 p-4 bg-accent/20 rounded-lg overflow-y-auto flex-1 min-h-0 scrollbar">
            <ReportMarkdown content={agentResultDialog?.content} />
          </div>
          <div className="flex justify-end gap-2 mt-2">
            <Button variant="outline" size="sm" onClick={() => { setAgentResultDialog(null); navigate('/history') }}>
              查看历史
            </Button>
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
