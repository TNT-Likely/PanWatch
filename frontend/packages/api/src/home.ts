import { fetchAPI } from './client'

export interface AlertHitToday {
  rule_id: number
  rule_name: string
  symbol: string
  name: string
  market: string
  trigger_time: string
  snapshot: Record<string, unknown>
}

export interface PortfolioTodo {
  type: string // no_alert | alert_expiring
  symbol?: string
  market?: string
  name?: string
  message: string
}

export const homeApi = {
  /** 今日(本地时区)全部提醒命中,跨规则聚合。 */
  alertHitsToday: () => {
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
    return fetchAPI<AlertHitToday[]>(`/price-alerts/hits/today?timezone=${encodeURIComponent(timezone)}`)
  },

  /** 首页空态待办:持仓未设提醒 / 提醒即将到期。 */
  todos: () => fetchAPI<{ todos: PortfolioTodo[]; count: number }>('/portfolio/todos'),
}
