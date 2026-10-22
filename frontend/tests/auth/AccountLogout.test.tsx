import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AccountMenu from '@/components/AccountMenu'
import { ToastProvider } from '@panwatch/base-ui/components/ui/toast'
import { changeLocale } from '@/i18n'
import { clearAuthSession, setAuthStatus } from '@panwatch/api/auth-session'
import { isAuthenticated } from '@panwatch/api/client'

vi.mock('@/hooks/use-avatar', () => ({ useAvatar: () => null }))
vi.mock('@/i18n/interface-language', () => ({ useInterfaceLanguage: () => 'en-US' }))

beforeEach(async () => {
  localStorage.clear()
  window.history.replaceState(null, '', '/login')
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: vi.fn().mockReturnValue({ matches: false }) })
  setAuthStatus({ initialized: true, oidc_enabled: true, password_enabled: false, authenticated: true,
    auth_method: 'oidc', csrf_token: 'csrf', oidc_login_url: 'https://example.com/api/auth/oidc/login' })
  await changeLocale('en-US')
})
afterEach(() => {
  vi.unstubAllGlobals()
  clearAuthSession()
})
function renderMenu() {
  return render(<MemoryRouter><ToastProvider><AccountMenu navItems={[]} mode="system" onSetMode={vi.fn()} onOpenSelfCheck={vi.fn()} /></ToastProvider></MemoryRouter>)
}

describe('account logout feedback', () => {
  it('keeps the authenticated UI on failure and lets the owner retry', async () => {
    const fetch = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(new Response(JSON.stringify({ code: 0, data: { logged_out: true } })))
    vi.stubGlobal('fetch', fetch)
    renderMenu()
    fireEvent.click(screen.getByRole('button', { name: 'Account and settings' }))
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(await screen.findByText('Could not sign out. Your session may still be active. Please retry.')).toBeTruthy()
    expect(isAuthenticated()).toBe(true)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Sign out' })) })
    expect(isAuthenticated()).toBe(false)
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('disables repeated sign-out clicks while confirmation is pending', async () => {
    let finish!: (value: Response) => void
    const fetch = vi.fn(() => new Promise<Response>(resolve => { finish = resolve }))
    vi.stubGlobal('fetch', fetch)
    renderMenu()
    fireEvent.click(screen.getByRole('button', { name: 'Account and settings' }))
    const signOut = screen.getByRole('button', { name: 'Sign out' }) as HTMLButtonElement
    fireEvent.click(signOut)
    fireEvent.click(signOut)
    expect(signOut.disabled).toBe(true)
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(isAuthenticated()).toBe(true)
    await act(async () => { finish(new Response(JSON.stringify({ code: 0, data: { logged_out: true } }))) })
    expect(isAuthenticated()).toBe(false)
  })
})
