import type { SuggestionInfo } from '@panwatch/biz-ui/components/suggestion-badge'

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
  asset_type?: 'stock' | 'etf'
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
  asset_type?: 'stock' | 'etf'
  symbol: string
  name: string
  market: string
}

export interface QuoteRequestItem {
  symbol: string
  market: string
}


export interface StockForm {
  asset_type: 'stock' | 'etf'
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
  stock_asset_type: 'stock' | 'etf'
}

// 建议池中的建议（包含来源和时间信息）
export interface PoolSuggestion extends Omit<SuggestionInfo, 'should_alert'> {
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
