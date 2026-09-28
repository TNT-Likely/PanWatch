import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import AppSidebar from '@/components/layout/AppSidebar'
import { installMatchMediaMock } from '../helpers/match-media-mock'

// 工具行内含 AccountMenu,其悬停判定依赖 matchMedia(jsdom 未实现)
beforeAll(installMatchMediaMock)

describe('AppSidebar 底部工具行', () => {
  it('不显示 GitHub 按钮,日志/自检/折叠按钮保留', () => {
    render(
      <MemoryRouter>
        <AppSidebar
          mode="light"
          onSetMode={vi.fn()}
          onOpenLogs={vi.fn()}
          onOpenSelfCheck={vi.fn()}
        />
      </MemoryRouter>,
    )

    expect(screen.queryByTitle('GitHub 项目')).toBeNull()
    expect(screen.getByTitle('查看日志')).toBeTruthy()
    expect(screen.getByTitle('系统自检')).toBeTruthy()
    expect(screen.getByTitle('收起侧边栏')).toBeTruthy()
  })
})
