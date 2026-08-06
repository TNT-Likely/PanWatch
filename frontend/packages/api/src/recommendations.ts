import { fetchAPI } from './client'

export interface EntryCandidateItem {
  id: number
  stock_symbol: string
  stock_market: string
  stock_name: string
  snapshot_date: string
  status: string
  score: number
  confidence?: number | null
  action: string
  action_label: string
  candidate_source?: string
  candidate_source_label?: string
  strategy_tags?: string[]
  strategy_labels?: string[]
  is_holding_snapshot?: boolean
  plan_quality?: number
  signal: string
  reason: string
  entry_low?: number | null
  entry_high?: number | null
  stop_loss?: number | null
  target_price?: number | null
  invalidation?: string
  source_agent?: string
  source_agent_label?: string
  source_suggestion_id?: number | null
  evidence?: string[]
  plan?: Record<string, any>
  meta?: Record<string, any>
  created_at?: string
  updated_at?: string
}

export interface EntryCandidatesResponse {
  snapshot_date: string
  count: number
  items: EntryCandidateItem[]
}

export interface EntryCandidateFeedbackPayload {
  snapshot_date?: string
  stock_symbol: string
  stock_market?: string
  useful: boolean
  candidate_source?: string
  strategy_tags?: string[]
  reason?: string
}

export interface EntryCandidateStatsResponse {
  window_days: number
  feedback: {
    total: number
    useful: number
    useless: number
    useful_rate: number
  }
  by_source: Array<{
    source: string
    source_label: string
    total: number
    useful: number
    useful_rate: number
  }>
  by_market: Array<{
    market: string
    total: number
    useful: number
    useful_rate: number
  }>
  by_strategy: Array<{
    strategy: string
    strategy_label: string
    total: number
    useful: number
    useless: number
    useful_rate: number
  }>
  coverage: {
    snapshot_date: string
    total_snapshot_candidates?: number
    total_active: number
    market_scan_active: number
    watchlist_active: number
    held_active?: number
    unheld_active?: number
    new_active_from_prev?: number
    dropped_from_prev?: number
    previous_snapshot_date?: string
    observing_candidates?: number
    market_scan_share_pct?: number
  }
  outcomes?: Array<{
    horizon_days: number
    source: string
    source_label: string
    total: number
    wins: number
    win_rate: number
    avg_return_pct: number
  }>
}

export interface StrategyCatalogItem {
  code: string
  name: string
  description: string
  version: string
  enabled: boolean
  market_scope: string
  risk_level: string
  params: Record<string, any>
  default_weight: number
}

export interface StrategySignalItem {
  id: number
  snapshot_date: string
  stock_symbol: string
  stock_market: string
  stock_name: string
  strategy_code: string
  strategy_name: string
  strategy_version: string
  risk_level: string
  risk_level_label: string
  source_pool: string
  source_pool_label: string
  score: number
  rank_score: number
  confidence?: number | null
  status: string
  action: string
  action_label: string
  signal: string
  reason: string
  evidence?: string[]
  holding_days?: number
  entry_low?: number | null
  entry_high?: number | null
  stop_loss?: number | null
  target_price?: number | null
  invalidation?: string
  plan_quality?: number
  source_agent?: string
  source_suggestion_id?: number | null
  source_candidate_id?: number | null
  trace_id?: string
  is_holding_snapshot?: boolean
  context_quality_score?: number | null
  score_breakdown?: {
    base_score?: number
    alpha_score?: number
    catalyst_score?: number
    quality_score?: number
    risk_penalty?: number
    crowd_penalty?: number
    source_bonus?: number
    regime?: string
    regime_label?: string
    regime_multiplier?: number
    raw_score?: number
    weighted_score?: number
    weight?: number
    has_entry_plan?: boolean
  }
  ai_score?: number
  factor_explain?: {
    positive?: { factor: string; label: string; contribution: number }[]
    negative?: { factor: string; label: string; contribution: number }[]
  }
  market_regime?: {
    regime?: string
    regime_label?: string
    confidence?: number
    regime_score?: number
  }
  cross_feature?: {
    market?: string
    score_pct?: number
    change_pct_rank?: number
    turnover_pct_rank?: number
    volume_pct_rank?: number
    relative_strength_pct?: number
    crowding_risk?: number
  }
  news_metric?: {
    news_count?: number
    high_importance_count?: number
    importance_weighted?: number
    event_bias_sum?: number
    latest_age_hours?: number | null
    event_score?: number
    event_bias?: number
    event_tier?: string
  }
  constrained?: boolean
  constraint_reasons?: string[]
  payload?: Record<string, any>
  created_at?: string
  updated_at?: string
}

export interface StrategySignalsResponse {
  snapshot_date: string
  count: number
  items: StrategySignalItem[]
  queued?: boolean
  running?: boolean
  accepted?: boolean
  message?: string
}

export interface StrategyRefreshStatusResponse {
  running: boolean
  started_at: string
  finished_at: string
  last_error: string
  last_snapshot_date: string
}

export interface StrategyRegimeSnapshot {
  id: number
  snapshot_date: string
  market: string
  regime: string
  regime_label: string
  regime_score: number
  confidence: number
  breadth_up_pct?: number | null
  avg_change_pct?: number | null
  volatility_pct?: number | null
  active_ratio?: number | null
  sample_size?: number
  meta?: Record<string, any>
  created_at?: string
  updated_at?: string
}

export interface StrategyRegimesResponse {
  count: number
  items: StrategyRegimeSnapshot[]
}

export interface PortfolioRiskSnapshotItem {
  id: number
  snapshot_date: string
  market: string
  total_signals: number
  active_signals: number
  held_signals: number
  unheld_signals: number
  high_risk_ratio?: number | null
  concentration_top5?: number | null
  avg_rank_score?: number | null
  risk_level: string
  meta?: Record<string, any>
  created_at?: string
  updated_at?: string
}

export interface PortfolioRiskSnapshotsResponse {
  count: number
  items: PortfolioRiskSnapshotItem[]
}

export interface StrategyFactorSnapshot {
  id: number
  signal_run_id: number
  snapshot_date: string
  stock_symbol: string
  stock_market: string
  strategy_code: string
  alpha_score: number
  catalyst_score: number
  quality_score: number
  risk_penalty: number
  crowd_penalty: number
  source_bonus: number
  regime_multiplier: number
  final_score: number
  factor_payload?: Record<string, any>
  created_at?: string
  updated_at?: string
}

export interface StrategyStatsResponse {
  window_days: number
  coverage: {
    snapshot_date: string
    total_signals: number
    active_signals: number
    watchlist_signals: number
    market_scan_signals: number
    market_scan_share_pct: number
    mixed_signals?: number
  }
  constraints?: {
    constrained_top20?: number
  }
  factor_stats?: {
    avg_alpha_score: number
    avg_catalyst_score: number
    avg_quality_score: number
    avg_risk_penalty: number
    avg_crowd_penalty: number
    sample_size: number
  }
  regimes?: StrategyRegimeSnapshot[]
  portfolio_risk?: PortfolioRiskSnapshotItem[]
  by_strategy: Array<{
    strategy_code: string
    strategy_name: string
    strategy_version: string
    market: string
    risk_level: string
    risk_level_label: string
    horizon_days: number
    sample_size: number
    wins: number
    win_rate: number
    avg_return_pct: number
    default_weight: number
    current_weight: number
  }>
  by_market: Array<{
    market: string
    total: number
    wins: number
    win_rate: number
    avg_return_pct: number
  }>
  weight_updates: {
    window_days: number
    changed: number
  }
  top_signals: StrategySignalItem[]
}

export interface StrategyWeightHistoryItem {
  id: number
  strategy_code: string
  market: string
  regime: string
  old_weight: number
  new_weight: number
  reason: string
  window_days: number
  sample_size: number
  meta?: Record<string, any>
  created_at: string
}

export interface StrategyWeightHistoryResponse {
  count: number
  items: StrategyWeightHistoryItem[]
}

const appendQuery = (q: URLSearchParams, key: string, value: unknown) => {
  if (value == null) return
  if (typeof value === 'string') {
    const v = value.trim()
    if (!v) return
    q.set(key, v)
    return
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    q.set(key, String(value))
    return
  }
  if (typeof value === 'boolean') {
    q.set(key, value ? 'true' : 'false')
  }
}

export const recommendationsApi = {
  listEntryCandidates: (params?: {
    market?: string
    status?: 'active' | 'inactive' | 'all'
    min_score?: number
    limit?: number
    refresh?: boolean
    snapshot_date?: string
    source?: 'market_scan' | 'watchlist' | 'mixed' | 'all'
    holding?: 'held' | 'unheld' | 'all'
    strategy?: string
    trend?: string
    macd?: string
    rsi?: string
    kdj?: string
    volume_ratio_min?: number
    change_pct_min?: number
    change_pct_max?: number
    timeoutMs?: number
  }) => {
    const q = new URLSearchParams()
    if (params?.market) q.set('market', params.market)
    if (params?.status) q.set('status', params.status)
    if (typeof params?.min_score === 'number') q.set('min_score', String(params.min_score))
    if (typeof params?.limit === 'number') q.set('limit', String(params.limit))
    if (typeof params?.refresh === 'boolean') q.set('refresh', String(params.refresh))
    if (params?.snapshot_date) q.set('snapshot_date', params.snapshot_date)
    if (params?.source) q.set('source', params.source)
    if (params?.holding) q.set('holding', params.holding)
    if (params?.strategy) q.set('strategy', params.strategy)
    if (params?.trend) q.set('trend', params.trend)
    if (params?.macd) q.set('macd', params.macd)
    if (params?.rsi) q.set('rsi', params.rsi)
    if (params?.kdj) q.set('kdj', params.kdj)
    if (typeof params?.volume_ratio_min === 'number') q.set('volume_ratio_min', String(params.volume_ratio_min))
    if (typeof params?.change_pct_min === 'number') q.set('change_pct_min', String(params.change_pct_min))
    if (typeof params?.change_pct_max === 'number') q.set('change_pct_max', String(params.change_pct_max))
    const qs = q.toString()
    return fetchAPI<EntryCandidatesResponse>(
      `/recommendations/entry-candidates${qs ? `?${qs}` : ''}`,
      {
        timeoutMs: params?.timeoutMs,
      }
    )
  },

  refreshEntryCandidates: (maxInputs = 300, marketScanLimit = 60) =>
    fetchAPI<EntryCandidatesResponse>(
      `/recommendations/entry-candidates/refresh?max_inputs=${encodeURIComponent(String(maxInputs))}&market_scan_limit=${encodeURIComponent(String(marketScanLimit))}`,
      {
        method: 'POST',
      }
    ),

  feedbackEntryCandidate: (payload: EntryCandidateFeedbackPayload) =>
    fetchAPI<{ ok: boolean }>('/recommendations/entry-candidates/feedback', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  getEntryCandidateStats: (days = 30) =>
    fetchAPI<EntryCandidateStatsResponse>(`/recommendations/entry-candidates/stats?days=${encodeURIComponent(String(days))}`),

  evaluateEntryCandidateOutcomes: (limit = 400, snapshotDays = 45) =>
    fetchAPI<Record<string, number>>(
      `/recommendations/entry-candidates/outcomes/evaluate?limit=${encodeURIComponent(String(limit))}&snapshot_days=${encodeURIComponent(String(snapshotDays))}`,
      {
        method: 'POST',
      }
    ),

  listStrategyCatalog: (enabledOnly = true) =>
    fetchAPI<{ items: StrategyCatalogItem[] }>(
      `/recommendations/strategy-catalog?enabled_only=${enabledOnly ? 'true' : 'false'}`
    ),

  listStrategySignals: (params?: {
    market?: string
    status?: 'active' | 'inactive' | 'all'
    min_score?: number
    limit?: number
    snapshot_date?: string
    source_pool?: 'market_scan' | 'watchlist' | 'mixed' | 'all'
    holding?: 'held' | 'unheld' | 'all'
    strategy_code?: string
    risk_level?: 'low' | 'medium' | 'high' | 'all'
    include_payload?: boolean
    trend?: string
    macd?: string
    rsi?: string
    kdj?: string
    volume_ratio_min?: number
    change_pct_min?: number
    change_pct_max?: number
    regime?: string
    strategy_tag?: string
    timeoutMs?: number
  }) => {
    const q = new URLSearchParams()
    appendQuery(q, 'market', params?.market)
    appendQuery(q, 'status', params?.status)
    appendQuery(q, 'min_score', params?.min_score)
    appendQuery(q, 'limit', params?.limit)
    appendQuery(q, 'snapshot_date', params?.snapshot_date)
    appendQuery(q, 'source_pool', params?.source_pool)
    appendQuery(q, 'holding', params?.holding)
    appendQuery(q, 'strategy_code', params?.strategy_code)
    appendQuery(q, 'risk_level', params?.risk_level)
    appendQuery(q, 'include_payload', params?.include_payload)
    appendQuery(q, 'trend', params?.trend)
    appendQuery(q, 'macd', params?.macd)
    appendQuery(q, 'rsi', params?.rsi)
    appendQuery(q, 'kdj', params?.kdj)
    appendQuery(q, 'volume_ratio_min', params?.volume_ratio_min)
    appendQuery(q, 'change_pct_min', params?.change_pct_min)
    appendQuery(q, 'change_pct_max', params?.change_pct_max)
    appendQuery(q, 'regime', params?.regime)
    appendQuery(q, 'strategy_tag', params?.strategy_tag)
    const qs = q.toString()
    return fetchAPI<StrategySignalsResponse>(
      `/recommendations/strategy-signals${qs ? `?${qs}` : ''}`,
      {
        timeoutMs: params?.timeoutMs,
      }
    )
  },

  listStrategyRegimes: (params?: {
    snapshot_date?: string
    market?: string
    limit?: number
  }) => {
    const q = new URLSearchParams()
    appendQuery(q, 'snapshot_date', params?.snapshot_date)
    appendQuery(q, 'market', params?.market)
    appendQuery(q, 'limit', params?.limit)
    const qs = q.toString()
    return fetchAPI<StrategyRegimesResponse>(`/recommendations/strategy-regimes${qs ? `?${qs}` : ''}`)
  },

  listStrategyRiskSnapshots: (params?: {
    snapshot_date?: string
    market?: string
    limit?: number
  }) => {
    const q = new URLSearchParams()
    appendQuery(q, 'snapshot_date', params?.snapshot_date)
    appendQuery(q, 'market', params?.market)
    appendQuery(q, 'limit', params?.limit)
    const qs = q.toString()
    return fetchAPI<PortfolioRiskSnapshotsResponse>(`/recommendations/strategy-risk-snapshots${qs ? `?${qs}` : ''}`)
  },

  getStrategyFactorSnapshot: (signalRunId: number) =>
    fetchAPI<StrategyFactorSnapshot>(`/recommendations/strategy-factors/${encodeURIComponent(String(signalRunId))}`),

  refreshStrategySignals: (params?: {
    rebuild_candidates?: boolean
    snapshot_date?: string
    max_inputs?: number
    market_scan_limit?: number
    max_kline_symbols?: number
    limit_candidates?: number
    wait?: boolean
  }) => {
    const q = new URLSearchParams()
    appendQuery(q, 'rebuild_candidates', params?.rebuild_candidates ?? true)
    appendQuery(q, 'snapshot_date', params?.snapshot_date)
    appendQuery(q, 'max_inputs', params?.max_inputs)
    appendQuery(q, 'market_scan_limit', params?.market_scan_limit)
    appendQuery(q, 'max_kline_symbols', params?.max_kline_symbols)
    appendQuery(q, 'limit_candidates', params?.limit_candidates)
    appendQuery(q, 'wait', params?.wait ?? false)
    const qs = q.toString()
    return fetchAPI<StrategySignalsResponse>(
      `/recommendations/strategy-signals/refresh${qs ? `?${qs}` : ''}`,
      {
        method: 'POST',
        timeoutMs: 120000,
      }
    )
  },

  getStrategyRefreshStatus: () =>
    fetchAPI<StrategyRefreshStatusResponse>('/recommendations/strategy-signals/refresh-status'),

  evaluateStrategyOutcomes: (limit = 800, snapshotDays = 60) =>
    fetchAPI<Record<string, number>>(
      `/recommendations/strategy-signals/outcomes/evaluate?limit=${encodeURIComponent(String(limit))}&snapshot_days=${encodeURIComponent(String(snapshotDays))}`,
      {
        method: 'POST',
        timeoutMs: 45000,
      }
    ),

  rebalanceStrategyWeights: (windowDays = 45, minSamples = 8, alpha = 0.35) =>
    fetchAPI<Record<string, any>>(
      `/recommendations/strategy-weights/rebalance?window_days=${encodeURIComponent(String(windowDays))}&min_samples=${encodeURIComponent(String(minSamples))}&alpha=${encodeURIComponent(String(alpha))}`,
      {
        method: 'POST',
      }
    ),

  getStrategyStats: (days = 45) =>
    fetchAPI<StrategyStatsResponse>(
      `/recommendations/strategy-stats?days=${encodeURIComponent(String(days))}`,
      { timeoutMs: 45000 }
    ),

  listStrategyWeightHistory: (params?: {
    strategy_code?: string
    market?: string
    limit?: number
  }) => {
    const q = new URLSearchParams()
    appendQuery(q, 'strategy_code', params?.strategy_code)
    appendQuery(q, 'market', params?.market)
    appendQuery(q, 'limit', params?.limit)
    const qs = q.toString()
    return fetchAPI<StrategyWeightHistoryResponse>(`/recommendations/strategy-weight-history${qs ? `?${qs}` : ''}`)
  },
}
