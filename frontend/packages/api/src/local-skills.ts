import { fetchAPI } from './client'

export interface LocalSkillItem {
  id: number
  slug: string
  display_name: string
  description: string
  skill_path: string
  source_root: string
  enabled: boolean
  config: Record<string, unknown>
  last_seen_at: string
  hermes_available: boolean
}

export interface LocalSkillUpdatePayload {
  enabled?: boolean
  display_name?: string
  description?: string
  config?: Record<string, unknown>
}

export interface LocalSkillTriggerPayload {
  stock_id?: number
  symbol?: string
  market?: string
  name?: string
}

export interface LocalSkillTriggerResponse {
  queued?: boolean
  trace_id?: string
  success?: boolean
  message: string
  result?: Record<string, unknown>
}

export interface HermesStatusResponse {
  available: boolean
  bin: string
  profile: string
  config: Record<string, string | number | boolean>
}

export interface HermesTestResponse {
  ok: boolean
  message: string
  bin?: string
  profile?: string
  skill?: string
  reply_preview?: string
  session_id?: string
}

export const LOCAL_SKILL_AGENT_PREFIX = 'local_skill:'

export function isLocalSkillAgentName(name: string): boolean {
  return (name || '').startsWith(LOCAL_SKILL_AGENT_PREFIX)
}

export function localSkillAgentName(slug: string): string {
  return `${LOCAL_SKILL_AGENT_PREFIX}${slug}`
}

export function parseLocalSkillSlug(agentName: string): string | null {
  if (!isLocalSkillAgentName(agentName)) return null
  const slug = agentName.slice(LOCAL_SKILL_AGENT_PREFIX.length).trim()
  return slug || null
}

export const localSkillsApi = {
  list: (opts?: { enabledOnly?: boolean; refresh?: boolean }) => {
    const q = new URLSearchParams()
    if (opts?.enabledOnly) q.set('enabled_only', 'true')
    if (opts?.refresh) q.set('refresh', 'true')
    const qs = q.toString()
    return fetchAPI<LocalSkillItem[]>(`/local-skills${qs ? `?${qs}` : ''}`)
  },

  refresh: () => fetchAPI<LocalSkillItem[]>('/local-skills/refresh', { method: 'POST' }),

  update: (slug: string, payload: LocalSkillUpdatePayload) =>
    fetchAPI<LocalSkillItem>(`/local-skills/${encodeURIComponent(slug)}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  trigger: (
    slug: string,
    payload: LocalSkillTriggerPayload,
    opts?: { wait?: boolean; timeoutMs?: number },
  ) => {
    const q = new URLSearchParams()
    if (opts?.wait === false) q.set('wait', 'false')
    const qs = q.toString()
    return fetchAPI<LocalSkillTriggerResponse>(
      `/local-skills/${encodeURIComponent(slug)}/trigger${qs ? `?${qs}` : ''}`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
        timeoutMs: opts?.timeoutMs ?? 720_000,
      },
    )
  },

  hermesStatus: () => fetchAPI<HermesStatusResponse>('/local-skills/hermes/status'),

  testHermes: (skill?: string) => {
    const q = skill ? `?skill=${encodeURIComponent(skill)}` : ''
    return fetchAPI<HermesTestResponse>(`/local-skills/hermes/test${q}`, {
      method: 'POST',
      timeoutMs: 120_000,
    })
  },
}
