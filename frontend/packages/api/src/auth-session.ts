/** Server-verified browser authentication. Cookie session data is never persisted. */
export interface AuthStatus {
  initialized: boolean
  oidc_enabled: boolean
  password_enabled: boolean
  authenticated: boolean
  auth_method: 'oidc' | 'password' | null
  csrf_token: string | null
  oidc_login_url: string | null
}

let status: AuthStatus | null = null
let revision = 0
const listeners = new Set<() => void>()

export function getAuthStatus(): AuthStatus | null {
  return status
}

export function getAuthRevision(): number {
  return revision
}

/** Invalidate older reads without changing the verified UI/session state. */
export function invalidateAuthStatusRequests(): void {
  revision += 1
}

export function subscribeAuthStatus(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

function notify() {
  revision += 1
  listeners.forEach(listener => listener())
}

function clearPasswordToken() {
  localStorage.removeItem('token')
  localStorage.removeItem('token_expires')
}

export function setAuthStatus(next: AuthStatus): void {
  status = next
  // A stale password token must never override a cookie session or SSO-only policy.
  if (!next.password_enabled || next.auth_method === 'oidc') clearPasswordToken()
  notify()
}

export function clearAuthSession(): void {
  clearPasswordToken()
  status = status ? { ...status, authenticated: false, auth_method: null, csrf_token: null } : null
  notify()
}

/** Reject incomplete status responses instead of accidentally exposing password setup. */
export function validateAuthStatus(value: AuthStatus): AuthStatus {
  if (!value || typeof value.initialized !== 'boolean' || typeof value.oidc_enabled !== 'boolean'
    || typeof value.password_enabled !== 'boolean' || typeof value.authenticated !== 'boolean'
    || ![null, 'oidc', 'password'].includes(value.auth_method)
    || (value.csrf_token !== null && typeof value.csrf_token !== 'string')
    || (value.oidc_login_url !== null && typeof value.oidc_login_url !== 'string')
    || (value.authenticated && value.auth_method === null)
    || (value.auth_method === 'oidc' && (!value.oidc_enabled || !value.csrf_token))
    || (value.auth_method === 'password' && !value.password_enabled)) {
    throw new Error('Invalid authentication status')
  }
  if (value.oidc_enabled) {
    const url = new URL(value.oidc_login_url || '')
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password
      || url.search || url.hash || !url.pathname.endsWith('/api/auth/oidc/login')) {
      throw new Error('Invalid authentication URL')
    }
  }
  return value
}
