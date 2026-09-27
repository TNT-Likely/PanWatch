import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ToastProvider } from '@panwatch/base-ui/components/ui/toast'
import { changeLocale } from '@/i18n'
import LoginPage from '@/pages/Login'

vi.mock('@panwatch/api', () => ({
  authApi: {
    status: vi.fn().mockResolvedValue({ initialized: true }),
    login: vi.fn(),
    setup: vi.fn(),
  },
}))

function renderLogin() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <LoginPage />
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

  it('renders the English experimental login surface', async () => {
    const user = userEvent.setup()
    renderLogin()

    await user.click(await screen.findByRole('button', { name: '切换界面语言' }))
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeTruthy()
    expect(screen.getByLabelText('Username')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeTruthy()
  })
})
