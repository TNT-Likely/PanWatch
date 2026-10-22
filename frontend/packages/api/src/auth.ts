import { fetchAPI, loadAuthStatus } from './client'
import { getAuthStatus, setAuthStatus } from './auth-session'
export type { AuthStatus } from './auth-session'

export function rememberPasswordLogin(data: AuthTokenPayload): void {
  localStorage.setItem('token', data.token)
  localStorage.setItem('token_expires', data.expires_at)
  const status = getAuthStatus()
  if (status?.password_enabled) setAuthStatus({ ...status, authenticated: true, auth_method: 'password', csrf_token: null })
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
  status: loadAuthStatus,
  startOidcLogin: (url: string) => window.location.assign(url),
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
