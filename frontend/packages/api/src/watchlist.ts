import { fetchAPI } from './client'

export type WatchDecisionLabel =
  | '观察'
  | '买入候选'
  | '继续持有'
  | '加仓观察'
  | '减仓警告'
  | '止损触发'
  | '禁止追高'

export interface WatchlistSignalPayload {
  symbol: string
  name?: string
  market?: string
  quote?: Record<string, unknown>
  technical?: Record<string, unknown>
  position?: Record<string, unknown>
  news_flags?: string[]
  sector_strength?: number | null
  already_no_chase_today?: boolean
}

export interface WatchlistSignalResult {
  symbol: string
  name: string
  market: string
  label: WatchDecisionLabel
  score: number
  reasons: string[]
  risks: string[]
  confirm_conditions: string[]
  invalidation_conditions: string[]
  risk_level: string
  generated_at: string
}

export const watchlistApi = {
  evaluateSignal: (payload: WatchlistSignalPayload) =>
    fetchAPI<WatchlistSignalResult>('/watchlist/signals/evaluate', {
      method: 'POST',
      body: JSON.stringify(payload),
      timeoutMs: 30_000,
    }),
}

