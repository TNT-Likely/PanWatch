import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AuthStatus } from '../../packages/api/src/auth-session'

const prefix = '/apps/panwatch'
const oidc: AuthStatus = {
  initialized: true, oidc_enabled: true, password_enabled: true, authenticated: true,
  auth_method: 'oidc', csrf_token: 'cookie-a', oidc_login_url: `https://example.test${prefix}/api/auth/oidc/login`,
}
const unauthenticated: AuthStatus = { ...oidc, authenticated: false, auth_method: null, csrf_token: null }
const password: AuthStatus = { ...oidc, auth_method: 'password', csrf_token: null }
const credential = { token: 'password-b', expires_at: '2099-01-01T00:00:00.100Z' }
const nextCredential = { token: 'password-c', expires_at: '2099-01-01T00:00:00.200Z' }
const response = (data: unknown, status = 200) => new Response(JSON.stringify({ code: status === 200 ? 0 : status, data }), { status })
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}
function expectCredential(expected = credential) {
  expect(localStorage.getItem('token')).toBe(expected.token)
  expect(localStorage.getItem('token_expires')).toBe(expected.expires_at)
}
let client: typeof import('../../packages/api/src/client')
let session: typeof import('../../packages/api/src/auth-session')
let tabBSession: typeof import('../../packages/api/src/auth-session')
let tabBAuth: typeof import('../../packages/api/src/auth')
let sse: typeof import('../../packages/api/src/sse')
let pdf: typeof import('../../packages/api/src/tradingagents')
let location: { pathname: string; href: string }
beforeEach(async () => {
  vi.resetModules()
  localStorage.clear()
  const meta = document.createElement('meta')
  meta.name = 'panwatch-base-path'
  meta.content = prefix
  document.head.append(meta)
  location = { pathname: `${prefix}/portfolio`, href: '' }
  vi.stubGlobal('window', { location, localStorage, setTimeout, clearTimeout })
  session = await import('../../packages/api/src/auth-session')
  client = await import('../../packages/api/src/client')
  sse = await import('../../packages/api/src/sse')
  pdf = await import('../../packages/api/src/tradingagents')
  session.setAuthStatus(oidc)

  // Separate module graphs model two tabs sharing only browser localStorage.
  // A is backgrounded when its cookie expires; B uses the real login helper.
  vi.resetModules()
  tabBSession = await import('../../packages/api/src/auth-session')
  tabBAuth = await import('../../packages/api/src/auth')
  tabBSession.setAuthStatus(unauthenticated)
  tabBAuth.rememberPasswordLogin(credential)
  expect(tabBSession.getAuthStatus()?.auth_method).toBe('password')
  expect(session.getAuthStatus()).toBe(oidc)
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  localStorage.clear()
  document.querySelector('meta[name="panwatch-base-path"]')?.remove()
})
const transports = ['API read', 'API mutation', 'SSE read', 'SSE mutation', 'PDF'] as const
type Transport = typeof transports[number]
function start(transport: Transport, signal?: AbortSignal) {
  const request = transport.startsWith('API')
    ? client.fetchAPI('/notifications/summary', { method: transport === 'API mutation' ? 'POST' : 'GET', signal })
    : transport.startsWith('SSE')
      ? sse.readSSE('/assistant/stream', { method: transport === 'SSE mutation' ? 'POST' : 'GET', signal, onEvent: vi.fn() })
      : pdf.tradingAgentsApi.downloadAnalysisPdf('TEST', '2026-10-11')
  return request.then(value => value, error => error as Error)
}
function bearer(options: RequestInit) {
  return new Headers(options.headers).get('Authorization')
}

for (const transport of transports) describe(transport, () => {
  it('verifies the other tab password login without replay, then authenticates a new request', async () => {
    const published: Array<AuthStatus | null> = []
    const unsubscribe = session.subscribeAuthStatus(() => published.push(session.getAuthStatus()))
    vi.stubGlobal('URL', class extends URL {
      static createObjectURL = vi.fn(() => 'blob:report')
      static revokeObjectURL = vi.fn()
    })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const fetch = vi.fn(async (url: string, options: RequestInit) => {
      const valid = bearer(options) === `Bearer ${credential.token}`
      if (url.endsWith('/auth/status')) return response(valid ? password : unauthenticated)
      if (!valid) return response(null, 401)
      if (transport.startsWith('SSE')) return new Response('id: 1\nevent: done\ndata: {}\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
      if (transport === 'PDF') return new Response('PDF report', { headers: { 'Content-Type': 'application/pdf' } })
      return response({ ok: true })
    })
    vi.stubGlobal('fetch', fetch)
    try {
      expect(await start(transport)).toBeInstanceOf(client.AuthChangedError)
      expectCredential()
      expect(session.getAuthStatus()).toEqual(password)
      expect(published).toEqual([password])
      expect(location.href).toBe('')
      expect(fetch).toHaveBeenCalledTimes(3)
      expect(fetch.mock.calls.slice(1).map(([url]) => url)).toEqual([`${prefix}/api/auth/status`, `${prefix}/api/auth/status`])
      expect(fetch.mock.calls.map(([, options]) => bearer(options))).toEqual([null, null, `Bearer ${credential.token}`])
      expect(fetch.mock.calls[2][1]).toMatchObject({ credentials: 'same-origin', cache: 'no-store' })
      const result = await start(transport)
      expect(result).not.toBeInstanceOf(Error)
      expect(fetch).toHaveBeenCalledTimes(4)
      expect(bearer(fetch.mock.calls[3][1])).toBe(`Bearer ${credential.token}`)
      expect(new Headers(fetch.mock.calls[3][1].headers).has('X-CSRF-Token')).toBe(false)
      expectCredential()
    } finally { unsubscribe() }
  })

  it.each(['offline', 'unauthorized', 'server failure', 'malformed'] as const)('preserves credentials and verified state when the bearer probe is %s', async failure => {
    const published = vi.fn()
    const unsubscribe = session.subscribeAuthStatus(published)
    const fetch = vi.fn().mockResolvedValueOnce(response(null, 401)).mockResolvedValueOnce(response(unauthenticated))
    if (failure === 'offline') fetch.mockRejectedValueOnce(new Error('offline'))
    else fetch.mockResolvedValueOnce(failure === 'malformed' ? response({ initialized: true }) : response(null, failure === 'unauthorized' ? 401 : 503))
    vi.stubGlobal('fetch', fetch)
    try {
      expect(await start(transport)).toBeInstanceOf(client.AuthChangedError)
      expectCredential()
      expect(session.getAuthStatus()).toBe(oidc)
      expect(published).not.toHaveBeenCalled()
      expect(location.href).toBe('')
      expect(fetch).toHaveBeenCalledTimes(3)
    } finally { unsubscribe() }
  })

  it('expires only after the bearer is also rejected by valid status', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(null, 401))
      .mockResolvedValueOnce(response(unauthenticated)).mockResolvedValueOnce(response(unauthenticated))
    vi.stubGlobal('fetch', fetch)
    expect(await start(transport)).toBeInstanceOf(client.AuthExpiredError)
    expect(fetch).toHaveBeenCalledTimes(3)
    expect(bearer(fetch.mock.calls[2][1])).toBe(`Bearer ${credential.token}`)
    expect(localStorage.getItem('token')).toBeNull()
    expect(session.getAuthStatus()?.authenticated).toBe(false)
    expect(location.href).toBe(`${prefix}/login`)
  })
})

it.each(['first status', 'bearer probe'] as const)('keeps cookie priority when a valid cookie is found during %s', async step => {
  const newer = { ...oidc, csrf_token: 'cookie-c' }
  const fetch = vi.fn().mockResolvedValueOnce(response(null, 401))
  if (step === 'bearer probe') fetch.mockResolvedValueOnce(response(unauthenticated))
  fetch.mockResolvedValueOnce(response(newer))
  vi.stubGlobal('fetch', fetch)
  expect(await start('API mutation')).toBeInstanceOf(client.AuthChangedError)
  expect(session.getAuthStatus()).toEqual(newer)
  expect(localStorage.getItem('token')).toBeNull()
  expect(location.href).toBe('')
  expect(fetch).toHaveBeenCalledTimes(step === 'first status' ? 2 : 3)
  if (step === 'first status') expect(fetch.mock.calls.every(([, options]) => bearer(options) === null)).toBe(true)
  expect(client.authHeaders('POST')).toEqual({ 'X-CSRF-Token': 'cookie-c' })
})

it.each(['first status', 'bearer probe'] as const)('honors disabled password fallback in %s', async step => {
  const disabled = { ...unauthenticated, password_enabled: false }
  const fetch = vi.fn().mockResolvedValueOnce(response(null, 401))
  if (step === 'bearer probe') fetch.mockResolvedValueOnce(response(unauthenticated))
  fetch.mockResolvedValueOnce(response(disabled))
  vi.stubGlobal('fetch', fetch)
  expect(await start('API read')).toBeInstanceOf(client.AuthExpiredError)
  expect(session.getAuthStatus()).toEqual(disabled)
  expect(localStorage.getItem('token')).toBeNull()
  expect(fetch).toHaveBeenCalledTimes(step === 'first status' ? 2 : 3)
  if (step === 'first status') expect(fetch.mock.calls.every(([, options]) => bearer(options) === null)).toBe(true)
})

it('uses fresh server policy when the old tab remembers SSO-only', async () => {
  session.setAuthStatus({ ...oidc, password_enabled: false })
  tabBAuth.rememberPasswordLogin(credential)
  const fetch = vi.fn().mockResolvedValueOnce(response(null, 401))
    .mockResolvedValueOnce(response(unauthenticated)).mockResolvedValueOnce(response(password))
  vi.stubGlobal('fetch', fetch)
  expect(await start('API read')).toBeInstanceOf(client.AuthChangedError)
  expectCredential()
  expect(session.getAuthStatus()).toEqual(password)
  expect(fetch).toHaveBeenCalledTimes(3)
})

it.each(['missing', 'invalid'] as const)('lets the server verify a candidate with %s local expiry metadata', async expiry => {
  if (expiry === 'missing') localStorage.removeItem('token_expires')
  else localStorage.setItem('token_expires', 'invalid-date')
  const fetch = vi.fn().mockResolvedValueOnce(response(null, 401))
    .mockResolvedValueOnce(response(unauthenticated)).mockResolvedValueOnce(response(password))
  vi.stubGlobal('fetch', fetch)
  expect(await start('API read')).toBeInstanceOf(client.AuthChangedError)
  expect(session.getAuthStatus()).toEqual(password)
  expect(localStorage.getItem('token')).toBe(credential.token)
  expect(fetch).toHaveBeenCalledTimes(3)
  expect(bearer(fetch.mock.calls[2][1])).toBe(`Bearer ${credential.token}`)
  expect(location.href).toBe('')
})

it('does not probe an already expired suppressed password token', async () => {
  tabBAuth.rememberPasswordLogin({ ...credential, expires_at: '2000-01-01T00:00:00Z' })
  const fetch = vi.fn().mockResolvedValueOnce(response(null, 401)).mockResolvedValueOnce(response(unauthenticated))
  vi.stubGlobal('fetch', fetch)
  expect(await start('API read')).toBeInstanceOf(client.AuthExpiredError)
  expect(fetch).toHaveBeenCalledTimes(2)
  expect(fetch.mock.calls.every(([, options]) => bearer(options) === null)).toBe(true)
  expect(localStorage.getItem('token')).toBeNull()
})

for (const step of ['first status', 'bearer probe'] as const) {
  it.each(['new token', 'new expiry', 'OIDC login', 'logout', 'SSO-only'] as const)(`does not probe or publish over %s during ${step}`, async change => {
    const pendingStatus = deferred<Response>()
    const fetch = vi.fn().mockResolvedValueOnce(response(null, 401))
    if (step === 'bearer probe') fetch.mockResolvedValueOnce(response(unauthenticated))
    fetch.mockReturnValueOnce(pendingStatus.promise)
    vi.stubGlobal('fetch', fetch)
    const pending = start('API mutation')
    const count = step === 'first status' ? 2 : 3
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(count))
    if (change === 'new token') tabBAuth.rememberPasswordLogin(nextCredential)
    if (change === 'new expiry') tabBAuth.rememberPasswordLogin({ ...nextCredential, token: credential.token })
    if (change === 'OIDC login') session.setAuthStatus({ ...oidc, csrf_token: 'cookie-c' })
    if (change === 'logout') tabBSession.clearAuthSession()
    if (change === 'SSO-only') session.setAuthStatus({ ...unauthenticated, password_enabled: false })
    const current = session.getAuthStatus()
    pendingStatus.resolve(response(step === 'first status' ? unauthenticated : password))
    expect(await pending).toBeInstanceOf(client.AuthChangedError)
    expect(session.getAuthStatus()).toBe(current)
    expect(fetch).toHaveBeenCalledTimes(count)
    expect(location.href).toBe('')
    if (change === 'new token') expectCredential(nextCredential)
    else if (change === 'new expiry') expectCredential({ ...nextCredential, token: credential.token })
    else expect(localStorage.getItem('token')).toBeNull()
  })

  it.each(['API mutation', 'SSE mutation'] as const)(`preserves cancellation and leaves no partial publication during ${step}: %s`, async transport => {
    const pendingStatus = deferred<Response>()
    const fetch = vi.fn().mockResolvedValueOnce(response(null, 401))
    if (step === 'bearer probe') fetch.mockResolvedValueOnce(response(unauthenticated))
    fetch.mockReturnValueOnce(pendingStatus.promise)
    vi.stubGlobal('fetch', fetch)
    const controller = new AbortController()
    const pending = start(transport, controller.signal)
    const count = step === 'first status' ? 2 : 3
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(count))
    controller.abort()
    pendingStatus.resolve(response(step === 'first status' ? unauthenticated : password))
    expect(await pending).toMatchObject({ name: 'AbortError' })
    expect(session.getAuthStatus()).toBe(oidc)
    expectCredential()
    expect(location.href).toBe('')
    expect(fetch).toHaveBeenCalledTimes(count)
  })
}

it('deduplicates standalone status consumers across both probes without partial publication', async () => {
  const cookieStatus = deferred<Response>()
  const passwordStatus = deferred<Response>()
  const fetch = vi.fn().mockReturnValueOnce(cookieStatus.promise).mockReturnValueOnce(passwordStatus.promise)
  vi.stubGlobal('fetch', fetch)
  const first = client.loadAuthStatus()
  expect(client.loadAuthStatus()).toBe(first)
  cookieStatus.resolve(response(unauthenticated))
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
  expect(client.loadAuthStatus()).toBe(first)
  expect(session.getAuthStatus()).toBe(oidc)
  expectCredential()
  passwordStatus.resolve(response(password))
  expect(await first).toEqual(password)
  expect(session.getAuthStatus()).toEqual(password)
  expectCredential()
  expect(fetch).toHaveBeenCalledTimes(2)
})

it('does not share bearer-probe cancellation with independent status consumers', async () => {
  const canceledProbe = deferred<Response>()
  const independentProbe = deferred<Response>()
  const fetch = vi.fn().mockResolvedValueOnce(response(null, 401)).mockResolvedValueOnce(response(unauthenticated))
    .mockReturnValueOnce(canceledProbe.promise).mockResolvedValueOnce(response(unauthenticated)).mockReturnValueOnce(independentProbe.promise)
  vi.stubGlobal('fetch', fetch)
  const controller = new AbortController()
  const pending = start('SSE mutation', controller.signal)
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(3))
  const independent = client.loadAuthStatus()
  expect(client.loadAuthStatus()).toBe(independent)
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(5))
  controller.abort()
  canceledProbe.resolve(response(unauthenticated))
  expect(await pending).toMatchObject({ name: 'AbortError' })
  expect(session.getAuthStatus()).toBe(oidc)
  expect(client.loadAuthStatus()).toBe(independent)
  independentProbe.resolve(response(password))
  expect(await independent).toEqual(password)
  expectCredential()
  expect(location.href).toBe('')
})

it('ignores concurrent old 401s while verifying the newly discovered password login', async () => {
  const first = deferred<Response>()
  const second = deferred<Response>()
  const third = deferred<Response>()
  const probe = deferred<Response>()
  const fetch = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise).mockReturnValueOnce(third.promise)
    .mockResolvedValueOnce(response(unauthenticated)).mockReturnValueOnce(probe.promise)
  vi.stubGlobal('fetch', fetch)
  const requests = (['API mutation', 'SSE mutation', 'PDF'] as const).map(transport => start(transport))
  first.resolve(response(null, 401))
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(5))
  second.resolve(response(null, 401))
  expect(await requests[1]).toBeInstanceOf(client.AuthChangedError)
  probe.resolve(response(password))
  expect(await requests[0]).toBeInstanceOf(client.AuthChangedError)
  third.resolve(response(null, 401))
  expect(await requests[2]).toBeInstanceOf(client.AuthChangedError)
  expect(session.getAuthStatus()).toEqual(password)
  expectCredential()
  expect(location.href).toBe('')
  expect(fetch).toHaveBeenCalledTimes(5)
})

it('bounds the bearer probe with the normal timeout without losing either tab login', async () => {
  vi.useFakeTimers()
  window.setTimeout = setTimeout
  window.clearTimeout = clearTimeout
  const fetch = vi.fn().mockResolvedValueOnce(response(null, 401)).mockResolvedValueOnce(response(unauthenticated))
    .mockImplementationOnce((_url, options: RequestInit) => new Promise((_resolve, reject) => {
      options.signal?.addEventListener('abort', () => reject(new DOMException('Timed out', 'AbortError')))
    }))
  vi.stubGlobal('fetch', fetch)
  const pending = start('API mutation')
  await vi.advanceTimersByTimeAsync(20000)
  expect(await pending).toBeInstanceOf(client.AuthChangedError)
  expect(fetch).toHaveBeenCalledTimes(3)
  expect(fetch.mock.calls[2][1].signal.aborted).toBe(true)
  expect(session.getAuthStatus()).toBe(oidc)
  expectCredential()
  expect(location.href).toBe('')
})
