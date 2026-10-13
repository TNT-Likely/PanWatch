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
  rank: number
  rank_percentile: number
  raw_score: number
  rank_change: number | null
  tradable: boolean
  reference_close: number | null
  factor_inputs: Record<string, number>
  explanation_kind: string
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
  model_id: string
  dataset_id: string
  experiment_id: string
  asof_date: string
  horizon: number
  data_kind: string
  config: QlibConfig
  predictions?: QlibPrediction[]
}
export interface QlibOpportunities {
  items: QlibPrediction[]
  status: string
  run: QlibRun | null
  mode: string
}
export interface QlibSchedule {
  enabled: boolean
  dataset_id: string | null
  daily_hour: number
  retrain_every_days: number
}
export interface QlibGovernance {
  champion_id: string | null
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
const post = <T>(path: string, body: unknown = {}) =>
  fetchAPI<T>(`/qlib/${path}`, { method: 'POST', body: JSON.stringify(body) })
export const qlibApi = {
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
  opportunities: () => fetchAPI<QlibOpportunities>('/qlib/opportunities'),
  governance: () => fetchAPI<QlibGovernance>('/qlib/governance'),
  experiments: () =>
    fetchAPI<{ items: QlibModel[] }>('/qlib/records/experiment'),
  selections: () => fetchAPI<{ items: QlibRun[] }>('/qlib/records/selection'),
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
