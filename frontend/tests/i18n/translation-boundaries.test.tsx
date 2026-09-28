import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ToastProvider } from '@panwatch/base-ui/components/ui/toast'
import PriceAlertFormDialog from '@panwatch/biz-ui/components/price-alert-form-dialog'
import AgentsPage from '@/pages/Agents'

vi.mock('@panwatch/api', () => ({
  fetchAPI: vi.fn(async (path: string) => {
    if (path === '/agents/health') {
      return {
        timezone: 'Asia/Shanghai',
        summary: { next_24h_count: 0, recent_failed_count: 0 },
        agents: [],
      }
    }
    return []
  }),
}))

describe('scoped translation boundaries', () => {
  it('renders the Agent page without exposing translation keys', async () => {
    render(
      <ToastProvider>
        <AgentsPage />
      </ToastProvider>,
    )

    expect(await screen.findByRole('heading', { name: 'Agent' })).toBeTruthy()
    expect(await screen.findByText('调度健康')).toBeTruthy()
    expect(screen.queryByText('pageTitle')).toBeNull()
    expect(screen.queryByText('health')).toBeNull()
  })

  it('renders localized fields in the shared price-alert editor', () => {
    render(
      <PriceAlertFormDialog
        open
        onOpenChange={vi.fn()}
        title="新建提醒规则"
        description="提醒规则说明"
        stocks={[{ id: 1, symbol: '600519', name: '贵州茅台', market: 'CN' }]}
        onSubmit={vi.fn()}
      />,
    )

    expect(screen.getByText('股票')).toBeTruthy()
    expect(screen.getByText('规则名称')).toBeTruthy()
    expect(screen.getByText('条件列表')).toBeTruthy()
    expect(screen.queryByText('form.stock')).toBeNull()
    expect(screen.queryByText('conditions.price')).toBeNull()
  })
})
