import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import AccountMenu from '@/components/AccountMenu'
import { installMatchMediaMock } from '../helpers/match-media-mock'

// jsdom 未实现 matchMedia,组件用它判定 PC 悬停展开
beforeAll(installMatchMediaMock)

function renderMenu(props?: Partial<Parameters<typeof AccountMenu>[0]>) {
  return render(
    <MemoryRouter>
      <AccountMenu mode="light" onSetMode={vi.fn()} onOpenSelfCheck={vi.fn()} {...props} />
    </MemoryRouter>,
  )
}

async function openMenu(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: '账户与设置' }))
  return screen.getByText('主题').closest('div.absolute') as HTMLElement
}

describe('AccountMenu 弹出方向', () => {
  it('side="top" 时菜单向锚点上方弹出(侧边栏底行场景)', async () => {
    const user = userEvent.setup()
    renderMenu({ size: 'sm', side: 'top' })

    const wrapper = await openMenu(user)

    expect(wrapper.className).toContain('bottom-full')
    expect(wrapper.className).toContain('pb-2')
    expect(wrapper.className).not.toContain('top-full')
    expect(wrapper.className).not.toContain('pt-2')
  })

  it('默认向下弹出(顶栏场景,保持既有行为)', async () => {
    const user = userEvent.setup()
    renderMenu({ size: 'sm' })

    const wrapper = await openMenu(user)

    expect(wrapper.className).toContain('top-full')
    expect(wrapper.className).toContain('right-0')
    expect(wrapper.className).not.toContain('bottom-full')
  })
})
