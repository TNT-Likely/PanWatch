import { afterEach, beforeEach, expect, it, vi } from 'vitest'

beforeEach(() => {
  vi.resetModules()
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  document.querySelector('meta[name="panwatch-base-path"]')?.remove()
})

function browserAt(prefix: string) {
  const meta = document.createElement('meta')
  meta.name = 'panwatch-base-path'
  meta.content = prefix
  document.head.append(meta)
  const location = { pathname: `${prefix}/portfolio`, href: '' }
  vi.stubGlobal('window', {
    location,
    setTimeout: window.setTimeout.bind(window),
    clearTimeout: window.clearTimeout.bind(window),
  })
  return location
}

async function signIn(prefix: string) {
  const session = await import('../../packages/api/src/auth-session')
  session.setAuthStatus({
    initialized: true, oidc_enabled: true, password_enabled: false,
    authenticated: true, auth_method: 'oidc', csrf_token: 'path-csrf',
    oidc_login_url: `https://portfolio.example${prefix}/api/auth/oidc/login`,
  })
  return session
}

it.each(['', '/panwatch', '/apps/panwatch'])('keeps OIDC API, stream, PDF and logout requests under %s', async prefix => {
  const location = browserAt(prefix)
  await signIn(prefix)
  const { fetchAPI, logout } = await import('../../packages/api/src/client')
  const { readSSE } = await import('../../packages/api/src/sse')
  const { tradingAgentsApi } = await import('../../packages/api/src/tradingagents')
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ code: 0, data: { ok: true } })))
    .mockResolvedValueOnce(new Response('data: {}\n\n', { headers: { 'Content-Type': 'text/event-stream' } }))
    .mockResolvedValueOnce(new Response('', { status: 404 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ code: 0, data: { logged_out: true } })))
  vi.stubGlobal('fetch', fetchMock)

  await fetchAPI('/accounts', { method: 'POST', body: '{}' })
  expect(fetchMock.mock.calls[0]).toEqual([`${prefix}/api/accounts`, expect.objectContaining({
    credentials: 'same-origin', headers: expect.objectContaining({ 'X-CSRF-Token': 'path-csrf' }),
  })])
  await readSSE('/assistant/chat', { method: 'POST', body: {}, onEvent: () => {} })
  expect(fetchMock.mock.calls[1]).toEqual([`${prefix}/api/assistant/chat`, expect.objectContaining({
    credentials: 'same-origin', headers: expect.objectContaining({ 'X-CSRF-Token': 'path-csrf' }),
  })])
  await expect(tradingAgentsApi.downloadAnalysisPdf('SHOP.TO', '2026-10-11')).rejects.toThrow()
  expect(fetchMock.mock.calls[2]).toEqual([
    `${prefix}/api/agents/tradingagents/analysis/pdf?stock_symbol=SHOP.TO&analysis_date=2026-10-11`,
    expect.objectContaining({ credentials: 'same-origin', headers: {} }),
  ])
  await logout()
  expect(fetchMock.mock.calls[3]).toEqual([`${prefix}/api/auth/logout`, expect.objectContaining({
    method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': 'path-csrf' },
  })])
  expect(location.href).toBe(`${prefix}/login`)
})

it.each(['', '/panwatch', '/apps/panwatch'])('redirects expired sessions once to the canonical %s login page', async prefix => {
  const location = browserAt(prefix)
  const { getAuthStatus } = await signIn(prefix)
  const { authHeaders, captureAuthRequest, handleUnauthorized } = await import('../../packages/api/src/client')
  vi.stubGlobal('fetch', vi.fn().mockImplementation(async () => new Response(JSON.stringify({ code: 0, data: {
    ...getAuthStatus(), authenticated: false, auth_method: null, csrf_token: null,
  } }))))
  await expect(handleUnauthorized('/accounts', 401, captureAuthRequest(authHeaders()))).rejects.toThrow()
  expect(getAuthStatus()?.authenticated).toBe(false)
  expect(location.href).toBe(`${prefix}/login`)

  location.pathname = `${prefix}/login`
  location.href = ''
  await expect(handleUnauthorized('/accounts', 401, captureAuthRequest(authHeaders()))).rejects.toThrow()
  expect(location.href).toBe('')
  await expect(handleUnauthorized('/auth/status', 401, captureAuthRequest(authHeaders()))).resolves.toBeUndefined()
  expect(location.href).toBe('')
})
