import { afterEach, expect, it, vi } from 'vitest'
import { healthApi } from '../../packages/api/src/health'
import { clearAuthSession, setAuthStatus } from '../../packages/api/src/auth-session'

afterEach(() => { vi.unstubAllGlobals(); clearAuthSession() })

it('uses protected POST for all selfcheck probes and keeps listing read-only', async () => {
  setAuthStatus({ initialized: true, oidc_enabled: true, password_enabled: false, authenticated: true,
    auth_method: 'oidc', csrf_token: 'csrf', oidc_login_url: 'https://example.com/api/auth/oidc/login' })
  const fetch = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ code: 0, data: { items: [] } }))))
  vi.stubGlobal('fetch', fetch)
  await healthApi.selfcheck(true)
  await healthApi.selfcheckKeys(['ai:1', 'notify:2'], true)
  await healthApi.selfcheckList()
  for (const index of [0, 1]) {
    expect(fetch.mock.calls[index][1]).toMatchObject({ method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': 'csrf' } })
  }
  expect(fetch.mock.calls[0][0]).toBe('/api/health/selfcheck?notify_send=true')
  expect(fetch.mock.calls[1][0]).toContain('keys=ai%3A1%2Cnotify%3A2')
  expect(fetch.mock.calls[2][0]).toBe('/api/health/selfcheck?list=1')
  expect(fetch.mock.calls[2][1].method).toBeUndefined()
  expect(fetch.mock.calls[2][1].headers['X-CSRF-Token']).toBeUndefined()
})
