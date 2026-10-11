import { fetchAPI } from './client'
import type { StockItem } from './stocks'

export type SetupStep = 'stock' | 'quote' | 'ai' | 'analysis' | 'alert' | 'notify'
export type TourTrack = 'main' | 'opportunities' | 'paper' | 'assistant' | 'automation' | 'datasources'
export interface TrackStatus {
  key: Exclude<TourTrack, 'main'>
  step: string | null
  started: boolean
  completed_steps: string[]
  completed: boolean
  total: number
}
export interface SetupStatus {
  goal: 'ai'
  active_track: TourTrack
  tracks: TrackStatus[]
  deferred: boolean
  started: boolean
  tour_step: SetupStep | 'done' | null
  selected_stock: StockItem | null
  models: Array<{ id: number; name: string; model: string; is_default: boolean; verified: boolean }>
  channels: Array<{ id: number; name: string; enabled: boolean; is_default: boolean; verified: boolean }>
  quote: { current_price: number; change_pct: number | null; source: string; observed_at: string; source_time: string } | null
  analysis: { id: number; status: 'running' | 'success' | 'failed'; content: string; error_code: string; error_message: string; model_label: string; created_at: string; trace_id: string } | null
  steps: Array<{ key: SetupStep; required: boolean; status: 'complete' | 'pending' | 'skipped' }>
  completed: boolean
  completed_count: number
  required_count: number
}
export interface SetupUpdate {
  track?: TourTrack
  track_step?: string
  complete_step?: string
  restart?: boolean
  tour_step?: SetupStep | 'done'
  deferred?: boolean
  stock_id?: number
  skip?: 'alert' | 'notify'
  unskip?: 'alert' | 'notify'
}
export const onboardingApi = {
  status: () => fetchAPI<SetupStatus>('/onboarding'),
  update: (body: SetupUpdate) => fetchAPI<SetupStatus>('/onboarding', { method: 'PATCH', body: JSON.stringify(body) }),
  quote: () => fetchAPI<SetupStatus>('/onboarding/quote', { method: 'POST', timeoutMs: 40000 }),
  analyze: () => fetchAPI<SetupStatus>('/onboarding/analysis', { method: 'POST' }),
}
