import { fetchAPI } from './client'

export interface SelfCheckItem {
  category: 'system' | 'datasource' | 'ai' | 'notify'
  key: string
  name: string
  status: 'ok' | 'slow' | 'fail'
  latency_ms: number
  error: string | null
  /** 中文修复提示(仅 fail 时非空)。 */
  hint: string
  hint_code?: string
  /** 例如通知"仅校验配置未真发"。 */
  note: string | null
  note_code?: string | null
  note_params?: Record<string, string | number>
  /** 二级分组(AI 类目=服务商名);其余类目为 null。 */
  group: string | null
}

export interface SelfCheckResult {
  items: SelfCheckItem[]
  summary: {
    total: number
    ok: number
    slow: number
    fail: number
  }
  notify_send: boolean
}

export const healthApi = {
  /** 系统自检(数据源/AI/通知连通性)。notifySend=true 会真实发送测试通知。 */
  selfcheck: (notifySend = false) =>
    fetchAPI<SelfCheckResult>('/health/selfcheck?notify_send=' + notifySend, { timeoutMs: 60000 }),

  /** 只取可自检项清单(不探测,秒回),用于先渲染再逐项检查。 */
  selfcheckList: () =>
    fetchAPI<{ items: Array<{ category: string; key: string; name: string; group: string | null }> }>(
      '/health/selfcheck?list=1',
    ),

  /** 只探测指定 key 的若干项(用于逐项/小并发自检)。notifySend 仅影响 notify 类目。 */
  selfcheckKeys: (keys: string[], notifySend = false) =>
    fetchAPI<SelfCheckResult>(
      '/health/selfcheck?keys=' +
        encodeURIComponent(keys.join(',')) +
        '&notify_send=' +
        notifySend,
      { timeoutMs: 30000 },
    ),
}
