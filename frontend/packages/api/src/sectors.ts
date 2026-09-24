import { fetchAPI } from './client'

/** 行业方向预测(盘前决策引擎产出,GET /sectors/predictions 返回行)。 */
export interface SectorPredictionItem {
  id: number
  snapshot_date: string
  board_code: string
  board_name: string
  market: string
  /** bullish / bearish / neutral */
  direction: string
  confidence?: number | null
  /** 所处阶段,如 启动/延续/高潮/退潮 */
  stage: string
  momentum_score?: number | null
  rationale: string
  catalysts?: unknown[]
  meta?: Record<string, unknown>
  source_agent?: string
  created_at?: string
  updated_at?: string
}

export interface SectorPredictionsResponse {
  /** 显式请求或解析出的库内最新快照日;无数据时为空串 */
  requested_date: string
  predictions: SectorPredictionItem[]
}

export const sectorsApi = {
  /** 按日期列出行业预测;date 缺省由后端取库内最新快照日 */
  getSectorPredictions: (date?: string) =>
    fetchAPI<SectorPredictionsResponse>(
      `/sectors/predictions${date ? `?date=${encodeURIComponent(date)}` : ''}`
    ),
}
