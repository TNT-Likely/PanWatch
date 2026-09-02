import { fetchAPI } from './client'

export type EvaluationHorizonUnit = 'trading_days' | 'calendar_days_legacy' | 'all'

export interface AgentPredictionOutcomeItem {
  status: string
  horizon_unit: Exclude<EvaluationHorizonUnit, 'all'>
  outcome_price: number | null
  return_pct: number | null
  hit: boolean | null
  evaluated_at: string
}

export interface AgentPredictionGroup {
  prediction_group_id: string
  is_legacy_group: boolean
  agent_name: string
  stock_symbol: string
  stock_market: string
  prediction_date: string
  action: string
  action_label: string
  confidence: number | null
  trigger_price: number | null
  reason: string
  signal: string
  created_at: string
  outcomes: Record<string, AgentPredictionOutcomeItem>
}

export interface AgentEvaluationPolicy {
  horizon_unit: 'trading_days'
  flat_threshold_pct: number
  actions: Record<string, string>
}

export interface AgentPredictionFilters {
  agentName?: string
  market?: string
  action?: string
  status?: string
  startDate?: string
  endDate?: string
  horizonUnit?: EvaluationHorizonUnit
  days?: number
  limit?: number
  offset?: number
}

export interface AgentPredictionListResponse {
  items: AgentPredictionGroup[]
  total: number
  available_filters: {
    agent_names: string[]
    markets: string[]
    actions: string[]
    statuses: string[]
    horizon_units: string[]
  }
  policy: AgentEvaluationPolicy
}

export interface AgentPredictionSummary {
  suggestion_count: number
  pending_count: number
  horizons: Record<string, {
    completed_count: number
    hit_count: number
    hit_rate: number | null
    avg_return_pct: number | null
  }>
  insufficient_sample: boolean
  policy: AgentEvaluationPolicy
}

export interface AgentPredictionEvaluationRun {
  total_pending: number
  eligible: number
  evaluated: number
  skipped_not_due: number
  skipped_invalid_date: number
  skipped_no_price: number
}

function withQuery(path: string, params: Record<string, string | number | undefined>) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') query.set(key, String(value))
  }
  const text = query.toString()
  return text ? `${path}?${text}` : path
}

export const evaluationsApi = {
  listAgentPredictions(filters: AgentPredictionFilters = {}) {
    return fetchAPI<AgentPredictionListResponse>(withQuery('/evaluations/agent-predictions', {
      agent_name: filters.agentName,
      market: filters.market,
      action: filters.action,
      status: filters.status,
      start_date: filters.startDate,
      end_date: filters.endDate,
      horizon_unit: filters.horizonUnit,
      days: filters.days,
      limit: filters.limit,
      offset: filters.offset,
    }))
  },

  getAgentPredictionSummary(filters: Omit<AgentPredictionFilters, 'limit' | 'offset'> = {}) {
    return fetchAPI<AgentPredictionSummary>(withQuery('/evaluations/agent-predictions/summary', {
      agent_name: filters.agentName,
      market: filters.market,
      action: filters.action,
      status: filters.status,
      start_date: filters.startDate,
      end_date: filters.endDate,
      horizon_unit: filters.horizonUnit,
      days: filters.days,
    }))
  },

  evaluateAgentPredictions() {
    return fetchAPI<AgentPredictionEvaluationRun>('/evaluations/agent-predictions/evaluate', {
      method: 'POST',
    })
  },
}
