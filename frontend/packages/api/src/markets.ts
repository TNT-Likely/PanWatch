import { fetchAPI } from './client'

export interface EffectiveMarket {
  code: string
  name: string
  optional: boolean
  capabilities: string[]
  currency: string
}

export interface EffectiveMarketsResponse {
  markets: EffectiveMarket[]
}

export const marketsApi = {
  getEffectiveMarkets: () => fetchAPI<EffectiveMarketsResponse>('/stocks/markets/enabled'),
}
