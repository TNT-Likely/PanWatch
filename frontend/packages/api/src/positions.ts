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

  /** 持仓变动流水 */
  trades: (positionId: number, limit = 20) =>
    fetchAPI<PositionTrade[]>(`/positions/${positionId}/trades?limit=${limit}`),
}
