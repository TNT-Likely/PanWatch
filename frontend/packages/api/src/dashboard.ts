import { fetchAPI } from './client'
import type { StrategySignalItem } from './recommendations'

type QueryValue = string | number | boolean | null | undefined

function withQuery(path: string, params: Record<string, QueryValue>): string {
  const q = new URLSearchParams()
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v === undefined || v === null) return
    const sv = String(v).trim()
    if (!sv) return
    q.set(k, sv)
  })
  const s = q.toString()
  return s ? `${path}?${s}` : path
}

export interface DashboardMarketIndex {
  symbol: string
  name: string
  market: string
  current_price: number | null
  change_pct: number | null
  change_amount: number | null
  prev_close: number | null
}

export interface DashboardMarketStatus {
  code: string
  name: string
  status: string
  status_text: string
  is_trading: boolean
  sessions: string[]
  local_time: string
}

export interface DashboardPosition {
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

export interface OtherFundItem {
  label: string
  amount: number
}

export interface DashboardAccountSummary {
  id: number
  name: string
  available_funds: number
  other_funds?: number
  other_fund_items?: OtherFundItem[]
  base_currency?: string
  total_cost: number
  total_market_value: number
  total_pnl: number
  total_pnl_pct: number
  total_assets: number
  positions: DashboardPosition[]
}

export interface DashboardPortfolioSummary {
  accounts: DashboardAccountSummary[]
  total: {
    total_market_value: number
    total_cost: number
    total_pnl: number
    total_pnl_pct: number
    available_funds: number
    other_funds?: number
    total_assets: number
  }
  exchange_rates?: {
    HKD_CNY: number
    USD_CNY?: number
  }
}

export interface DashboardWatchStock {
  id: number
  symbol: string
  name: string
  market: string
}

export interface DashboardQuoteRequestItem {
  symbol: string
  market: string
}

export interface DashboardQuoteResponse {
  symbol: string
  market: string
  current_price: number | null
  change_pct: number | null
}

export interface DashboardHistoryItem {
  id: number
  agent_name: string
  stock_symbol: string
  analysis_date: string
  title: string
  content: string
  created_at: string
}

export interface DashboardSuggestion {
  action: string
  action_label: string
  signal?: string | null
  reason?: string | null
  should_alert?: boolean
  agent_label?: string | null
}

export interface DashboardMonitorStock {
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
  suggestion: DashboardSuggestion | null
}

export interface DashboardIntradayScanResponse {
  stocks: DashboardMonitorStock[]
  available_funds: number
}

export interface DashboardInsightItem {
  id: number
  agent_name: string
  agent_label: string
  analysis_date: string
  title: string
  updated_at: string
}

export interface DashboardMarketStock {
  symbol: string
  market: string
  name: string
  score_seed: number
  change_pct?: number | null
  turnover?: number | null
  source: string
}

export interface DashboardTopicItem {
  name: string
  score: number
  sentiment: string
}

export interface DashboardRiskSignalItem extends StrategySignalItem {
  risk_flags?: string[]
}

export interface DashboardOverviewResponse {
  generated_at: string
  market: 'ALL' | 'CN' | 'HK' | 'US'
  snapshot_date: string
  data_freshness: {
    strategy_snapshot_date: string
    entry_snapshot_date: string
    market_scan_snapshot_date: string
    latest_history_updated_at: string
  }
  kpis: {
    watchlist_count: number
    positions_count: number
    available_funds: number
    invested_cost: number
    total_assets_estimate: number
    executable_opportunities: number
    risk_positions: number
    win_rate_3d?: number | null
    win_sample_3d: number
    errors_24h: number
  }
  portfolio: {
    positions_count: number
    watchlist_count: number
    available_funds: number
    invested_cost: number
    by_market: Array<{
      market: string
      positions: number
      invested_cost: number
    }>
  }
  action_center: {
    opportunities: StrategySignalItem[]
    risk_items: DashboardRiskSignalItem[]
  }
  market_pulse: {
    hot_stocks: DashboardMarketStock[]
    hot_topics: DashboardTopicItem[]
  }
  strategy: {
    coverage: Record<string, any>
    factor_stats: Record<string, any>
    by_market: Array<Record<string, any>>
    top_by_strategy: Array<Record<string, any>>
  }
  insights: DashboardInsightItem[]
}

export const dashboardApi = {
  indices: () => fetchAPI<DashboardMarketIndex[]>('/market/indices'),

  marketStatus: () => fetchAPI<DashboardMarketStatus[]>('/stocks/markets/status'),

  portfolioSummary: (params?: { include_quotes?: boolean }) =>
    fetchAPI<DashboardPortfolioSummary>(
      withQuery('/portfolio/summary', {
        include_quotes: params?.include_quotes,
      })
    ),

  watchlist: () => fetchAPI<DashboardWatchStock[]>('/stocks'),

  batchQuotes: (items: DashboardQuoteRequestItem[]) =>
    fetchAPI<DashboardQuoteResponse[]>('/quotes/batch', {
      method: 'POST',
      body: JSON.stringify({ items }),
    }),

  history: (params: Record<string, QueryValue>) =>
    fetchAPI<DashboardHistoryItem[]>(withQuery('/history', params)),

  intradayScan: (params?: { analyze?: boolean }) =>
    fetchAPI<DashboardIntradayScanResponse>(
      withQuery('/agents/intraday/scan', {
        analyze: params?.analyze,
      }),
      {
        method: 'POST',
        timeoutMs: params?.analyze ? 90000 : 30000,
      }
    ),

  overview: (params?: {
    market?: 'ALL' | 'CN' | 'HK' | 'US'
    action_limit?: number
    risk_limit?: number
    days?: number
  }) => {
    const q = new URLSearchParams()
    if (params?.market) q.set('market', params.market)
    if (typeof params?.action_limit === 'number') q.set('action_limit', String(params.action_limit))
    if (typeof params?.risk_limit === 'number') q.set('risk_limit', String(params.risk_limit))
    if (typeof params?.days === 'number') q.set('days', String(params.days))
    const qs = q.toString()
    return fetchAPI<DashboardOverviewResponse>(`/dashboard/overview${qs ? `?${qs}` : ''}`, {
      timeoutMs: 45000,
    })
  },

  curate: (candidates: CurateCandidate[], model_id?: number) =>
    fetchAPI<{ items: CuratedItem[] }>('/dashboard/curate', {
      method: 'POST',
      body: JSON.stringify({ candidates, model_id }),
      timeoutMs: 40000,
    }),

  brief: (type: 'premarket' | 'eod') =>
    fetchAPI<DashboardBrief>(`/dashboard/brief?type=${type}`),
}

export interface DashboardBrief {
  empty?: boolean
  type: string
  agent_label?: string
  title?: string
  content?: string
  date?: string
  updated_at?: string
}

export interface CurateCandidate {
  type: string
  symbol?: string
  name?: string
  market?: string
  signal?: string
  change_pct?: number | null
}

export interface CuratedItem {
  index: number
  importance: number
  why: string
}
