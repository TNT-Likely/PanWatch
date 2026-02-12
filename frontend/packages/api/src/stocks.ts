import { fetchAPI } from './client'

export interface StockAgentInfo {
  agent_name: string
  schedule: string
  ai_model_id: number | null
  notify_channel_ids: number[]
}

export interface StockItem {
  id: number
  symbol: string
  name: string
  market: string
  sort_order?: number
  agents?: StockAgentInfo[]
}

export interface StockCreatePayload {
  symbol: string
  name: string
  market: string
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
  symbol?: string
  market?: string
  name?: string
}

export interface TriggerStockAgentResponse {
  result: Record<string, any>
  code: number
  success: boolean
  message: string
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
  remove: (id: number) => fetchAPI<{ ok: boolean }>(`/stocks/${id}`, { method: 'DELETE' }),
  updateAgents: (id: number, payload: StockAgentUpdatePayload) =>
    fetchAPI<StockItem>(`/stocks/${id}/agents`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  triggerAgent: (id: number, agentName: string, options: TriggerStockAgentOptions = {}) =>
    fetchAPI<TriggerStockAgentResponse>(
      withQuery(`/stocks/${id}/agents/${encodeURIComponent(agentName)}/trigger`, options),
      { method: 'POST' }
    ),
}
