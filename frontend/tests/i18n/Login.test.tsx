import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ToastProvider } from '@panwatch/base-ui/components/ui/toast'
import { authApi } from '@panwatch/api'
import { changeLocale } from '@/i18n'
import LoginPage from '@/pages/Login'

vi.mock('@panwatch/api', () => ({
  authApi: {
    status: vi.fn().mockResolvedValue({ initialized: true }),
    login: vi.fn(),
    setup: vi.fn(),
  },
}))

function Destination() {
  const { pathname, search, hash } = useLocation()
  return <output aria-label="location">{pathname + search + hash}</output>
}

function renderLogin(state?: object) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: '/login', state }]}>
      <ToastProvider>
        <LoginPage />
        <Destination />
      </ToastProvider>
    </MemoryRouter>,
  )
}

describe('LoginPage translations', () => {
  beforeEach(async () => {
    await changeLocale('zh-CN')
  })

  it('renders the default Chinese login surface', async () => {
    renderLogin()

    expect(await screen.findByRole('heading', { name: '登录' })).toBeTruthy()
    expect(screen.getByLabelText('用户名')).toBeTruthy()
    expect(screen.getByRole('button', { name: '登录' })).toBeTruthy()
  })

  it('renders the English login surface', async () => {
    const user = userEvent.setup()
    renderLogin()

    await user.click(await screen.findByRole('button', { name: '切换界面语言' }))
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeTruthy()
    expect(screen.getByLabelText('Username')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeTruthy()
  })

  it('returns to the device settings section after a successful sign-in', async () => {
    vi.mocked(authApi.login).mockResolvedValueOnce({ token: 'fixture', expires_at: '2099-01-01T00:00:00Z' })
    const user = userEvent.setup()
    renderLogin({ from: { pathname: '/settings', hash: '#sec-mobile' } })

    await user.type(await screen.findByLabelText('用户名'), 'qa-user')
    await user.type(screen.getByLabelText('密码'), 'fixture-password')
    await user.click(screen.getByRole('button', { name: '登录' }))
    expect(await screen.findByText('/settings#sec-mobile')).toBeTruthy()
  })
})
