import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AccountMenu from '@/components/AccountMenu'
import { changeLocale, LOCALE_STORAGE_KEY } from '@/i18n'

vi.mock('@/hooks/use-avatar', () => ({
  useAvatar: () => null,
}))

describe('AccountMenu language switcher', () => {
  beforeEach(async () => {
    window.localStorage.clear()
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: vi.fn().mockReturnValue({ matches: false }),
    })
    await changeLocale('zh-CN')
  })

  it('changes and persists the locale from the avatar menu', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <AccountMenu
          navItems={[]}
          mode="system"
          onSetMode={vi.fn()}
          onOpenSelfCheck={vi.fn()}
        />
      </MemoryRouter>,
    )

    await user.click(screen.getByRole('button', { name: '账户与设置' }))
    await user.click(screen.getByRole('button', { name: 'English' }))

    expect(document.documentElement.lang).toBe('en-US')
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en-US')
    expect(screen.getByRole('button', { name: 'Account and settings' })).toBeTruthy()
  })
})
