import { interfaceText, isEnglishInterface } from './locale'

const API_BASE = '/api'
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
      throw new Error(interfaceText('请求超时，请稍后重试', 'The request timed out. Try again later.'))
    }
    throw error
  } finally {
    if (timeoutId !== null) {
      window.clearTimeout(timeoutId)
    }
  }

  if (res.status === 401 && token) {
    logout()
    throw new Error(interfaceText('登录已过期', 'Your session has expired. Sign in again.'))
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
