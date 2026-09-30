import { fetchAPI } from './client'

export type NotificationSource = 'assistant' | 'agent' | 'market'
export type NotificationView = 'all' | 'unread' | 'pending' | 'archived' | 'attention'
export interface NotificationAction {
  kind: 'assistant_conversation' | 'assistant_export' | 'agent_run' | 'price_alert_hit'
  export_id?: number
  conversation_id?: number
  task_id?: number
  run_id?: number
  hit_id?: number
  rule_id?: number
}
export interface NotificationItem {
  id: number
  source: NotificationSource
  event_type: 'assistant_completed' | 'assistant_failed' | 'assistant_export_completed' | 'assistant_export_failed' | 'assistant_awaiting_approval' | 'agent_completed' | 'agent_failed' | 'price_alert_hit'
  severity: 'info' | 'warning' | 'critical'
  attention: 'informational' | 'action_required'
  title: string
  template_key: string
  template_params: Record<string, string | number | null>
  group_key: string
  toast_eligible: boolean
  occurred_at: string
  resolved_at: string | null
  expires_at: string | null
  read_at: string | null
  archived_at: string | null
  action_required: boolean
  available: boolean
  actions: NotificationAction[]
}
export interface NotificationSummary { unread_count: number; pending_action_count: number; observed_id: number }
export interface NotificationPage { items: NotificationItem[]; next_cursor: string | null; observed_id: number }
export interface NotificationFilter { source?: NotificationSource; view?: NotificationView }
export interface NotificationSelection extends NotificationFilter { ids?: number[]; through_id?: number }
export type NotificationTarget =
  | { kind: 'assistant_conversation'; conversation_id: number }
  | { kind: 'assistant_export'; export_id: number; conversation_id: number }
  | { kind: 'agent_run'; id: number; agent_name: string; status: string; result: string; error: string; occurred_at: string; notify_attempted: boolean; notify_sent: boolean }
  | { kind: 'price_alert_hit'; id: number; rule_id: number; name: string; symbol: string; occurred_at: string; notify_success: boolean; snapshot: { quote?: { current_price?: number; change_pct?: number }; conditions?: { type: string; op: string; target: unknown; actual: number | null; matched: boolean }[] } }
export const notificationsApi = {
  summary: (signal?: AbortSignal) => fetchAPI<NotificationSummary>('/notifications/summary', { signal }),
  list: (filter: NotificationFilter & { cursor?: string; limit?: number } = {}, signal?: AbortSignal) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(filter)) if (value != null) params.set(key, String(value))
    return fetchAPI<NotificationPage>(`/notifications?${params}`, { signal })
  },
  read: (command: NotificationSelection) => fetchAPI<{ updated: number }>('/notifications/read', { method: 'POST', body: JSON.stringify(command) }),
  archive: (command: NotificationSelection & { archived?: boolean }) => fetchAPI<{ updated: number }>('/notifications/archive', { method: 'POST', body: JSON.stringify(command) }),
  target: (id: number, signal?: AbortSignal) => fetchAPI<NotificationTarget>(`/notifications/${id}/target`, { signal }),
}
