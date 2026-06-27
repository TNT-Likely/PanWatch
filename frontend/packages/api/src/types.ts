export interface AIModel {
  id: number
  name: string
  service_id: number
  model: string
  is_default: boolean
}

export interface AIService {
  id: number
  name: string
  base_url: string
  api_key: string
  models: AIModel[]
}

export interface NotifyChannel {
  id: number
  name: string
  type: string
  config: Record<string, string>
  enabled: boolean
  is_default: boolean
}

export interface DataSourceCookieHealth {
  status: 'not_configured' | 'unknown' | 'ok' | 'expired' | 'blocked' | 'error'
  label: string
  message: string
  checked_at?: string | null
  sample_count?: number
  update_hint?: string
}

export interface DataSource {
  id: number
  name: string
  type: string
  provider: string
  config: Record<string, unknown>
  enabled: boolean
  priority: number
  supports_batch: boolean
  test_symbols: string[]
  cookie_health?: DataSourceCookieHealth | null
}
