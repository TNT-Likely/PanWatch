const API_BASE = '/api'
const DEFAULT_TIMEOUT_MS = 20000

interface ApiResponse<T> {
  code: number
  error_code?: string
  success?: boolean
  data: T
  message: string
}

const ENGLISH_HTTP_ERRORS: Record<string, string> = {
  http_400: 'The request is invalid. Check the submitted values and try again.',
  http_401: 'Your session has expired. Sign in again.',
  http_403: 'You do not have permission to perform this action.',
  http_404: 'The requested item could not be found.',
  http_409: 'The request conflicts with the current state. Refresh and try again.',
  http_422: 'Some submitted values could not be accepted.',
  http_429: 'Too many requests. Wait a moment and try again.',
  http_500: 'The server encountered an error. Try again later.',
  http_502: 'An upstream service failed. Try again later.',
  http_503: 'The service is temporarily unavailable. Try again later.',
  http_504: 'The service timed out. Try again later.',
}

function localizedApiError(body: ApiResponse<unknown>, status: number): string {
  const code = body.error_code || `http_${body.code || status}`
  if (localStorage.getItem('panwatch-locale')?.toLowerCase().startsWith('en')) {
    return ENGLISH_HTTP_ERRORS[code] || 'The request failed. Try again later.'
  }
  return body.message || `HTTP ${status}`
}

export function getToken(): string | null {
  return localStorage.getItem('token')
}

export function logout() {
  localStorage.removeItem('token')
  localStorage.removeItem('token_expires')
  window.location.href = '/login'
}

export function isAuthenticated(): boolean {
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
    logout()
    throw new Error('登录已过期')
  }

  const body: ApiResponse<T> = await res.json().catch(() => ({
    code: res.status,
    data: null as T,
    message: `HTTP ${res.status}`,
  }))
  if (body.code !== 0 || body.success === false) {
    throw new Error(localizedApiError(body, res.status))
  }
  return body.data
}

export const apiClient = {
  request: fetchAPI,
}
