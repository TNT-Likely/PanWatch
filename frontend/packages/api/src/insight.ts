import { fetchAPI, getToken } from './client'

type QueryValue = string | number | boolean | null | undefined

function withQuery(path: string, params: Record<string, QueryValue>): string {
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

export const insightApi = {
  quote: <T>(symbol: string, market: string) =>
    fetchAPI<T>(`/quotes/${encodeURIComponent(symbol)}?market=${encodeURIComponent(market)}`),

  klineSummary: <T>(symbol: string, market: string) =>
    fetchAPI<T>(`/klines/${encodeURIComponent(symbol)}/summary?market=${encodeURIComponent(market)}`),

  klines: <T>(symbol: string, params: { market: string; days?: number; interval?: string }) =>
    fetchAPI<T>(
      withQuery(`/klines/${encodeURIComponent(symbol)}`, {
        market: params.market,
        days: params.days,
        interval: params.interval,
      })
    ),

  intradayTrends: <T>(symbol: string, market: string) =>
    fetchAPI<T>(
      withQuery(`/klines/${encodeURIComponent(symbol)}/trends`, { market }),
      { timeoutMs: 20000 },
    ),

  suggestions: <T>(
    symbol: string,
    params: { market?: string; limit?: number; include_expired?: boolean }
  ) =>
    fetchAPI<T>(
      withQuery(`/suggestions/${encodeURIComponent(symbol)}`, {
        market: params.market,
        limit: params.limit,
        include_expired: params.include_expired,
      })
    ),

  news: <T>(params: Record<string, QueryValue>) => fetchAPI<T>(withQuery('/news', params)),

  history: <T>(params: Record<string, QueryValue>) => fetchAPI<T>(withQuery('/history', params)),

  portfolioSummary: <T>(params?: { include_quotes?: boolean }) =>
    fetchAPI<T>(
      withQuery('/portfolio/summary', {
        include_quotes: params?.include_quotes,
      })
    ),

  addPositionEval: (params: AddPositionEvalParams) =>
    fetchAPI<AddPositionEvalResult>('/insights/add-position-eval', {
      method: 'POST',
      body: JSON.stringify(params),
      timeoutMs: 60000, // AI 评估较慢,放宽超时
    }),

  chanEmotionStrategy: (symbol: string, params: { market?: string; holding?: boolean }) =>
    fetchAPI<ChanEmotionStrategyResult>(
      withQuery(`/insights/chan-emotion/${encodeURIComponent(symbol)}`, {
        market: params.market,
        holding: params.holding,
      }),
      { timeoutMs: 45000 },
    ),

  analysisBriefBatch: (items: Array<{ symbol: string; market: string }>) =>
    fetchAPI<AnalysisBriefItem[]>(
      '/insights/analysis-brief/batch',
      {
        method: 'POST',
        body: JSON.stringify({ items }),
        timeoutMs: 15_000,
      },
    ),

  announcementEval: (params: { symbol: string; market: string; model_id?: number }) =>
    fetchAPI<AnnouncementEvalResult>('/insights/announcement-eval', {
      method: 'POST',
      body: JSON.stringify(params),
      timeoutMs: 40000,
    }),

  /** 把任意分析历史记录导出为 PDF 并触发浏览器下载。 */
  async downloadHistoryPdf(historyId: number): Promise<void> {
    const token = getToken()
    const resp = await fetch(`/api/history/${historyId}/pdf`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!resp.ok) {
      let msg = `导出失败 (${resp.status})`
      try {
        const j = await resp.json()
        msg = (j && (j.message || j.detail)) || msg
      } catch {
        /* 非 JSON 错误体 */
      }
      throw new Error(msg)
    }
    const blob = await resp.blob()
    let filename = `分析报告-${historyId}.pdf`
    const cd = resp.headers.get('Content-Disposition') || ''
    const m = cd.match(/filename\*=UTF-8''([^;]+)/i)
    if (m) {
      try {
        filename = decodeURIComponent(m[1])
      } catch {
        /* 保留默认文件名 */
      }
    }
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  },
}

export interface AnnouncementToneItem {
  title: string
  time: string
  tone: string // 利好 / 利空 / 中性
  summary: string
}

export interface AnnouncementEvalResult {
  symbol: string
  market: string
  items: AnnouncementToneItem[]
}

export interface AddPositionEvalParams {
  symbol: string
  market: string
  current_quantity: number
  current_cost: number
  add_quantity: number
  add_price: number
  model_id?: number
}

export interface AddPositionEvalResult {
  symbol: string
  market: string
  action: string // 加仓 / 建仓
  new_cost: number
  dilute_abs: number
  dilute_pct: number
  total_quantity: number
  total_invested: number
  verdict: string // 适合 / 谨慎 / 不适合 / 未知
  content: string // markdown 结论
}

export interface ChanEmotionLevelAnalysis {
  timeframe: string
  label: string
  bar_count: number
  trend: string
  stroke_count: number
  pivot: { zd: number; zg: number } | null
  divergence: string | null
  signal_tags: string[]
}

export interface ChanEmotionBrief {
  action: string
  action_label: string
  win_rate: number
  emotion_phase: string
  emotion_label: string
  reason?: string
}

export interface AnalysisBriefItem {
  symbol: string
  market: string
  lmd_brief: string | null
  deep_brief: string | null
}

export interface ChanEmotionStrategyResult {
  symbol: string
  market: string
  asof: string
  last_close: number | null
  emotion_phase: string
  emotion_label: string
  levels: ChanEmotionLevelAnalysis[]
  win_rate: number
  position_pct: number
  position_label: string
  action: string
  action_label: string
  signal: string
  reason: string
  stop_loss: number | null
  target_price: number | null
  invalidation: string
  decision_explanation: string
  agent_instruction: string
  human_notes: string[]
  evidence: Array<{ text: string; delta: number }>
  strategy_code: string
  strategy_name: string
}
