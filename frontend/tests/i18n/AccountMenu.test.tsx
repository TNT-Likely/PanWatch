import { render, screen, within } from '@testing-library/react'
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
    const setMode = vi.fn()
    render(
      <MemoryRouter>
        <AccountMenu
          navItems={[]}
          mode="system"
          onSetMode={setMode}
          onOpenSelfCheck={vi.fn()}
        />
      </MemoryRouter>,
    )

    await user.click(screen.getByRole('button', { name: '账户与设置' }))
    const appearance = within(screen.getByRole('group', { name: '主题' }))
    expect(appearance.getAllByRole('button')).toHaveLength(3)
    expect(appearance.getByRole('button', { name: '跟随系统' }).getAttribute('aria-pressed')).toBe('true')
    await user.click(appearance.getByRole('button', { name: '暗色' }))
    expect(setMode).toHaveBeenCalledWith('dark')

    const language = within(screen.getByRole('group', { name: '界面语言' }))
    await user.click(language.getByRole('button', { name: 'English' }))

    expect(document.documentElement.lang).toBe('en-US')
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en-US')
    expect(screen.getByRole('button', { name: 'English' }).getAttribute('aria-pressed')).toBe('true')
    await user.keyboard('{Escape}')
    const opener = screen.getByRole('button', { name: 'Account and settings' })
    expect(opener.getAttribute('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(opener)

    await user.click(opener)
    expect(screen.queryByRole('button', { name: 'Phones & devices' })).toBeNull()
  })
})
