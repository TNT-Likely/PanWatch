import { fetchAPI } from './client'

/** 个人访问令牌(PAT)—— MCP 端点专用凭据 */
export interface PatItem {
  id: number
  name: string
  prefix: string
  scopes: string[]
  expires_at: string | null
  last_used_at: string | null
  revoked_at: string | null
  created_at: string | null
  revoked: boolean
}

/** 创建响应:额外带一次性明文 token */
export interface PatCreated extends PatItem {
  token: string
}

export interface CreatePatBody {
  name?: string
  scopes?: string[]
  /** 过期天数;null = 永不过期 */
  expires_in_days?: number | null
}

export const patsApi = {
  list: () => fetchAPI<{ items: PatItem[] }>('/pats'),

  create: (body: CreatePatBody) =>
    fetchAPI<PatCreated>('/pats', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  revoke: (id: number) =>
    fetchAPI<{ ok: boolean; id: number }>(`/pats/${encodeURIComponent(String(id))}`, {
      method: 'DELETE',
    }),
}
