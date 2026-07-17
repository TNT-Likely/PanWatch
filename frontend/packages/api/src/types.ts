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

export interface SourceHealth {
  count: number
  success_rate: number | null
  p50_latency_ms: number | null
  last_error?: string
  last_success_at?: number
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
  engine_attached?: boolean
  health?: SourceHealth | null
  /** 孤儿源:该 (type, provider) 在包内无对应 vendor 且不在种子里,抓取/测试必失败。 */
  is_orphan?: boolean
}
