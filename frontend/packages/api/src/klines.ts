import { fetchAPI } from './client'

export interface KlineSummaryRequestItem {
  symbol: string
  market: string
}

export interface KlineSummaryResponse {
  symbol: string
  market: string
  summary: Record<string, unknown>
}

export const klinesApi = {
  summaryBatch: (
    items: KlineSummaryRequestItem[],
    signal?: AbortSignal,
  ) => fetchAPI<KlineSummaryResponse[]>('/klines/summary/batch', {
    method: 'POST',
    body: JSON.stringify({ items }),
    signal,
    timeoutMs: 60_000,
  }),
}
