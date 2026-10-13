const API_BASE = '/api'
const DEFAULT_TIMEOUT_MS = 20000

interface ApiResponse<T> {
  code: number
  success?: boolean
  data: T
  message: string
}

// ============================================================
// 认证模式（由 /auth/status 拉取后设置，见 setAuthMode）
// local: 应用自身 JWT 登录；proxy: 认证卸载到前置网关，
//        请求凭据依赖同域 cookie，由网关注入身份头
// ============================================================
export type AuthMode = 'local' | 'proxy'

let authMode: AuthMode = 'local'
let proxyLogoutUrl = ''
const authModeListeners = new Set<() => void>()

export function setAuthMode(mode: AuthMode, logoutUrl?: string) {
  authMode = mode
  proxyLogoutUrl = logoutUrl || ''
  authModeListeners.forEach((listener) => listener())
}

export function getAuthMode(): AuthMode {
  return authMode
}

/** 订阅认证模式变化（供 React useSyncExternalStore 等使用） */
export function subscribeAuthMode(listener: () => void): () => void {
  authModeListeners.add(listener)
  return () => authModeListeners.delete(listener)
}

export function getToken(): string | null {
  return localStorage.getItem('token')
}

export function logout() {
  if (authMode === 'proxy') {
    // 网关模式：清的是网关 cookie，跳统一登出页（无地址则刷新触发 302）
    if (proxyLogoutUrl) {
      window.location.href = proxyLogoutUrl
    } else {
      window.location.reload()
    }
    return
  }
  localStorage.removeItem('token')
  localStorage.removeItem('token_expires')
  window.location.href = '/login'
}

export function isAuthenticated(): boolean {
  if (authMode === 'proxy') return true
  const token = getToken()
  if (!token) return false

  const expires = localStorage.getItem('token_expires')
  if (expires && new Date(expires) < new Date()) {
    logout()
    return false
  }
  return true
}

export interface ApiRequestOptions extends RequestInit {
  timeoutMs?: number
}

export async function fetchAPI<T>(path: string, options?: ApiRequestOptions): Promise<T> {
  const headers: Record<string, string> = {}

  const token = getToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  if (options?.body) {
    headers['Content-Type'] = 'application/json'
  }

  const timeoutController = options?.signal ? null : new AbortController()
  const timeoutMs = typeof options?.timeoutMs === 'number' && options.timeoutMs > 0
    ? options.timeoutMs
    : DEFAULT_TIMEOUT_MS
  const timeoutId = timeoutController
    ? window.setTimeout(() => timeoutController.abort(), timeoutMs)
    : null

  let res: Response
  try {
    const { timeoutMs: _timeoutMs, ...requestOptions } = options || {}
    res = await fetch(`${API_BASE}${path}`, {
      ...requestOptions,
      headers: {
        ...headers,
        ...(requestOptions.headers as Record<string, string> | undefined),
      },
      signal: requestOptions.signal || timeoutController?.signal,
    })
  } catch (error: any) {
    if (error?.name === 'AbortError') {
      throw new Error('请求超时，请稍后重试')
    }
    throw error
  } finally {
    if (timeoutId !== null) {
      window.clearTimeout(timeoutId)
    }
  }

  if (res.status === 401) {
    if (authMode === 'proxy') {
      // 网关 cookie 失效：刷新页面，由网关 302 引导到统一登录页
      window.location.reload()
      throw new Error('登录已过期')
    }
    logout()
    throw new Error('登录已过期')
  }

  const body: ApiResponse<T> = await res.json().catch(() => ({
    code: res.status,
    data: null as T,
    message: `HTTP ${res.status}`,
  }))
  if (body.code !== 0 || body.success === false) {
    throw new Error(body.message || `HTTP ${res.status}`)
  }
  return body.data
}

export const apiClient = {
  request: fetchAPI,
}
