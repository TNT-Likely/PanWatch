import { fetchAPI } from './client'

export interface PositionTrade {
  id: number
  position_id: number
  side: string
  price: number
  quantity: number
  amount: number
  cost_before: number | null
  qty_before: number | null
  cost_after: number | null
  qty_after: number | null
  note: string | null
  traded_at: string | null
  created_at: string | null
}

export interface PositionSnapshot {
  id: number
  account_id: number
  stock_id: number
  cost_price: number
  quantity: number
  invested_amount: number | null
  sort_order: number
  trading_style: string | null
  account_name: string | null
  stock_symbol: string | null
  stock_name: string | null
}

export interface PositionAddResult {
  position: PositionSnapshot
  trade: PositionTrade
  /** 卖出后账户可用资金(清仓/减仓回款已计入) */
  available_funds?: number | null
  /** 本次卖出是否导致清仓 */
  closed?: boolean
}

export interface PortfolioRecentTrade extends PositionTrade {
  account_name: string
  symbol: string
  market: string
  stock_name: string
}

export interface ClosedPositionTrade extends PositionTrade {}

export interface ClosedPosition {
  id: number
  account_id: number
  stock_id: number
  stock_symbol: string | null
  stock_name: string | null
  market: string | null
  account_name: string | null
  cost_price: number
  quantity: number
  invested_amount: number | null
  realized_pnl: number
  opened_at: string | null
  closed_at: string | null
  trading_style: string | null
  trades: ClosedPositionTrade[]
}

export const positionsApi = {
  /** 加仓:记录流水并更新加权平均成本 */
  add: (
    positionId: number,
    body: { price: number; quantity: number; note?: string; traded_at?: string },
  ) =>
    fetchAPI<PositionAddResult>(`/positions/${positionId}/add`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  /** 减仓/卖出:记录流水并更新股数(成本单价不变) */
  reduce: (
    positionId: number,
    body: { price: number; quantity: number; note?: string; traded_at?: string },
  ) =>
    fetchAPI<PositionAddResult>(`/positions/${positionId}/reduce`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  /** 持仓变动流水 */
  trades: (positionId: number, limit = 20) =>
    fetchAPI<PositionTrade[]>(`/positions/${positionId}/trades?limit=${limit}`),

  /** 全账户最近加仓/变动流水 */
  recentTrades: (limit = 50) =>
    fetchAPI<PortfolioRecentTrade[]>(`/portfolio/recent-trades?limit=${limit}`),

  /** 已清仓持仓列表(含历史成交明细) */
  closedPositions: (limit = 100) =>
    fetchAPI<ClosedPosition[]>(`/portfolio/closed-positions?limit=${limit}`),
}
