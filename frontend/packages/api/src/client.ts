import { interfaceText, isEnglishInterface } from './locale'
import { clearAuthSession, getAuthRevision, getAuthStatus, invalidateAuthStatusRequests, setAuthStatus, validateAuthStatus, type AuthStatus } from './auth-session'

import { publicUrl } from './base-path'

const API_BASE = publicUrl('/api')
const DEFAULT_TIMEOUT_MS = 20000

export interface ApiResponse<T> {
  code: number
  error_code?: string
  success?: boolean
  data: T
  message: string
}

const API_ERROR_TEXT_EN: Record<string, string> = {
  assistant_export_empty: 'There is no saved conversation content to summarize.',
  assistant_export_budget: 'The context budget is too small. Increase it in assistant settings and retry.',
  assistant_export_too_large: 'This conversation exceeds the current summary export limit.',
  assistant_export_invalid: 'Could not generate a valid context summary. Try again.',
  assistant_export_timeout: 'Context summary generation timed out. Try again.',
  assistant_export_cancelled: 'Context export was cancelled.',
  notification_cursor_invalid: 'The notification page cursor is invalid. Refresh the list.',
  notification_pending_action: 'Resolve pending approvals before archiving.',
  notification_source_unavailable: 'The notification source was deleted or is unavailable.',
  account_not_found: 'The account could not be found.',
  ai_authentication_failed: 'AI service authentication failed. Check that the API key is correct and still valid.',
  ai_connection_failed: 'Could not connect to the AI service. Check the service URL, proxy, and network.',
  ai_content_rejected: 'The AI service rejected this content. Revise the request and try again.',
  ai_context_limit_exceeded: 'The request exceeds the model context limit. Start a new conversation, compress the context, or switch models.',
  ai_permission_denied: 'The API key cannot use this AI model. Check the provider permissions or switch models.',
  ai_quota_exhausted: 'The AI service quota is exhausted. Add credits or switch to an available model.',
  ai_rate_limited: 'The AI service is receiving too many requests. Wait and retry or switch models.',
  ai_request_invalid: 'The AI service could not accept this request. Check model compatibility and configuration.',
  ai_request_timeout: 'The AI service timed out. Try again later or switch models.',
  ai_service_failed: 'The AI service call failed. Check the model configuration or try again later.',
  ai_service_unavailable: 'The AI service is temporarily unavailable. Try again later or switch models.',
  ai_announcement_interpretation_failed: 'The AI announcement interpretation failed. Try again later.',
  ai_evaluation_failed: 'The AI evaluation failed. Try again later.',
  ai_model_discovery_failed: 'Model discovery failed. Try again later or add a model manually.',
  ai_model_not_found: 'The AI model could not be found.',
  ai_model_service_not_found: 'The AI service for this model could not be found.',
  ai_model_test_failed: 'The AI model test failed. Check the service configuration and try again.',
  ai_service_not_found: 'The AI service could not be found.',
  agent_binding_unsupported: 'This Agent is an internal capability and cannot be bound to a stock.',
  agent_not_enabled: 'This Agent is disabled.',
  agent_not_found: 'The selected Agent could not be found.',
  agent_schedule_invalid: 'The schedule expression is invalid.',
  agent_trigger_failed: 'The Agent could not be run. Try again later.',
  agent_trigger_invalid: 'The Agent request is invalid.',
  analysis_not_found: 'The requested analysis could not be found.',
  analysis_record_not_found: 'The requested history record could not be found.',
  assistant_approval_conflict: 'This approval was already handled or has expired.',
  assistant_config_invalid: 'The Assistant configuration is invalid.',
  assistant_permission_invalid: 'The Assistant tool-permission settings are invalid.',
  assistant_resource_not_found: 'The requested Assistant resource could not be found.',
  auth_already_configured: 'An account is already configured. Use the sign-in endpoint instead.',
  auth_not_configured: 'Set up an account before signing in.',
  auth_required: 'Sign in to continue.',
  auth_expired: 'Your session has expired. Sign in again.',
  password_login_disabled: 'Password sign-in is disabled. Continue with SSO.',
  auth_setup_disabled: 'Password setup is disabled for this installation.',
  selfcheck_requires_post: 'Use POST to run system checks.',
  entry_candidates_refresh_requires_post: 'Use POST to refresh entry candidates.',
  csrf_invalid: 'Your session changed or the request could not be verified. Refresh the page and try again.',
  oidc_not_configured: 'SSO is not configured correctly. Contact the installation owner.',
  avatar_data_invalid: 'The avatar data is invalid.',
  avatar_data_url_invalid: 'The avatar must be a data URL.',
  board_code_required: 'Enter a sector code.',
  board_stocks_timeout: 'The sector-constituent data source timed out. Check the proxy and try again.',
  board_stocks_unavailable: 'Sector-constituent data is temporarily unavailable.',
  channel_config_invalid: 'The notification channel configuration is invalid.',
  channel_not_found: 'The notification channel could not be found.',
  channel_test_failed: 'The test notification could not be sent.',
  database_busy: 'The database is busy. Try again shortly.',
  datasource_not_found: 'The data source could not be found.',
  discovery_mode_invalid: 'The selected ranking mode is not supported.',
  factor_weight_invalid: 'The factor-weight settings are invalid.',
  hot_boards_unavailable: 'Hot-sector data is temporarily unavailable.',
  hot_stocks_unavailable: 'Hot-stock data is temporarily unavailable.',
  invalid_credentials: 'The username or password is incorrect.',
  market_unsupported: 'This market is not supported.',
  mcp_bearer_required: 'A Bearer personal access token is required.',
  mcp_pat_required: 'This MCP endpoint requires a pwmcp_ personal access token.',
  mcp_scope_required: 'This personal access token does not have the required scope.',
  mcp_token_expired: 'This personal access token has expired.',
  mcp_token_invalid: 'This personal access token is invalid.',
  mcp_token_revoked: 'This personal access token has been revoked.',
  paper_trading_account_not_found: 'The paper-trading account could not be found.',
  paper_trading_allocation_invalid: 'Market allocations cannot exceed 100%.',
  paper_trading_close_failed: 'The paper-trading position could not be closed.',
  paper_trading_t1_locked: 'A shares use T+1: positions bought today can be sold from the next trading day.',
  paper_trading_open_time_missing: 'The purchase time is missing; the sellable quantity cannot be verified.',
  paper_trading_notify_failed: 'The paper-trading test notification could not be sent.',
  paper_trading_reset_failed: 'The paper-trading account could not be reset.',
  password_too_short: 'The password must contain at least 6 characters.',
  pat_not_found: 'The personal access token could not be found.',
  pat_scope_invalid: 'One or more personal-access-token scopes are not supported.',
  position_calculation_invalid: 'The added quantity and price must both be greater than zero.',
  portfolio_ai_review_failed: 'The AI portfolio review failed. Try again later.',
  position_already_exists: 'This account already has a position in that stock.',
  position_not_found: 'The position could not be found.',
  price_alert_invalid: 'The price-alert settings are invalid.',
  price_alert_not_found: 'The price alert could not be found.',
  price_alert_stock_not_found: 'The selected stock could not be found.',
  quote_not_found: 'No quote is available for this stock.',
  stock_agent_not_bound: 'This stock is not bound to the selected Agent.',
  stock_agent_unbound_not_allowed: 'Allow an unbound Agent run before running it for a stock that is not in the watchlist.',
  stock_already_exists: 'This stock is already in the watchlist.',
  stock_has_position: 'Remove the positions before removing this stock.',
  stock_not_found: 'The stock could not be found.',
  stock_symbol_required: 'Enter a stock symbol before running an unbound Agent.',
  template_mode_invalid: 'Choose either merge or replace as the import mode.',
  template_module_invalid: 'The configuration package contains an unsupported module.',
  template_module_required: 'Choose at least one configuration module.',
  template_reference_invalid: 'The configuration package contains invalid references and was not imported.',
  template_version_unsupported: 'This configuration package version is not supported.',
  trace_id_invalid: 'The analysis trace ID is invalid.',
  tradingagents_not_registered: 'The TradingAgents workflow is not registered.',
  ui_language_invalid: 'The interface language must be zh-CN or en-US.',
  username_too_short: 'The username must contain at least 2 characters.',
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
  unknown: 'The request failed. Try again later.',
}

export function localizedApiError(body: ApiResponse<unknown>, status: number): string {
  const code = body.error_code || `http_${body.code || status}`
  if (isEnglishInterface()) {
    return API_ERROR_TEXT_EN[code] || API_ERROR_TEXT_EN.unknown
  }
  return body.message || `HTTP ${status}`
}

export function getToken(): string | null {
  const status = getAuthStatus()
  if (status && (!status.password_enabled || status.auth_method === 'oidc')) return null
  const expires = localStorage.getItem('token_expires')
  if (expires && new Date(expires).getTime() <= Date.now()) {
    localStorage.removeItem('token')
    localStorage.removeItem('token_expires')
    return null
  }
  return localStorage.getItem('token')
}

export function authHeaders(method = 'GET'): Record<string, string> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const status = getAuthStatus()
  if (status?.authenticated && status.auth_method === 'oidc'
    && !['GET', 'HEAD', 'OPTIONS'].includes(method.toUpperCase()) && status.csrf_token) {
    headers['X-CSRF-Token'] = status.csrf_token
  }
  return headers
}

function redirectToLogin() {
  // Auth failures on the login/status endpoints must never reload the login page.
  if (window.location.pathname !== publicUrl('/login')) window.location.href = publicUrl('/login')
}

export class AuthExpiredError extends Error {}

/** An obsolete or unverifiable request must fail without ending the current session. */
export class AuthChangedError extends Error {
  constructor() {
    super(interfaceText('登录状态已变化或无法确认，请重试', 'Your session changed or could not be verified. Try again.'))
  }
}

export interface AuthRequestSnapshot {
  revision: number
  credential: PasswordCredential | null
  status: AuthStatus | null
  usesSessionAuthorization: boolean
  signal?: AbortSignal
}

/** Call after header construction, which may discard an already expired token. */
export function captureAuthRequest(headers: HeadersInit, signal?: AbortSignal): AuthRequestSnapshot {
  const credential = passwordCredential()
  const status = getAuthStatus()
  const token = status && (!status.password_enabled || status.auth_method === 'oidc') ? null : credential?.token
  return {
    revision: getAuthRevision(), credential, status, signal,
    usesSessionAuthorization: new Headers(headers).get('Authorization') === (token ? `Bearer ${token}` : null),
  }
}

function isCurrentAuthRequest(sent: AuthRequestSnapshot): boolean {
  const current = passwordCredential()
  // Another request may have removed this same expired token while we waited.
  // An empty current pair is safe; a replacement token/expiry is never erased.
  const expiredAndRemoved = current?.token === null && current.expires === null
    && !!sent.credential?.expires && new Date(sent.credential.expires).getTime() <= Date.now()
  return sent.revision === getAuthRevision() && sent.usesSessionAuthorization
    && (samePasswordCredential(sent.credential, current) || expiredAndRemoved)
}

function checkAuthRequestAbort(sent: AuthRequestSnapshot): void {
  if (sent.signal?.aborted) throw new DOMException('The request was aborted.', 'AbortError')
}

export async function handleUnauthorized(path: string, status: number, sent: AuthRequestSnapshot): Promise<void> {
  if (status !== 401 || ['/auth/status', '/auth/login', '/auth/setup', '/auth/logout'].includes(path.split('?')[0])) return
  checkAuthRequestAbort(sent)
  if (!isCurrentAuthRequest(sent)) throw new AuthChangedError()

  if (!sent.status || sent.status.oidc_enabled) {
    // HttpOnly cookies can rotate in another tab without any local notification.
    // Verify once with today's cookie, never replaying the rejected operation.
    const verification = readAuthStatus(true, sent.signal)
    const revision = getAuthRevision()
    const credential = passwordCredential()
    let current: AuthStatus
    try {
      current = await verification
    } catch {
      checkAuthRequestAbort(sent)
      throw new AuthChangedError()
    }
    checkAuthRequestAbort(sent)
    const expectedCredential = !current.password_enabled || current.auth_method === 'oidc'
      ? { token: null, expires: null } : credential
    // Publishing status notifies subscribers synchronously; one may establish a
    // newer login. Only the single verified publication may advance this read.
    if (getAuthRevision() !== revision + 1 || getAuthStatus() !== current
      || !samePasswordCredential(expectedCredential, passwordCredential()) || current.authenticated) {
      throw new AuthChangedError()
    }
  }
  clearAuthSession()
  redirectToLogin()
  throw new AuthExpiredError(interfaceText('登录已过期', 'Your session has expired. Sign in again.'))
}

interface PasswordCredential {
  token: string | null
  expires: string | null
}

function passwordCredential(): PasswordCredential | null {
  // Read the raw pair without expiring it: another tab can replace these keys
  // without changing this tab's in-memory AuthStatus. Same-second JWTs may be
  // identical, so the more precise login expiry is part of the identity too.
  try {
    return { token: localStorage.getItem('token'), expires: localStorage.getItem('token_expires') }
  } catch {
    // Inaccessible storage is an unknown identity, never an empty credential.
    return null
  }
}

function samePasswordCredential(left: PasswordCredential | null, right: PasswordCredential | null): boolean {
  return left !== null && right !== null && left.token === right.token && left.expires === right.expires
}

let statusRequest: Promise<AuthStatus> | null = null

function readAuthStatus(fresh = false, signal?: AbortSignal): Promise<AuthStatus> {
  if (fresh) {
    // A status request started before a rejected logout may still contain the
    // old cookie's CSRF token. Do not reuse it or let it replace this fresh read.
    invalidateAuthStatusRequests()
    statusRequest = null
  }
  if (statusRequest && !signal) return statusRequest
  const revision = getAuthRevision()
  const previous = getAuthStatus()
  const passwordSuppressed = !!previous && (!previous.password_enabled || previous.auth_method === 'oidc')
  const response = fetchAPI<AuthStatus>('/auth/status', { cache: 'no-store' })
  // Header construction can discard an already expired token. Snapshot after it.
  const sentCredential = passwordCredential()
  const changedStatus = (): AuthStatus | null => {
    if (signal?.aborted) throw new DOMException('The request was aborted.', 'AbortError')
    if (revision !== getAuthRevision() || !samePasswordCredential(sentCredential, passwordCredential())) {
      const current = getAuthStatus()
      if (fresh || !current) throw new Error('Authentication changed')
      return current
    }
    return null
  }
  const request = response.then(validateAuthStatus).then(async status => {
    const changed = changedStatus()
    if (changed) return changed
    // Another tab can finish password login while this tab still remembers OIDC.
    // Preserve cookie priority: only test the suppressed bearer after the server
    // confirms no cookie session and allows password fallback. Never replay the
    // protected operation or publish an unverified password session locally.
    if (passwordSuppressed && !status.authenticated && status.password_enabled && sentCredential?.token
      && !(sentCredential.expires && new Date(sentCredential.expires).getTime() <= Date.now())) {
      status = validateAuthStatus(await fetchAPI<AuthStatus>('/auth/status', {
        cache: 'no-store', headers: { Authorization: `Bearer ${sentCredential.token}` },
      }))
      const changed = changedStatus()
      if (changed) return changed
    }
    setAuthStatus(status)
    return status
  }).finally(() => {
    // An older request must not clear the newer in-flight request's dedup guard.
    if (statusRequest === request) statusRequest = null
  })
  // Cancellation belongs to the protected operation, not unrelated status
  // consumers. Keep abort-bound verification out of the shared dedup promise.
  if (!signal) statusRequest = request
  return request
}

export function loadAuthStatus(): Promise<AuthStatus> {
  return readAuthStatus()
}

function sameAuthSession(left: AuthStatus | null, right: AuthStatus | null): boolean {
  return left?.authenticated === right?.authenticated
    && left?.auth_method === right?.auth_method && left?.csrf_token === right?.csrf_token
}

let logoutRequest: Promise<void> | null = null

export function logout(): Promise<void> {
  if (logoutRequest) return logoutRequest
  logoutRequest = (async () => {
    // Only a known CSRF rejection is safe to retry. Network errors, timeouts,
    // generic 403s and arbitrary mutations never trigger an automatic replay.
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const sentStatus = getAuthStatus()
      const headers = authHeaders('POST')
      const sentCredential = passwordCredential()
      const controller = new AbortController()
      const timeout = window.setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS)
      let res: Response
      try {
        res = await fetch(`${API_BASE}/auth/logout`, {
          method: 'POST',
          credentials: 'same-origin',
          headers,
          signal: controller.signal,
        })
      } finally {
        window.clearTimeout(timeout)
      }
      if (res.status !== 401) {
        const body: ApiResponse<unknown> = await res.json().catch(() => ({ code: res.status, data: null, message: `HTTP ${res.status}` }))
        if (!res.ok || body.code !== 0 || body.success === false) {
          if (attempt === 0 && res.status === 403 && body.error_code === 'csrf_invalid') {
            if (!sameAuthSession(sentStatus, getAuthStatus())
              || !samePasswordCredential(sentCredential, passwordCredential())) throw new Error('Authentication changed')
            const refresh = readAuthStatus(true)
            const refreshCredential = passwordCredential()
            const current = await refresh
            // setAuthStatus intentionally removes password credentials for OIDC
            // and SSO-only policy. All other changes still belong to a newer login.
            const currentCredential = !current.password_enabled || current.auth_method === 'oidc'
              ? { token: null, expires: null } : refreshCredential
            if (!sameAuthSession(current, getAuthStatus())
              || !samePasswordCredential(currentCredential, passwordCredential())) throw new Error('Authentication changed')
            if (!current.authenticated) {
              // The fresh read confirms the cookie is already expired/revoked.
              clearAuthSession()
              redirectToLogin()
              return
            }
            if (current.auth_method === 'oidc' && current.csrf_token !== sentStatus?.csrf_token) continue
          }
          throw new Error(localizedApiError(body, res.status))
        }
      }
      // Never erase a newer session established while this logout was in flight.
      if (!sameAuthSession(sentStatus, getAuthStatus())
        || !samePasswordCredential(sentCredential, passwordCredential())) throw new Error('Authentication changed')
      // Success or 401 confirms there is no old server session left to revoke.
      clearAuthSession()
      redirectToLogin()
      return
    }
  })().finally(() => { logoutRequest = null })
  return logoutRequest
}

export function isAuthenticated(): boolean {
  const status = getAuthStatus()
  if (!status?.authenticated) return false
  return status.auth_method === 'oidc' || (status.password_enabled && !!getToken())
}

export interface ApiRequestOptions extends RequestInit {
  timeoutMs?: number
}

export async function fetchAPI<T>(path: string, options?: ApiRequestOptions): Promise<T> {
  const headers = authHeaders(options?.method)

  if (options?.body) {
    headers['Content-Type'] = 'application/json'
  }
  new Headers(options?.headers).forEach((value, key) => {
    const existing = Object.keys(headers).find(name => name.toLowerCase() === key)
    if (existing) delete headers[existing]
    headers[key] = value
  })

  const timeoutController = options?.signal ? null : new AbortController()
  const timeoutMs = typeof options?.timeoutMs === 'number' && options.timeoutMs > 0
    ? options.timeoutMs
    : DEFAULT_TIMEOUT_MS
  const timeoutId = timeoutController
    ? window.setTimeout(() => timeoutController.abort(), timeoutMs)
    : null

  const sentAuth = captureAuthRequest(headers, options?.signal || timeoutController?.signal)
  let res: Response
  try {
    const { timeoutMs: _timeoutMs, ...requestOptions } = options || {}
    res = await fetch(`${API_BASE}${path}`, {
      ...requestOptions,
      credentials: 'same-origin',
      headers,
      signal: requestOptions.signal || timeoutController?.signal,
    })
  } catch (error: any) {
    if (error?.name === 'AbortError') {
      throw new Error(interfaceText('请求超时，请稍后重试', 'The request timed out. Try again later.'))
    }
    throw error
  } finally {
    if (timeoutId !== null) {
      window.clearTimeout(timeoutId)
    }
  }

  await handleUnauthorized(path, res.status, sentAuth)

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
