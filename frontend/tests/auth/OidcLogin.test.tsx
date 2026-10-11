import { StrictMode } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Login from '@/pages/Login'
import RequireAuth from '@/components/RequireAuth'
import { ToastProvider } from '@panwatch/base-ui/components/ui/toast'
import { changeLocale } from '@/i18n'
import { authApi } from '@panwatch/api/auth'
import { clearAuthSession, setAuthStatus, type AuthStatus } from '@panwatch/api/auth-session'

const passwordStatus: AuthStatus = {
  initialized: true, oidc_enabled: false, password_enabled: true, authenticated: false,
  auth_method: null, csrf_token: null, oidc_login_url: null,
}
const oidcStatus: AuthStatus = {
  ...passwordStatus, initialized: false, oidc_enabled: true, password_enabled: false,
  oidc_login_url: 'https://panwatch.example/api/auth/oidc/login',
}
const response = (data: unknown) => new Response(JSON.stringify({ code: 0, data }), {
  headers: { 'Content-Type': 'application/json' },
})
function renderLogin(entry = '/login') {
  return render(<StrictMode><MemoryRouter initialEntries={[entry]}><ToastProvider><Routes>
    <Route path="/login" element={<Login />} />
    <Route path="/" element={<div>Protected home</div>} />
  </Routes></ToastProvider></MemoryRouter></StrictMode>)
}
function renderGuard() {
  return render(<StrictMode><MemoryRouter initialEntries={['/']}><Routes>
    <Route path="/" element={<RequireAuth><div>Protected home</div></RequireAuth>} />
    <Route path="/login" element={<div>Login destination</div>} />
  </Routes></MemoryRouter></StrictMode>)
}
beforeEach(async () => {
  localStorage.clear()
  setAuthStatus(passwordStatus)
  await changeLocale('en-US')
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  localStorage.clear()
  clearAuthSession()
})

describe('OIDC sign-in surface', () => {
  it('shows only SSO and never initial password setup when password access is disabled', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(oidcStatus)))
    renderLogin()
    expect(await screen.findByRole('button', { name: 'Continue with SSO' })).toBeTruthy()
    expect(screen.queryByLabelText('Username')).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Secure your installation' })).toBeNull()
  })

  it('shows the explicit password fallback alongside SSO', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ ...oidcStatus, initialized: true, password_enabled: true })))
    renderLogin()
    expect(await screen.findByRole('button', { name: 'Continue with SSO' })).toBeTruthy()
    expect(screen.getByLabelText('Username')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeTruthy()
  })

  it('preserves local password setup when OIDC is off', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ ...passwordStatus, initialized: false })))
    renderLogin()
    expect(await screen.findByRole('heading', { name: 'Secure your installation' })).toBeTruthy()
    expect(screen.getByLabelText('Confirm password')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Continue with SSO' })).toBeNull()
  })

  it('fails closed on status errors and retries without exposing a password form', async () => {
    const fetch = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(response(oidcStatus))
    vi.stubGlobal('fetch', fetch)
    renderLogin()
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.queryByLabelText('Username')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('button', { name: 'Continue with SSO' })).toBeTruthy()
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('uses a generic translated error and permits retry without displaying query text', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(oidcStatus)))
    const start = vi.spyOn(authApi, 'startOidcLogin').mockImplementation(() => {})
    renderLogin('/login?oidc_error=secret-provider-description')
    expect((await screen.findByRole('alert')).textContent).toContain('SSO sign-in could not be completed')
    expect(screen.queryByText(/secret-provider-description/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Continue with SSO' }))
    expect(start).toHaveBeenCalledWith('https://panwatch.example/api/auth/oidc/login')
  })

  it('recovers if starting the browser redirect fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(oidcStatus)))
    const start = vi.spyOn(authApi, 'startOidcLogin').mockImplementationOnce(() => { throw new Error('navigation blocked') }).mockImplementation(() => {})
    renderLogin()
    fireEvent.click(await screen.findByRole('button', { name: 'Continue with SSO' }))
    expect(await screen.findByText(/SSO sign-in could not be completed/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Continue with SSO' }))
    expect(start).toHaveBeenCalledTimes(2)
  })

  it('prevents repeated redirects, then permits retry after browser Back', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(oidcStatus)))
    const start = vi.spyOn(authApi, 'startOidcLogin').mockImplementation(() => {})
    renderLogin()
    const button = await screen.findByRole('button', { name: 'Continue with SSO' })
    fireEvent.click(button)
    fireEvent.click(button)
    expect(start).toHaveBeenCalledTimes(1)
    act(() => { window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })) })
    fireEvent.click(screen.getByRole('button', { name: 'Continue with SSO' }))
    expect(start).toHaveBeenCalledTimes(2)
  })

  it('does not persist a password result or navigate after the form unmounts', async () => {
    let finish!: (value: Response) => void
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(response(passwordStatus))
      .mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve })))
    const view = renderLogin()
    await userEvent.type(await screen.findByLabelText('Username'), 'owner')
    await userEvent.type(screen.getByLabelText('Password'), 'password')
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    view.unmount()
    await act(async () => { finish(response({ token: 'late-token', expires_at: '2099-01-01' })) })
    expect(localStorage.getItem('token')).toBeNull()
  })

  it('deduplicates password submissions and preserves the successful fallback', async () => {
    let finish!: (value: Response) => void
    const fetch = vi.fn().mockResolvedValueOnce(response({ ...oidcStatus, initialized: true, password_enabled: true }))
      .mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve }))
    vi.stubGlobal('fetch', fetch)
    renderLogin()
    await userEvent.type(await screen.findByLabelText('Username'), 'owner')
    await userEvent.type(screen.getByLabelText('Password'), 'password')
    const button = screen.getByRole('button', { name: 'Sign in' })
    fireEvent.click(button)
    fireEvent.click(button)
    expect(fetch).toHaveBeenCalledTimes(2)
    await act(async () => { finish(response({ token: 'password-token', expires_at: '2099-01-01' })) })
    expect(await screen.findByText('Protected home')).toBeTruthy()
    expect(localStorage.getItem('token')).toBe('password-token')
    expect(fetch.mock.calls[1][1]).toMatchObject({ method: 'POST', body: JSON.stringify({ username: 'owner', password: 'password' }) })
  })
})

describe('protected route verification', () => {
  it('waits for a server-verified cookie session even with a stale local marker', async () => {
    localStorage.setItem('oidc_authenticated', 'true')
    let finish!: (value: Response) => void
    const fetch = vi.fn(() => new Promise<Response>(resolve => { finish = resolve }))
    vi.stubGlobal('fetch', fetch)
    renderGuard()
    expect(screen.getByRole('status')).toBeTruthy()
    expect(screen.queryByText('Protected home')).toBeNull()
    expect(fetch).toHaveBeenCalledTimes(1)
    await act(async () => { finish(response({ ...oidcStatus, authenticated: true, auth_method: 'oidc', csrf_token: 'csrf' })) })
    expect(await screen.findByText('Protected home')).toBeTruthy()
  })

  it('rejects a stale password token when the server says unauthenticated', async () => {
    localStorage.setItem('token', 'stale-token')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(passwordStatus)))
    renderGuard()
    expect(await screen.findByText('Login destination')).toBeTruthy()
    expect(screen.queryByText('Protected home')).toBeNull()
  })

  it('keeps protected content hidden on a status failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    renderGuard()
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeTruthy()
    expect(screen.queryByText('Protected home')).toBeNull()
    expect(screen.queryByText('Login destination')).toBeNull()
  })

  it('ignores a late status response after route unmount', async () => {
    let finish!: (value: Response) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { finish = resolve })))
    const view = renderGuard()
    view.unmount()
    await act(async () => { finish(response(oidcStatus)) })
    expect(screen.queryByText('Login destination')).toBeNull()
  })
})
