import { fetchAPI } from './client'
import type { InvestmentProfile, InvestmentProfileEvaluateResult } from './investment-profile'

export type { InvestmentProfile, InvestmentProfileEvaluateResult } from './investment-profile'
export { DEFAULT_INVESTMENT_PROFILE } from './investment-profile'

export interface StockAgentInfo {
  agent_name: string
  schedule: string
  ai_model_id: number | null
  notify_channel_ids: number[]
}

export interface StockConceptTag {
  name: string
  source: 'auto' | 'manual' | string
}

export interface IndustryChainInfo {
  sector: string
  sector_label: string
  layer: 'foundation' | 'middleware' | 'integration' | 'application' | string
  layer_label: string
  display: string
  description?: string
  score?: number
  match_source?: string
  matched?: string[]
}

export interface StockItem {
  id: number
  symbol: string
  name: string
  market: string
  security_type?: string
  sort_order?: number
  is_featured?: boolean
  concept_tags?: StockConceptTag[]
  concept_tags_auto?: string[]
  concept_tags_manual?: string[]
  industry_chain?: IndustryChainInfo | null
  investment_profile?: InvestmentProfile
  agents?: StockAgentInfo[]
}

export interface StockCreatePayload {
  symbol: string
  name: string
  market: string
  security_type?: string
}

export interface EtfSpot {
  symbol: string
  name: string
  price: number | null
  iopv: number | null
  premium_pct: number | null
  change_pct: number | null
  turnover: number | null
  total_value: number | null
  turnover_rate: number | null
  volume: number | null
}

export interface EtfHolding {
  symbol: string
  name: string
  weight_pct: number
}

export interface EtfNavPoint {
  date: string
  unit_nav: number | null
  cum_nav: number | null
  change_pct: number | null
}

export interface EtfOverview {
  symbol: string
  spot: EtfSpot | null
  holdings: EtfHolding[]
  nav_history: EtfNavPoint[]
}

export interface StockAgentUpdatePayload {
  agents: Array<{
    agent_name: string
    schedule?: string
    ai_model_id?: number | null
    notify_channel_ids?: number[]
  }>
}

export interface TriggerStockAgentOptions {
  bypass_throttle?: boolean
  bypass_market_hours?: boolean
  allow_unbound?: boolean
  wait?: boolean
  symbol?: string
  market?: string
  name?: string
}

export interface TriggerStockAgentResponse {
  result?: Record<string, any>
  code?: number
  success?: boolean
  message: string
  queued?: boolean
  deduplicated?: boolean
  trace_id?: string
}

function withQuery(path: string, params: TriggerStockAgentOptions): string {
  const q = new URLSearchParams()
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v === undefined || v === null) return
    const sv = String(v).trim()
    if (!sv) return
    q.set(k, sv)
  })
  const s = q.toString()
  return s ? `${path}?${s}` : path
}

export const stocksApi = {
  list: () => fetchAPI<StockItem[]>('/stocks'),
  create: (payload: StockCreatePayload) =>
    fetchAPI<StockItem>('/stocks', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  etfOverview: (code: string, top = 30, navDays = 180) =>
    fetchAPI<EtfOverview>(
      `/stocks/etf/${encodeURIComponent(code)}/overview?top=${top}&nav_days=${navDays}`,
      { timeoutMs: 30_000 }
    ),
  remove: (id: number) => fetchAPI<{ ok: boolean }>(`/stocks/${id}`, { method: 'DELETE' }),
  updateConceptTags: (id: number, manual: string[]) =>
    fetchAPI<StockItem>(`/stocks/${id}/concept-tags`, {
      method: 'PUT',
      body: JSON.stringify({ manual }),
    }),
  refreshConceptTags: (id: number) =>
    fetchAPI<StockItem>(`/stocks/${id}/concept-tags/refresh`, { method: 'POST' }),
  refreshMissingConceptTags: (limit = 20) =>
    fetchAPI<{ queued: boolean; limit: number }>('/stocks/concept-tags/refresh', {
      method: 'POST',
      body: JSON.stringify({ limit }),
    }),
  refreshIndustryChains: (limit = 50) =>
    fetchAPI<{ queued: boolean; limit: number }>('/stocks/industry-chains/refresh', {
      method: 'POST',
      body: JSON.stringify({ limit }),
    }),
  refreshIndustryChain: (id: number) =>
    fetchAPI<StockItem>(`/stocks/${id}/industry-chain/refresh`, { method: 'POST' }),
  updateAgents: (id: number, payload: StockAgentUpdatePayload) =>
    fetchAPI<StockItem>(`/stocks/${id}/agents`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  triggerAgent: (id: number, agentName: string, options: TriggerStockAgentOptions = {}) =>
    fetchAPI<TriggerStockAgentResponse>(
      withQuery(`/stocks/${id}/agents/${encodeURIComponent(agentName)}/trigger`, options),
      { method: 'POST', timeoutMs: 120_000 }
    ),
  ensureLmdReport: (id: number) =>
    fetchAPI<{ has_report: boolean; queued: boolean; deduplicated?: boolean; message?: string }>(
      `/stocks/${id}/agents/lmd_outlook/ensure`,
      { method: 'POST' },
    ),
  getInvestmentProfile: (id: number) =>
    fetchAPI<{ stock_id: number; symbol: string; market: string; investment_profile: InvestmentProfile; portfolio_role_label: string }>(
      `/stocks/${id}/investment-profile`,
    ),
  updateInvestmentProfile: (id: number, profile: Partial<InvestmentProfile>) =>
    fetchAPI<StockItem>(`/stocks/${id}/investment-profile`, {
      method: 'PUT',
      body: JSON.stringify(profile),
    }),
  setFeatured: (id: number, isFeatured: boolean) =>
    fetchAPI<StockItem>(`/stocks/${id}/featured`, {
      method: 'PUT',
      body: JSON.stringify({ is_featured: isFeatured }),
    }),
  evaluateInvestmentProfile: (id: number, price?: number) => {
    const q = price != null && price > 0 ? `?price=${price}` : ''
    return fetchAPI<InvestmentProfileEvaluateResult>(`/stocks/${id}/investment-profile/evaluate${q}`)
  },
}
