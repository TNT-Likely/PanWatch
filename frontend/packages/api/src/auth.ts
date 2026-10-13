import { fetchAPI } from './client'

export interface AuthStatus {
  /** local: 应用自身登录；proxy: 认证已卸载到前置网关 */
  mode?: 'local' | 'proxy'
  initialized: boolean
  /** proxy 模式下的统一登出地址 */
  logout_url?: string
}

export interface AuthTokenPayload {
  token: string
  expires_at: string
}

export interface LoginPayload {
  username: string
  password: string
}

export const authApi = {
  status: () => fetchAPI<AuthStatus>('/auth/status'),
  login: (payload: LoginPayload) =>
    fetchAPI<AuthTokenPayload>('/auth/login', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  setup: (payload: LoginPayload) =>
    fetchAPI<AuthTokenPayload>('/auth/setup', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}
