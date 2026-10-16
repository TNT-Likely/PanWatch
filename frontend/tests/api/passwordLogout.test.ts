import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { authApi, rememberPasswordLogin } from '../../packages/api/src/auth'
import { clearAuthSession, getAuthStatus, setAuthStatus, subscribeAuthStatus, type AuthStatus } from '../../packages/api/src/auth-session'
import { getToken, isAuthenticated, logout } from '../../packages/api/src/client'

const passwordStatus: AuthStatus = {
  initialized: true, oidc_enabled: true, password_enabled: true, authenticated: true,
  auth_method: 'password', csrf_token: null, oidc_login_url: 'https://panwatch.example/api/auth/oidc/login',
}
const originalCredential = { token: 'password-session-a', expires_at: '2099-01-01T00:00:00.100Z' }
const newerCredential = { token: 'password-session-b', expires_at: '2099-01-01T00:00:00.200Z' }
const response = (data: unknown, status = 200) => new Response(JSON.stringify({ code: status === 200 ? 0 : status, data }), { status })
const csrfRejected = () => new Response(JSON.stringify({ code: 403, error_code: 'csrf_invalid', data: null }), { status: 403 })
const deferred = <T,>() => {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

function writeCredential(data = newerCredential) {
  // Another tab writes shared storage without updating this tab's AuthStatus.
  localStorage.setItem('token', data.token)
  localStorage.setItem('token_expires', data.expires_at)
}

function expectCredential(data = newerCredential) {
  expect(localStorage.getItem('token')).toBe(data.token)
  expect(localStorage.getItem('token_expires')).toBe(data.expires_at)
}

beforeEach(() => {
  localStorage.clear()
  setAuthStatus(passwordStatus)
  rememberPasswordLogin(originalCredential)
  window.history.replaceState(null, '', '/login')
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  clearAuthSession()
})

describe('password logout credential identity', () => {
  it.each([200, 401])('preserves another tab\'s newer credential after an old logout returns %s', async status => {
    const finish = deferred<Response>()
    const fetch = vi.fn().mockReturnValueOnce(finish.promise).mockResolvedValueOnce(response({ logged_out: true }))
    vi.stubGlobal('fetch', fetch)
    const sentStatus = getAuthStatus()
    const pending = logout()
    expect(logout()).toBe(pending)
    expect(fetch.mock.calls[0][1].headers).toEqual({ Authorization: `Bearer ${originalCredential.token}` })
    writeCredential()
    expect(getAuthStatus()).toBe(sentStatus)
    finish.resolve(response(null, status))
    await expect(pending).rejects.toThrow('Authentication changed')
    expectCredential()
    expect(getAuthStatus()).toBe(sentStatus)
    expect(isAuthenticated()).toBe(true)
    expect(fetch).toHaveBeenCalledTimes(1)
    await logout()
    expect(fetch.mock.calls[1][1].headers).toEqual({ Authorization: `Bearer ${newerCredential.token}` })
    expect(localStorage.getItem('token')).toBeNull()
    expect(isAuthenticated()).toBe(false)
  })

  it.each(['same tab', 'another tab'])('distinguishes same-second JWT logins by expiry in %s', async source => {
    const finish = deferred<Response>()
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(finish.promise))
    const pending = logout()
    const newer = { ...newerCredential, token: originalCredential.token }
    if (source === 'same tab') rememberPasswordLogin(newer)
    else writeCredential(newer)
    finish.resolve(response({ logged_out: true }))
    await expect(pending).rejects.toThrow('Authentication changed')
    expectCredential(newer)
    expect(isAuthenticated()).toBe(true)
  })

  it('preserves a new token even when the expiry and stale auth status are unchanged', async () => {
    setAuthStatus({ ...passwordStatus, authenticated: false, auth_method: null })
    const sentStatus = getAuthStatus()
    const finish = deferred<Response>()
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(finish.promise))
    const pending = logout()
    const newer = { ...newerCredential, expires_at: originalCredential.expires_at }
    writeCredential(newer)
    finish.resolve(response({ logged_out: true }))
    await expect(pending).rejects.toThrow('Authentication changed')
    expectCredential(newer)
    expect(getAuthStatus()).toBe(sentStatus)
  })

  it('checks credential identity after reading a delayed response body', async () => {
    const body = deferred<unknown>()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => body.promise }))
    const pending = logout()
    await Promise.resolve()
    writeCredential()
    body.resolve({ code: 0, data: { logged_out: true } })
    await expect(pending).rejects.toThrow('Authentication changed')
    expectCredential()
  })

  it('does not start a CSRF refresh for credentials replaced before the rejection', async () => {
    const finish = deferred<Response>()
    const fetch = vi.fn().mockReturnValue(finish.promise)
    vi.stubGlobal('fetch', fetch)
    const pending = logout()
    writeCredential()
    finish.resolve(csrfRejected())
    await expect(pending).rejects.toThrow('Authentication changed')
    expectCredential()
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it.each(['password allowed', 'SSO only', 'OIDC authenticated'])('rejects a stale %s refresh before it can overwrite a newer password credential', async policy => {
    const finish = deferred<Response>()
    const fetch = vi.fn().mockResolvedValueOnce(csrfRejected()).mockReturnValueOnce(finish.promise)
    vi.stubGlobal('fetch', fetch)
    const sentStatus = getAuthStatus()
    const pending = logout()
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
    writeCredential()
    finish.resolve(response({
      ...passwordStatus, password_enabled: policy !== 'SSO only',
      authenticated: policy === 'OIDC authenticated',
      auth_method: policy === 'OIDC authenticated' ? 'oidc' : null,
      csrf_token: policy === 'OIDC authenticated' ? 'fresh-csrf' : null,
    }))
    await expect(pending).rejects.toThrow('Authentication changed')
    expectCredential()
    expect(getAuthStatus()).toBe(sentStatus)
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('does not clear a credential installed as the fresh unauthenticated status is published', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(csrfRejected()).mockResolvedValueOnce(response({
      ...passwordStatus, authenticated: false, auth_method: null,
    }))
    vi.stubGlobal('fetch', fetch)
    const unsubscribe = subscribeAuthStatus(() => {
      if (!getAuthStatus()?.authenticated) writeCredential()
    })
    try {
      await expect(logout()).rejects.toThrow('Authentication changed')
      expectCredential()
      expect(fetch).toHaveBeenCalledTimes(2)
    } finally {
      unsubscribe()
    }
  })

  it('does not let an older status request overwrite a newer credential from another tab', async () => {
    const finish = deferred<Response>()
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(finish.promise))
    const sentStatus = getAuthStatus()
    const pending = authApi.status()
    writeCredential()
    finish.resolve(response({ ...passwordStatus, password_enabled: false, authenticated: false, auth_method: null }))
    expect(await pending).toBe(sentStatus)
    expectCredential()
    expect(getAuthStatus()).toBe(sentStatus)
  })

  it.each([200, 401])('clears an unchanged password credential on a confirmed %s', async status => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(null, status)))
    await logout()
    expect(localStorage.getItem('token')).toBeNull()
    expect(localStorage.getItem('token_expires')).toBeNull()
    expect(isAuthenticated()).toBe(false)
  })

  it('normalizes an already expired token before taking the logout identity snapshot', async () => {
    writeCredential({ ...originalCredential, expires_at: '2000-01-01T00:00:00.000Z' })
    const fetch = vi.fn().mockResolvedValue(response({ logged_out: true }))
    vi.stubGlobal('fetch', fetch)
    await logout()
    expect(fetch.mock.calls[0][1].headers).toEqual({})
    expect(localStorage.getItem('token')).toBeNull()
    expect(isAuthenticated()).toBe(false)
  })

  it('allows expiry normalization during a fresh status read without mistaking it for a newer login', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-11T00:00:00Z'))
    writeCredential({ ...originalCredential, expires_at: '2026-10-11T00:00:01Z' })
    const fetch = vi.fn().mockImplementationOnce(async () => {
      vi.setSystemTime(new Date('2026-10-11T00:00:02Z'))
      return csrfRejected()
    }).mockResolvedValueOnce(response({ ...passwordStatus, authenticated: false, auth_method: null }))
    vi.stubGlobal('fetch', fetch)
    await logout()
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(fetch.mock.calls[1][1].headers).toEqual({})
    expect(isAuthenticated()).toBe(false)
  })

  it.each(['offline', 'timeout', 'server error'])('retains a newer credential after %s and allows a manual retry', async failure => {
    const finish = deferred<Response>()
    const fetch = vi.fn().mockReturnValueOnce(finish.promise).mockResolvedValueOnce(response({ logged_out: true }))
    vi.stubGlobal('fetch', fetch)
    const pending = logout()
    writeCredential()
    if (failure === 'server error') finish.resolve(response(null, 500))
    else finish.reject(failure === 'timeout' ? new DOMException('Timed out', 'AbortError') : new Error('offline'))
    await expect(pending).rejects.toThrow()
    expectCredential()
    expect(fetch).toHaveBeenCalledTimes(1)
    await logout()
    expect(isAuthenticated()).toBe(false)
  })

  it('does not mistake expiry cleanup during an in-flight logout for a credential to erase', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-11T00:00:00Z'))
    writeCredential({ ...originalCredential, expires_at: '2026-10-11T00:00:01Z' })
    const finish = deferred<Response>()
    const fetch = vi.fn().mockReturnValueOnce(finish.promise).mockResolvedValueOnce(response(null, 401))
    vi.stubGlobal('fetch', fetch)
    const pending = logout()
    vi.setSystemTime(new Date('2026-10-11T00:00:02Z'))
    expect(getToken()).toBeNull()
    finish.resolve(response({ logged_out: true }))
    await expect(pending).rejects.toThrow('Authentication changed')
    await logout()
    expect(isAuthenticated()).toBe(false)
  })

  it('retains the credential and permits retry if storage reads become unavailable', async () => {
    const finish = deferred<Response>()
    const fetch = vi.fn().mockReturnValueOnce(finish.promise).mockResolvedValueOnce(response({ logged_out: true }))
    vi.stubGlobal('fetch', fetch)
    const pending = logout()
    const storageRead = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('Blocked', 'SecurityError') })
    finish.resolve(response({ logged_out: true }))
    await expect(pending).rejects.toThrow('Authentication changed')
    storageRead.mockRestore()
    expectCredential(originalCredential)
    await logout()
    expect(isAuthenticated()).toBe(false)
  })

  it('rejects unavailable storage before starting a password status request without leaving a pending read', async () => {
    const fetch = vi.fn().mockResolvedValue(response(passwordStatus))
    vi.stubGlobal('fetch', fetch)
    const storageRead = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('Blocked', 'SecurityError') })
    await expect(authApi.status()).rejects.toThrow('Blocked')
    expect(fetch).not.toHaveBeenCalled()
    storageRead.mockRestore()
    expectCredential(originalCredential)
    expect(await authApi.status()).toEqual(passwordStatus)
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('allows a manual logout retry if removing the credential is temporarily blocked', async () => {
    const fetch = vi.fn().mockImplementation(async () => response({ logged_out: true }))
    vi.stubGlobal('fetch', fetch)
    const storageWrite = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new DOMException('Read only', 'SecurityError') })
    await expect(logout()).rejects.toThrow('Read only')
    storageWrite.mockRestore()
    expectCredential(originalCredential)
    expect(isAuthenticated()).toBe(true)
    await logout()
    expect(isAuthenticated()).toBe(false)
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('still aborts a timed-out logout without replaying it and permits a manual retry', async () => {
    vi.useFakeTimers()
    const fetch = vi.fn().mockImplementationOnce((_url, options: RequestInit) => new Promise<Response>((_resolve, reject) => {
      options.signal?.addEventListener('abort', () => reject(new DOMException('Timed out', 'AbortError')))
    })).mockResolvedValueOnce(response({ logged_out: true }))
    vi.stubGlobal('fetch', fetch)
    const pending = logout()
    const rejection = expect(pending).rejects.toThrow('Timed out')
    await vi.advanceTimersByTimeAsync(20000)
    await rejection
    expectCredential(originalCredential)
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0][1].signal.aborted).toBe(true)
    await logout()
    expect(isAuthenticated()).toBe(false)
  })

  it('preserves a password login in another tab while an older OIDC logout completes', async () => {
    setAuthStatus({ ...passwordStatus, auth_method: 'oidc', csrf_token: 'old-cookie-csrf' })
    const finish = deferred<Response>()
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(finish.promise))
    const pending = logout()
    writeCredential()
    finish.resolve(response({ logged_out: true }))
    await expect(pending).rejects.toThrow('Authentication changed')
    expectCredential()
    expect(getAuthStatus()?.csrf_token).toBe('old-cookie-csrf')
  })

  it('allows readable-storage OIDC refresh to discard a stale password marker and retry', async () => {
    setAuthStatus({ ...passwordStatus, auth_method: 'oidc', csrf_token: 'old-cookie-csrf' })
    writeCredential(originalCredential)
    const fetch = vi.fn().mockResolvedValueOnce(csrfRejected()).mockResolvedValueOnce(response({
      ...passwordStatus, auth_method: 'oidc', csrf_token: 'new-cookie-csrf',
    })).mockResolvedValueOnce(response({ logged_out: true }))
    vi.stubGlobal('fetch', fetch)
    await logout()
    expect(fetch).toHaveBeenCalledTimes(3)
    expect(fetch.mock.calls[2][1].headers).toEqual({ 'X-CSRF-Token': 'new-cookie-csrf' })
    expect(localStorage.getItem('token')).toBeNull()
    expect(isAuthenticated()).toBe(false)
  })

  it('retains OIDC state rather than clearing unverified credentials when storage reads are blocked', async () => {
    setAuthStatus({ ...passwordStatus, auth_method: 'oidc', csrf_token: 'cookie-csrf' })
    const fetch = vi.fn().mockImplementation(async () => response({ logged_out: true }))
    vi.stubGlobal('fetch', fetch)
    const storageRead = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('Blocked', 'SecurityError') })
    await expect(logout()).rejects.toThrow('Authentication changed')
    storageRead.mockRestore()
    expect(getAuthStatus()?.csrf_token).toBe('cookie-csrf')
    expect(fetch).toHaveBeenCalledTimes(1)
    await logout()
    expect(isAuthenticated()).toBe(false)
  })
})
