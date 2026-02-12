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
}
