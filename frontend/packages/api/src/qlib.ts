import { fetchAPI } from './client'

export interface QlibConfig {
  algorithm: 'lightgbm' | 'ridge'
  horizon: number
  topk: number
  train_fraction: number
  valid_fraction: number
  open_cost: number
  close_cost: number
  min_cost: number
  capital: number
  limit_threshold: number
  max_drawdown: number
  min_ic: number
  min_test_days: number
  num_boost_round: number
}
export const defaultQlibConfig: QlibConfig = {
  algorithm: 'lightgbm',
  horizon: 5,
  topk: 10,
  train_fraction: 0.6,
  valid_fraction: 0.15,
  open_cost: 0.0003,
  close_cost: 0.0008,
  min_cost: 5,
  capital: 1000000,
  limit_threshold: 0.095,
  max_drawdown: 0.3,
  min_ic: 0,
  min_test_days: 20,
  num_boost_round: 80,
}
export interface QlibPrediction {
  symbol: string
  market: string
  currency?: string
  paper_eligible?: boolean
  portfolio_candidate?: boolean
  rank: number
  rank_percentile: number
  raw_score: number
  rank_change: number | null
  tradable: boolean
  reference_close: number | null
  factor_inputs: Record<string, number>
  explanation_kind: string
  paper_block_reason?: string
  observed_context?: {
    observations: number
    return_20_observations: number
    volume_ratio_20_observations: number | null
    drawdown_from_60_observation_high: number
    basis: string
  } | null
}
export interface QlibDataset {
  id: string
  name: string
  source: string
  license: string
  data_kind: string
  universe_kind: string
  adjustment: string
  start_date: string
  end_date: string
  rows: number
  dates: number
  symbols: string[]
  content_hash: string
  warnings: string[]
}
export interface QlibBacktest {
  days: number
  return: number | null
  max_drawdown: number | null
  sharpe: number | null
  turnover_mean: number
  curve: { date: string; equity: number }[]
  policy: string
  benchmark: string
}
export interface QlibMetrics {
  rank_ic: number | null
  icir: number | null
  ic_dates: number
  security_days: number
  backtest: QlibBacktest
}
export interface QlibModel {
  id: string
  name: string
  market?: 'CN' | 'HK' | 'US'
  universe_snapshot_id?: string | null
  created_at: string
  state?: 'champion' | 'previous' | 'candidate' | 'retired'
  dataset_id: string
  experiment_id: string
  config: QlibConfig
  metrics: QlibMetrics
  baseline: QlibMetrics
  data_kind: string
  adjustment: string
  universe_kind: string
  segments: Record<string, [string, string]>
  runtime: Record<string, string>
  artifact_hash: string
  replay_fingerprint: string
  rolling: {
    segments: Record<string, [string, string]>
    model_available_after: string
    metrics: QlibMetrics
  }[]
}
export interface QlibJob {
  market?: QlibMarket
  id: string
  kind: string
  dataset_id: string
  model_id: string | null
  config: QlibConfig
  status: string
  error_code: string
  result_id: string | null
  created_at: string
}
export interface QlibRun {
  id: string
  market?: string
  prediction_count?: number
  watched_count?: number
  catalog_count?: number
  ready_stocks?: number
  training_stocks?: number
  model_id: string
  dataset_id: string
  experiment_id: string
  asof_date: string
  horizon: number
  data_kind: string
  config: QlibConfig
  predictions?: QlibPrediction[]
  names?: Record<string, string>
}
export interface QlibMarketDataSource {
  symbols: string[]
  vendor: 'auto' | 'tencent' | 'eastmoney'
  license: string
  license_acknowledged: true
  automatic: boolean
}
export interface QlibSyncJob {
  id: string
  status: string
  progress: {
    completed: number
    total: number
    phase: string
    vendor?: string
    symbol?: string
  }
  dataset_id: string | null
  train_job_id: string | null
  predict_job_id: string | null
  error_code: string
}
export interface QlibDataSourceStatus {
  source: QlibMarketDataSource | null
  dataset_id: string | null
  vendors: string[]
  job: QlibSyncJob | null
}
export interface QlibOpportunities {
  items: QlibPrediction[]
  status: string
  run: QlibRun | null
  mode: string
  total?: number
  prediction_count?: number
}
export interface QlibSchedule {
  enabled: boolean
  dataset_id: string | null
  daily_hour: number
  retrain_every_days: number
}
export interface QlibGovernance {
  champion_id: string | null
  champions?: Record<string, string | null>
  revision: number
  settings: QlibSchedule
  models: QlibModel[]
  audit: {
    id: number
    action: string
    actor: string
    target_id: string | null
    created_at: string
    detail: Record<string, unknown>
  }[]
}
export interface QlibHealth {
  available: boolean
  error_code: string
  runtime: Record<string, string> | null
  monitor: {
    forward?: {
      champion_closed_trades: number
      champion_realized_net_pnl: number
      champion_open_positions: number
      champion_unrealized_net_pnl: number
      qlib_equity_exposure: number | null
      max_position_equity_weight: number | null
      filled_intents: number
      rejected_intents: number
      asof: string
    }
    fresh: boolean
    prediction_date: string | null
    topk_change_fraction: number | null
    coverage: {
      fraction?: number
      expected?: number
      predicted?: number
      input_abs_z_mean?: number
      input_shift_warning?: boolean
    }
    recent_failures: { id: string; error_code: string }[]
  }
}
export interface QlibDecision {
  id: string
  run_id: string
  model_id: string
  asof_date: string
  execution_date: string
  policy: string
  symbols: string[]
  target_weights: Record<string, number>
  research_mode: boolean
}
export interface QlibPlanOrder {
  symbol: string
  name: string
  side: 'buy' | 'sell'
  reference_price: number
  estimated_quantity: number
  estimated_fee: number
  estimated_cash_delta: number
  cash_limit: number | null
  position_id: number | null
}
export interface QlibPlanPreview {
  market_equity?: number
  current_exposure?: number
  target_exposure?: number
  run_id: string
  model_id: string
  market: string
  asof_date: string
  execution_date: string | null
  execution_window: string
  research_mode: boolean
  ready: boolean
  blocking_reason: string
  orders: QlibPlanOrder[]
  kept: { symbol: string; quantity: number }[]
  excluded: { symbol: string; reason: string }[]
  excluded_count?: number
  cash_budget: number
  available_cash: number
  estimated_fees: number
  account_enabled: boolean
  existing_decision_id: string | null
  replaces_decision_id?: string | null
  preview_hash: string | null
  pool_count: number
  ranked_count: number
}
export interface QlibPaperPlan {
  block_reason?: string
  approval_origin?: 'manual' | 'automatic'
  automation_grant_id?: string | null
  id: string
  run_id: string
  model_id: string
  asof_date: string
  execution_date: string
  status: string
  research_mode: boolean
  cash_budget: number | null
  orders: QlibPlanOrder[]
  symbols: string[]
  filled: number
  rejected: number
  can_cancel: boolean
  events: {
    symbol: string
    side: string
    status: string
    quantity: number
    price: number | null
    reason: string
  }[]
}
export interface QlibExecution {
  id: string
  decision_id: string
  run_id: string
  model_id: string
  symbol: string
  side: string
  status: string
  quantity: number
  price: number | null
  reason: string
  position_id: number | null
}
export interface QlibPaper {
  items: QlibExecution[]
  decisions: QlibDecision[]
  positions: {
    id: number
    symbol: string
    quantity: number
    status: string
    research: Record<string, unknown>
  }[]
}
export interface QlibTemplate {
  id: string
  name: string
  config: QlibConfig
}
export interface QlibBasketItem {
  symbol: string
  run_id: string
}
export type QlibMarket = 'CN' | 'HK' | 'US'
export interface QlibUniverseConfig {
  markets: QlibMarket[]
  license: string
  license_acknowledged: true
  automatic: boolean
  training_stocks: number
}
export interface QlibUniverseJob {
  id: string
  kind: string
  market: QlibMarket | null
  status: string
  progress: {
    phase: string
    completed: number
    total: number
    symbol?: string
    market?: string
    errors?: Record<string, string>
  }
  error_code: string
  resumable: boolean
  result_id: string | null
}
export interface QlibUniverseMember {
  quality_warnings?: Record<string, number>
  symbol: string
  name: string
  code: string
  market: QlibMarket
  currency: string
  board: string
  lot: number
  status: string
  error_code: string
  asof_date: string | null
}
export interface QlibUniverseStatus {
  config: QlibUniverseConfig | null
  markets: Record<
    QlibMarket,
    {
      total: number
      counts: Record<string, number>
      reasons: Record<string, number>
      vendors: string[]
      champion_id: string | null
      completed_day: string
      predicted: number
      prediction_date: string | null
      catalog: {
        source: string
        source_date: string
        total: number
        raw_count: number
        historical_membership: boolean
      } | null
    }
  >
  jobs: QlibUniverseJob[]
}
const post = <T>(path: string, body: unknown = {}) =>
  fetchAPI<T>(`/qlib/${path}`, { method: 'POST', body: JSON.stringify(body) })
export interface QlibAutomationStatus {
  bound_model_id?: string | null
  mode: 'automatic' | 'manual'
  authorized: boolean
  model_id: string | null
  model_name: string | null
  account_enabled: boolean
  can_authorize: boolean
  capital: { account_id: number; initial_capital: number; allocations: Record<string, number>; market_principal: number; target_ratio: number; capital_policy: string; capital_config_hash: string } | null
  bound_market_principal: number | null
  expires_at: string | null
  grant_id: string | null
  phase: string
  reason: string
  latest_decision_id: string | null
}
export const qlibApi = {
  automationStatus: () => fetchAPI<QlibAutomationStatus>('/qlib/paper-automation'),
  configureAutomation: (body: { mode: 'automatic' | 'manual'; enabled?: boolean; request_key?: string; model_id?: string; capital_config_hash?: string; days?: number; acknowledge_research?: boolean }) => fetchAPI<QlibAutomationStatus>('/qlib/paper-automation', { method: 'PUT', body: JSON.stringify(body) }),
  universe: () => fetchAPI<QlibUniverseStatus>('/qlib/universe'),
  configureUniverse: (body: QlibUniverseConfig) =>
    fetchAPI<QlibUniverseStatus>('/qlib/universe', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  universeJob: (
    kind: 'catalog' | 'history' | 'train' | 'predict',
    market: QlibMarket | null = null,
    config = defaultQlibConfig,
    reinitialize = false,
  ) =>
    post<QlibUniverseJob>(`universe/jobs/${kind}`, {
      request_key: `universe-${crypto.randomUUID()}`,
      market,
      config,
      reinitialize,
    }),
  universeJobAction: (id: string, action: 'cancel' | 'resume') =>
    post<QlibUniverseJob>(`universe/jobs/${id}/${action}`),
  universeMembers: (market: QlibMarket, page = 1, search = '', status = '') =>
    fetchAPI<{ total: number; items: QlibUniverseMember[] }>(
      `/qlib/universe/members?${new URLSearchParams({ market, page: String(page), limit: '100', search, status })}`,
    ),
  dataSource: () => fetchAPI<QlibDataSourceStatus>('/qlib/data-source'),
  configureDataSource: (body: QlibMarketDataSource) =>
    fetchAPI<QlibDataSourceStatus>('/qlib/data-source', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  syncDataSource: (
    request_key: string,
    train_when_ready = false,
    reinitialize = false,
  ) =>
    post<QlibSyncJob>('data-source/sync', {
      request_key,
      train_when_ready,
      reinitialize,
    }),
  cancelDataSync: (id: string) =>
    post<QlibSyncJob>(`data-source/jobs/${id}/cancel`),
  health: () => fetchAPI<QlibHealth>('/qlib/health'),
  datasets: () => fetchAPI<{ items: QlibDataset[] }>('/qlib/datasets'),
  importDataset: (data: unknown) => post<QlibDataset>('datasets', data),
  jobs: () => fetchAPI<{ items: QlibJob[] }>('/qlib/jobs'),
  train: (body: {
    dataset_id: string
    config: QlibConfig
    request_key: string
  }) => post<QlibJob>('jobs', body),
  predict: (model: string, dataset_id: string, request_key: string) =>
    post<QlibJob>(`models/${model}/predict`, { dataset_id, request_key }),
  cancel: (id: string) => post<QlibJob>(`jobs/${id}/cancel`),
  opportunities: (query?: Record<string, string>) =>
    fetchAPI<QlibOpportunities>(
      `/qlib/opportunities${query ? `?${new URLSearchParams(query)}` : ''}`,
    ),
  governance: () => fetchAPI<QlibGovernance>('/qlib/governance'),
  experiments: () =>
    fetchAPI<{ items: QlibModel[] }>('/qlib/records/experiment'),
  selections: (market?: QlibMarket) =>
    fetchAPI<{ items: QlibRun[] }>(
      `/qlib/records/selection${market ? `?metadata_only=true&market=${market}` : ''}`,
    ),
  export: (id: string) => fetchAPI<QlibModel>(`/qlib/experiments/${id}/export`),
  compare: (ids: string[]) =>
    post<{ comparable: boolean; reason: string; items: QlibModel[] }>(
      'experiments/compare',
      { ids },
    ),
  publish: (
    id: string,
    revision: number,
    reason: string,
    mode: 'research' | 'validated',
    rollback = false,
  ) =>
    post<QlibGovernance>(`models/${id}/${rollback ? 'rollback' : 'publish'}`, {
      revision,
      reason,
      mode,
    }),
  retire: (id: string, revision: number, reason: string) =>
    post<QlibGovernance>(`models/${id}/retire`, { revision, reason }),
  decision: (id: string, acknowledge_research: boolean) =>
    post<QlibDecision>(`selections/${id}/decision`, { acknowledge_research }),
  previewPlan: (id: string) =>
    fetchAPI<QlibPlanPreview>(`/qlib/selections/${id}/plan`),
  confirmPlan: (
    id: string,
    preview_hash: string,
    acknowledge_research: boolean,
  ) =>
    post<QlibDecision>(`selections/${id}/plan`, {
      preview_hash,
      acknowledge_research,
    }),
  paperPlans: () =>
    fetchAPI<{ items: QlibPaperPlan[]; account_enabled: boolean }>(
      `/qlib/paper-plans`,
    ),
  cancelPlan: (id: string) =>
    post<{ id: string; status: string }>(`paper-plans/${id}/cancel`),
  templates: () => fetchAPI<{ items: QlibTemplate[] }>('/qlib/templates'),
  saveTemplate: (name: string, config: QlibConfig) =>
    post<QlibTemplate>('templates', { name, config }),
  schedule: (body: QlibSchedule) =>
    fetchAPI<QlibGovernance>('/qlib/schedule', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  tick: () => post<{ status: string }>('schedule/tick'),
  executions: () => fetchAPI<QlibPaper>('/qlib/executions'),
  basket: () => fetchAPI<{ items: QlibBasketItem[] }>('/qlib/basket'),
  setBasket: (run_id: string, symbol: string, included: boolean) =>
    fetchAPI<{ items: QlibBasketItem[] }>('/qlib/basket', {
      method: 'PUT',
      body: JSON.stringify({ run_id, symbol, included }),
    }),
}
