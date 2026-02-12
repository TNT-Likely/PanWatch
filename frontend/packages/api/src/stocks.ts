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

export const stocksApi = {
  list: () => fetchAPI<StockItem[]>('/stocks'),
  create: (payload: StockCreatePayload) =>
    fetchAPI<StockItem>('/stocks', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  remove: (id: number) => fetchAPI<{ ok: boolean }>(`/stocks/${id}`, { method: 'DELETE' }),
}
