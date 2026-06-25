import { fetchAPI } from './client'

export interface PaperTradingAccountResponse {
  id: number
  initial_capital: number
  current_capital: number
  total_equity: number
  total_pnl: number
  unrealized_pnl: number
  total_trades: number
  winning_trades: number
  win_rate: number
  max_drawdown_pct: number
  peak_capital: number
  enabled: boolean
  excluded_markets: string[]
  /** 各市场投资比例 {CN/HK/US: 0~1} */
  market_allocations: Record<string, number>
  /** 仅按单市场口径返回时存在 */
  market?: string
  allocation_ratio?: number
  created_at: string
  updated_at: string
}

export type MarketView = 'ALL' | 'CN' | 'HK' | 'US'

export interface PaperTradingPositionItem {
  id: number
  stock_symbol: string
  stock_market: string
  stock_name: string
  security_type?: string
  quantity: number
  entry_price: number
  stop_loss?: number | null
  target_price?: number | null
  current_price?: number | null
  unrealized_pnl: number
  unrealized_pnl_pct: number
  status: string
  signal_run_id?: number | null
  signal_snapshot_date: string
  signal_action: string
  strategy_code: string
  holding_days: number
  opened_at: string
  closed_at: string
  updated_at: string
}

export interface PaperTradingTradeItem {
  id: number
  stock_symbol: string
  stock_market: string
  stock_name: string
  security_type?: string
  quantity: number
  entry_price: number
  exit_price: number
  pnl: number
  pnl_pct: number
  exit_reason: string
  signal_run_id?: number | null
  signal_snapshot_date: string
  strategy_code: string
  holding_days: number
  opened_at: string
  closed_at: string
}

export interface PaperTradingTradesResponse {
  total: number
  items: PaperTradingTradeItem[]
}

export interface EquityCurvePoint {
  date: string
  equity: number
}

export interface StrategyPerformanceItem {
  strategy_code: string
  total_trades: number
  winning_trades: number
  win_rate: number
  total_pnl: number
  avg_pnl_pct: number
  avg_holding_days: number
  open_positions: number
  unrealized_pnl: number
}

export interface PaperTradingMetricsResponse {
  account: PaperTradingAccountResponse | null
  equity_curve: EquityCurvePoint[]
  open_positions: number
  strategy_performance: StrategyPerformanceItem[]
}

export interface NotifyChannelItem {
  id: number
  name: string
  type: string
  is_default: boolean
}

export interface PaperTradingNotifySettings {
  settings: {
    pt_notify_enabled: string
    pt_notify_channel_ids: string
    pt_notify_realtime: string
    pt_notify_premarket: string
    pt_notify_summary: string
  }
  channels: NotifyChannelItem[]
}

export const paperTradingApi = {
  getAccount: (market?: string) =>
    fetchAPI<PaperTradingAccountResponse>(
      `/paper-trading/account${market && market !== 'ALL' ? `?market=${encodeURIComponent(market)}` : ''}`
    ),

  listPositions: (status = 'open', market?: string) =>
    fetchAPI<PaperTradingPositionItem[]>(
      `/paper-trading/positions?status=${encodeURIComponent(status)}${market && market !== 'ALL' ? `&market=${encodeURIComponent(market)}` : ''}`
    ),

  listTrades: (limit = 50, offset = 0, market?: string) =>
    fetchAPI<PaperTradingTradesResponse>(
      `/paper-trading/trades?limit=${encodeURIComponent(String(limit))}&offset=${encodeURIComponent(String(offset))}${market && market !== 'ALL' ? `&market=${encodeURIComponent(market)}` : ''}`
    ),

  getMetrics: (market?: string) =>
    fetchAPI<PaperTradingMetricsResponse>(
      `/paper-trading/metrics${market && market !== 'ALL' ? `?market=${encodeURIComponent(market)}` : ''}`
    ),

  toggleAccount: (enabled: boolean) =>
    fetchAPI<PaperTradingAccountResponse>('/paper-trading/account/toggle', {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    }),

  resetAccount: () =>
    fetchAPI<{ ok: boolean }>('/paper-trading/account/reset', {
      method: 'POST',
    }),

  closePosition: (positionId: number) =>
    fetchAPI<{ ok: boolean }>(`/paper-trading/positions/${encodeURIComponent(String(positionId))}/close`, {
      method: 'POST',
    }),

  updateSettings: (settings: {
    excluded_markets?: string[]
    market_allocations?: Record<string, number>
    initial_capital?: number
  }) =>
    fetchAPI<PaperTradingAccountResponse>('/paper-trading/account/settings', {
      method: 'POST',
      body: JSON.stringify(settings),
    }),

  scan: () =>
    fetchAPI<{ status: string; opened: number; closed: number }>('/paper-trading/scan', {
      method: 'POST',
      timeoutMs: 30000,
    }),

  getNotifySettings: () =>
    fetchAPI<PaperTradingNotifySettings>('/paper-trading/notify-settings'),

  updateNotifySettings: (settings: Record<string, string>) =>
    fetchAPI<PaperTradingNotifySettings>('/paper-trading/notify-settings', {
      method: 'POST',
      body: JSON.stringify(settings),
    }),

  testNotify: () =>
    fetchAPI<{ ok: boolean }>('/paper-trading/notify-test', {
      method: 'POST',
    }),
}
