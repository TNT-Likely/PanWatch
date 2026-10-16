import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AuthStatus } from '../../packages/api/src/auth-session'

const prefix = '/apps/panwatch'
const passwordStatus: AuthStatus = {
  initialized: true, oidc_enabled: false, password_enabled: true, authenticated: true,
  auth_method: 'password', csrf_token: null, oidc_login_url: null,
}
const oidcStatus: AuthStatus = {
  ...passwordStatus, oidc_enabled: true, auth_method: 'oidc', csrf_token: 'cookie-a',
  oidc_login_url: `https://example.test${prefix}/api/auth/oidc/login`,
}
const oldCredential = { token: 'password-a', expires_at: '2099-01-01T00:00:00.100Z' }
const newCredential = { token: 'password-b', expires_at: '2099-01-01T00:00:00.200Z' }
const response = (data: unknown, status = 200) => new Response(JSON.stringify({ code: status === 200 ? 0 : status, data }), { status })
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}
function writeCredential(credential = newCredential) {
  localStorage.setItem('token', credential.token)
  localStorage.setItem('token_expires', credential.expires_at)
}
function expectCredential(credential = newCredential) {
  expect(localStorage.getItem('token')).toBe(credential.token)
  expect(localStorage.getItem('token_expires')).toBe(credential.expires_at)
}
let client: typeof import('../../packages/api/src/client')
let session: typeof import('../../packages/api/src/auth-session')
let auth: typeof import('../../packages/api/src/auth')
let sse: typeof import('../../packages/api/src/sse')
let pdf: typeof import('../../packages/api/src/tradingagents')
let location: { pathname: string, href: string }
beforeEach(async () => {
  vi.resetModules()
  localStorage.clear()
  const meta = document.createElement('meta')
  meta.name = 'panwatch-base-path'
  meta.content = prefix
  document.head.append(meta)
  location = { pathname: `${prefix}/portfolio`, href: '' }
  vi.stubGlobal('window', { location, localStorage, setTimeout: window.setTimeout.bind(window), clearTimeout: window.clearTimeout.bind(window) })
  client = await import('../../packages/api/src/client')
  session = await import('../../packages/api/src/auth-session')
  auth = await import('../../packages/api/src/auth')
  sse = await import('../../packages/api/src/sse')
  pdf = await import('../../packages/api/src/tradingagents')
  session.setAuthStatus(passwordStatus)
  writeCredential(oldCredential)
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  localStorage.clear()
  document.querySelector('meta[name="panwatch-base-path"]')?.remove()
})
const transports = ['API mutation', 'SSE mutation', 'PDF'] as const
function start(transport: typeof transports[number], signal?: AbortSignal) {
  const request = transport === 'API mutation'
    ? client.fetchAPI('/settings/example', { method: 'POST', body: '{}', signal })
    : transport === 'SSE mutation'
      ? sse.readSSE('/assistant/stream', { method: 'POST', body: {}, signal, onEvent: vi.fn() })
      : pdf.tradingAgentsApi.downloadAnalysisPdf('TEST', '2026-10-11')
  return request.then(() => undefined, error => error as Error)
}
for (const transport of transports) describe(transport, () => {
  it.each(['token', 'expiry', 'same-tab revision'])('preserves a newer login identified by %s after a delayed 401', async change => {
    const finish = deferred<Response>()
    const fetch = vi.fn().mockReturnValue(finish.promise)
    vi.stubGlobal('fetch', fetch)
    const pending = start(transport)
    const credential = change === 'token' ? { ...oldCredential, token: newCredential.token }
      : change === 'expiry' ? { ...newCredential, token: oldCredential.token } : oldCredential
    if (change === 'same-tab revision') auth.rememberPasswordLogin(credential)
    else writeCredential(credential)
    const newerStatus = session.getAuthStatus()
    finish.resolve(response(null, 401))
    expect(await pending).toBeInstanceOf(Error)
    expectCredential(credential)
    expect(session.getAuthStatus()).toBe(newerStatus)
    expect(location.href).toBe('')
    expect(fetch).toHaveBeenCalledTimes(1)
  })
  it.each(['password to OIDC', 'OIDC to OIDC', 'OIDC to password'])('preserves the verified %s transition', async transition => {
    if (transition.startsWith('OIDC')) session.setAuthStatus(oidcStatus)
    const finish = deferred<Response>()
    const fetch = vi.fn().mockReturnValue(finish.promise)
    vi.stubGlobal('fetch', fetch)
    const pending = start(transport)
    if (transition.endsWith('password')) auth.rememberPasswordLogin(newCredential)
    else session.setAuthStatus({ ...oidcStatus, csrf_token: 'cookie-b' })
    const newerStatus = session.getAuthStatus()
    finish.resolve(response(null, 401))
    expect(await pending).toBeInstanceOf(Error)
    expect(session.getAuthStatus()).toBe(newerStatus)
    expect(client.isAuthenticated()).toBe(true)
    expect(location.href).toBe('')
    expect(fetch).toHaveBeenCalledTimes(1)
    if (transition.endsWith('password')) expectCredential()
  })
  it('preserves a cross-tab password login after an old cookie request fails', async () => {
    session.setAuthStatus(oidcStatus)
    const finish = deferred<Response>()
    const fetch = vi.fn().mockReturnValue(finish.promise)
    vi.stubGlobal('fetch', fetch)
    const pending = start(transport)
    writeCredential()
    finish.resolve(response(null, 401))
    expect(await pending).toBeInstanceOf(Error)
    expectCredential()
    expect(session.getAuthStatus()).toEqual(oidcStatus)
    expect(location.href).toBe('')
    expect(fetch).toHaveBeenCalledTimes(1)
  })
  it.each(['unchanged', 'expired before dispatch', 'expired while in flight'])('expires a genuinely rejected password session: %s', async expiry => {
    if (expiry === 'expired before dispatch') writeCredential({ ...oldCredential, expires_at: '2000-01-01T00:00:00Z' })
    const finish = deferred<Response>()
    const fetch = vi.fn().mockReturnValue(finish.promise)
    vi.stubGlobal('fetch', fetch)
    const pending = start(transport)
    if (expiry === 'expired while in flight') {
      vi.spyOn(Date, 'now').mockReturnValue(new Date('2100-01-01').getTime())
      expect(client.getToken()).toBeNull()
    }
    finish.resolve(response(null, 401))
    expect(await pending).toBeInstanceOf(client.AuthExpiredError)
    expect(localStorage.getItem('token')).toBeNull()
    expect(localStorage.getItem('token_expires')).toBeNull()
    expect(session.getAuthStatus()?.authenticated).toBe(false)
    expect(location.href).toBe(`${prefix}/login`)
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0][1].headers.Authorization).toBe(expiry === 'expired before dispatch' ? undefined : `Bearer ${oldCredential.token}`)
  })
  it.each(['cookie', 'password fallback'])('verifies an otherwise invisible newer cross-tab OIDC login from %s', async previous => {
    session.setAuthStatus(previous === 'cookie' ? oidcStatus : { ...passwordStatus, oidc_enabled: true, oidc_login_url: oidcStatus.oidc_login_url })
    const newer = { ...oidcStatus, csrf_token: 'cookie-b' }
    const fetch = vi.fn().mockResolvedValueOnce(response(null, 401)).mockResolvedValueOnce(response(newer))
    vi.stubGlobal('fetch', fetch)
    expect(await start(transport)).toBeInstanceOf(Error)
    expect(session.getAuthStatus()).toEqual(newer)
    expect(client.isAuthenticated()).toBe(true)
    expect(location.href).toBe('')
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(fetch.mock.calls[1]).toEqual([`${prefix}/api/auth/status`, expect.objectContaining({ cache: 'no-store', credentials: 'same-origin' })])
  })
  it('redirects a genuinely expired cookie only after fresh unauthenticated status', async () => {
    session.setAuthStatus(oidcStatus)
    const fetch = vi.fn().mockResolvedValueOnce(response(null, 401)).mockResolvedValueOnce(response({ ...oidcStatus, authenticated: false, auth_method: null, csrf_token: null }))
    vi.stubGlobal('fetch', fetch)
    expect(await start(transport)).toBeInstanceOf(client.AuthExpiredError)
    expect(session.getAuthStatus()?.authenticated).toBe(false)
    expect(location.href).toBe(`${prefix}/login`)
    expect(fetch).toHaveBeenCalledTimes(2)
  })
  it.each(['offline', 'unauthorized', 'malformed'])('preserves unverified cookie state when status verification is %s', async failure => {
    session.setAuthStatus(oidcStatus)
    const fetch = vi.fn().mockResolvedValueOnce(response(null, 401))
    if (failure === 'offline') fetch.mockRejectedValueOnce(new Error('offline'))
    else fetch.mockResolvedValueOnce(failure === 'unauthorized' ? response(null, 401) : response({ initialized: true }))
    vi.stubGlobal('fetch', fetch)
    expect(await start(transport)).toBeInstanceOf(Error)
    expect(session.getAuthStatus()).toEqual(oidcStatus)
    expect(location.href).toBe('')
    expect(fetch).toHaveBeenCalledTimes(2)
  })
  it.each(['same tab', 'another tab'])('preserves a newer password login during cookie verification in %s', async source => {
    session.setAuthStatus(oidcStatus)
    const finish = deferred<Response>()
    const fetch = vi.fn().mockResolvedValueOnce(response(null, 401)).mockReturnValueOnce(finish.promise)
    vi.stubGlobal('fetch', fetch)
    const pending = start(transport)
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
    if (source === 'same tab') auth.rememberPasswordLogin(newCredential)
    else writeCredential()
    const newerStatus = session.getAuthStatus()
    finish.resolve(response({ ...oidcStatus, authenticated: false, auth_method: null, csrf_token: null }))
    expect(await pending).toBeInstanceOf(Error)
    expectCredential()
    expect(session.getAuthStatus()).toBe(newerStatus)
    expect(location.href).toBe('')
    expect(fetch).toHaveBeenCalledTimes(2)
  })
  it('checks for a login installed by a status subscriber before clearing', async () => {
    session.setAuthStatus(oidcStatus)
    const fetch = vi.fn().mockResolvedValueOnce(response(null, 401)).mockResolvedValueOnce(response({ ...oidcStatus, authenticated: false, auth_method: null, csrf_token: null }))
    vi.stubGlobal('fetch', fetch)
    const unsubscribe = session.subscribeAuthStatus(() => { if (!session.getAuthStatus()?.authenticated) writeCredential() })
    try {
      expect(await start(transport)).toBeInstanceOf(Error)
      expectCredential()
      expect(location.href).toBe('')
      expect(fetch).toHaveBeenCalledTimes(2)
    } finally { unsubscribe() }
  })
  it.each([403, 500])('does not clear or replay on HTTP %s', async status => {
    const fetch = vi.fn().mockResolvedValue(response(null, status))
    vi.stubGlobal('fetch', fetch)
    expect(await start(transport)).toBeInstanceOf(Error)
    expectCredential(oldCredential)
    expect(session.getAuthStatus()).toEqual(passwordStatus)
    expect(location.href).toBe('')
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})
it.each(['/auth/status?fresh=1', '/auth/login', '/auth/setup', '/auth/logout'])('keeps %s exempt from unauthorized session handling', async path => {
  session.setAuthStatus(oidcStatus)
  const fetch = vi.fn().mockResolvedValue(response(null, 401))
  vi.stubGlobal('fetch', fetch)
  await expect(client.fetchAPI(path)).rejects.toThrow()
  expect(session.getAuthStatus()).toEqual(oidcStatus)
  expect(location.href).toBe('')
  expect(fetch).toHaveBeenCalledTimes(1)
})
it('stops an obsolete SSE subscription without retrying or clearing its newer login', async () => {
  const finish = deferred<Response>()
  const fetch = vi.fn().mockReturnValue(finish.promise)
  vi.stubGlobal('fetch', fetch)
  const onRetry = vi.fn()
  const onFailed = vi.fn()
  const stop = sse.subscribeSSE('/notifications/stream', { onEvent: vi.fn(), onRetry, onFailed })
  writeCredential()
  finish.resolve(response(null, 401))
  await vi.waitFor(() => expect(onFailed).toHaveBeenCalledTimes(1))
  expect(onRetry).not.toHaveBeenCalled()
  expectCredential()
  expect(location.href).toBe('')
  expect(fetch).toHaveBeenCalledTimes(1)
  stop()
})

it.each([
  { Authorization: 'Bearer custom-token' },
  { authorization: 'Bearer custom-token' },
  new Headers({ Authorization: 'Bearer custom-token' }),
  [['Authorization', 'Bearer custom-token']] as [string, string][],
  { Authorization: '' },
])('does not clear the session for a caller-supplied authorization override: %j', async headers => {
  const fetch = vi.fn().mockResolvedValue(response(null, 401))
  vi.stubGlobal('fetch', fetch)
  await expect(client.fetchAPI('/settings/example', { method: 'POST', headers })).rejects.toBeInstanceOf(client.AuthChangedError)
  expectCredential(oldCredential)
  expect(location.href).toBe('')
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(new Headers(fetch.mock.calls[0][1].headers).get('authorization')).toBe(new Headers(headers).get('authorization'))
})

it('still expires the session for an explicitly matching authorization header', async () => {
  const fetch = vi.fn().mockResolvedValue(response(null, 401))
  vi.stubGlobal('fetch', fetch)
  await expect(client.fetchAPI('/settings/example', { headers: { authorization: `Bearer ${oldCredential.token}` } })).rejects.toBeInstanceOf(client.AuthExpiredError)
  expect(client.isAuthenticated()).toBe(false)
  expect(location.href).toBe(`${prefix}/login`)
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('bounds cookie status verification with the normal timeout and preserves state', async () => {
  vi.useFakeTimers()
  window.setTimeout = setTimeout
  window.clearTimeout = clearTimeout
  session.setAuthStatus(oidcStatus)
  const fetch = vi.fn().mockResolvedValueOnce(response(null, 401)).mockImplementationOnce((_url, options: RequestInit) => new Promise((_resolve, reject) => {
    options.signal?.addEventListener('abort', () => reject(new DOMException('Timed out', 'AbortError')))
  }))
  vi.stubGlobal('fetch', fetch)
  const pending = start('API mutation')
  await vi.advanceTimersByTimeAsync(20000)
  expect(await pending).toBeInstanceOf(client.AuthChangedError)
  expect(fetch).toHaveBeenCalledTimes(2)
  expect(fetch.mock.calls[1][1].signal.aborted).toBe(true)
  expect(session.getAuthStatus()).toEqual(oidcStatus)
  expect(location.href).toBe('')
})

it('ignores concurrent old 401s while cookie verification publishes a newer session', async () => {
  session.setAuthStatus(oidcStatus)
  const first = deferred<Response>()
  const second = deferred<Response>()
  const third = deferred<Response>()
  const verification = deferred<Response>()
  const fetch = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    .mockReturnValueOnce(third.promise).mockReturnValueOnce(verification.promise)
  vi.stubGlobal('fetch', fetch)
  const requests = transports.map(transport => start(transport))
  first.resolve(response(null, 401))
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(4))
  second.resolve(response(null, 401))
  expect(await requests[1]).toBeInstanceOf(client.AuthChangedError)
  const newer = { ...oidcStatus, csrf_token: 'cookie-b' }
  verification.resolve(response(newer))
  expect(await requests[0]).toBeInstanceOf(client.AuthChangedError)
  third.resolve(response(null, 401))
  expect(await requests[2]).toBeInstanceOf(client.AuthChangedError)
  expect(session.getAuthStatus()).toEqual(newer)
  expect(location.href).toBe('')
  expect(fetch).toHaveBeenCalledTimes(4)
})

it.each(['API mutation', 'SSE mutation'] as const)('preserves cancellation during %s cookie verification', async transport => {
  session.setAuthStatus(oidcStatus)
  const finish = deferred<Response>()
  const fetch = vi.fn().mockResolvedValueOnce(response(null, 401)).mockReturnValueOnce(finish.promise)
  vi.stubGlobal('fetch', fetch)
  const controller = new AbortController()
  const pending = start(transport, controller.signal)
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
  controller.abort()
  finish.resolve(response({ ...oidcStatus, authenticated: false, auth_method: null, csrf_token: null }))
  expect(await pending).toMatchObject({ name: 'AbortError' })
  expect(session.getAuthStatus()).toEqual(oidcStatus)
  expect(location.href).toBe('')
  expect(fetch).toHaveBeenCalledTimes(2)
})

it.each(['API mutation', 'SSE mutation'] as const)('does not dispatch verification or clear state after an aborted %s', async transport => {
  session.setAuthStatus(oidcStatus)
  const finish = deferred<Response>()
  const fetch = vi.fn().mockReturnValue(finish.promise)
  vi.stubGlobal('fetch', fetch)
  const controller = new AbortController()
  const pending = start(transport, controller.signal)
  controller.abort()
  finish.resolve(response(null, 401))
  expect(await pending).toMatchObject({ name: 'AbortError' })
  expect(session.getAuthStatus()).toEqual(oidcStatus)
  expect(location.href).toBe('')
  expect(fetch).toHaveBeenCalledTimes(1)
})

for (const transport of ['API mutation', 'SSE mutation'] as const) {
  it.each(['before cancellation', 'after cancellation'])(`does not share ${transport} cancellation with an independent status load: %s`, async when => {
    session.setAuthStatus(oidcStatus)
    const verification = deferred<Response>()
    const independent = deferred<Response>()
    const fetch = vi.fn().mockResolvedValueOnce(response(null, 401))
      .mockReturnValueOnce(verification.promise).mockReturnValueOnce(independent.promise)
    vi.stubGlobal('fetch', fetch)
    const controller = new AbortController()
    const pending = start(transport, controller.signal)
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
    if (when === 'after cancellation') controller.abort()
    const status = client.loadAuthStatus()
    // Unrelated consumers still deduplicate with each other.
    expect(client.loadAuthStatus()).toBe(status)
    const statusResult = status.then(value => ({ value }), error => ({ error }))
    if (when === 'before cancellation') controller.abort()
    verification.resolve(response({ ...oidcStatus, authenticated: false, auth_method: null, csrf_token: null }))
    expect(await pending).toMatchObject({ name: 'AbortError' })
    expect(session.getAuthStatus()).toEqual(oidcStatus)
    expect(client.loadAuthStatus()).toBe(status)
    const newer = { ...oidcStatus, csrf_token: 'independent-cookie' }
    independent.resolve(response(newer))
    expect(await statusResult).toEqual({ value: newer })
    expect(session.getAuthStatus()).toEqual(newer)
    expect(location.href).toBe('')
    expect(fetch).toHaveBeenCalledTimes(3)
  })
}

it.each(['API mutation', 'SSE mutation'] as const)('preserves an independently published status before canceled %s verification settles', async transport => {
  session.setAuthStatus(oidcStatus)
  const verification = deferred<Response>()
  const independent = deferred<Response>()
  const fetch = vi.fn().mockResolvedValueOnce(response(null, 401))
    .mockReturnValueOnce(verification.promise).mockReturnValueOnce(independent.promise)
  vi.stubGlobal('fetch', fetch)
  const controller = new AbortController()
  const pending = start(transport, controller.signal)
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
  const status = client.loadAuthStatus()
  controller.abort()
  const newer = { ...oidcStatus, csrf_token: 'independent-cookie' }
  independent.resolve(response(newer))
  expect(await status).toEqual(newer)
  verification.resolve(response({ ...oidcStatus, authenticated: false, auth_method: null, csrf_token: null }))
  expect(await pending).toMatchObject({ name: 'AbortError' })
  expect(session.getAuthStatus()).toEqual(newer)
  expect(location.href).toBe('')
  expect(fetch).toHaveBeenCalledTimes(3)
})
