import type { SuggestionInfo, KlineSummary } from '@panwatch/biz-ui/components/suggestion-badge'

// 持仓页领域类型与表单初值（由 Stocks.tsx 外移，纯移动不改语义）

export interface AgentResult {
  success?: boolean
  message?: string
  title: string
  content: string
  should_alert: boolean
  notified: boolean
  skipped?: boolean
}

export interface StockAgentInfo {
  agent_name: string
  display_name?: string
  schedule: string
  ai_model_id: number | null
  notify_channel_ids: number[]
}

export interface Stock {
  id: number
  symbol: string
  name: string
  market: string
  sort_order?: number
  agents: StockAgentInfo[]
}

export interface Account {
  id: number
  name: string
  available_funds: number
  enabled: boolean
}

export interface Position {
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
  exchange_rate: number | null  // 汇率（仅港股）
}

export interface AccountSummary {
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

export interface PortfolioSummary {
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

export interface AgentConfig {
  name: string
  display_name: string
  description: string
  enabled: boolean
  schedule: string
  execution_mode: string  // batch: 批量分析, single: 逐只分析
}

export interface SchedulePreview {
  schedule: string
  timezone: string
  next_runs: string[]
}

export interface SearchResult {
  symbol: string
  name: string
  market: string
}

export interface QuoteRequestItem {
  symbol: string
  market: string
}

export interface QuoteResponse {
  symbol: string
  market: string
  current_price: number | null
  change_pct: number | null
}

export interface StockForm {
  symbol: string
  name: string
  market: string
}

export interface AccountForm {
  name: string
  available_funds: string
}

export interface PositionForm {
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
export interface StockSuggestionData {
  symbol: string
  suggestion: SuggestionInfo | null
  kline: KlineSummary | null
}

// 建议池中的建议（包含来源和时间信息）
export interface PoolSuggestion {
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

export interface MarketStatus {
  code: string
  name: string
  status: string
  status_text: string
  is_trading: boolean
  sessions: string[]
  local_time: string
}

export interface NewsItem {
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

export interface PriceAlertRuleSummary {
  stock_symbol: string
  market: string
  enabled: boolean
}

export const emptyStockForm: StockForm = { symbol: '', name: '', market: 'CN' }
export const emptyAccountForm: AccountForm = { name: '', available_funds: '0' }
