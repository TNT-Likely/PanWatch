import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { qlibApi } from '@panwatch/api'
import QlibResearch from '@/pages/QlibResearch'
import { ConfirmProvider } from '@panwatch/base-ui/components/ui/confirm-dialog'
import { ToastProvider } from '@panwatch/base-ui/components/ui/toast'
import i18n from '@/i18n'

vi.mock('@panwatch/api', async (original) => ({
  ...await original<typeof import('@panwatch/api')>(),
  qlibApi: Object.fromEntries([
    'health', 'datasets', 'jobs', 'experiments', 'governance', 'opportunities',
    'templates', 'selections', 'executions', 'basket', 'train', 'decision',
  ].map(name => [name, vi.fn()])),
}))
const settings = { enabled: false, dataset_id: null, daily_hour: 18, retrain_every_days: 30 }
const view = (path = '/research?tab=opportunities') => render(<MemoryRouter initialEntries={[path]}><ConfirmProvider labels={{ title: '确认', cancel: '取消', confirm: '确认' }}><ToastProvider><QlibResearch /></ToastProvider></ConfirmProvider></MemoryRouter>)
beforeEach(async () => {
  vi.resetAllMocks()
  await i18n.changeLanguage('zh-CN')
  for (const method of ['datasets', 'jobs', 'experiments', 'templates', 'selections', 'basket'] as const) {
    vi.mocked(qlibApi[method]).mockResolvedValue({ items: [] })
  }
  vi.mocked(qlibApi.health).mockResolvedValue({ available: true, error_code: '', runtime: { pyqlib: '0.9.7' },
    monitor: { champion_id: null, prediction_date: null, fresh: false, coverage: {}, topk_change_fraction: null, recent_failures: [], schedule: settings } })
  vi.mocked(qlibApi.governance).mockResolvedValue({ champion_id: null, revision: 0, settings, models: [], audit: [] })
  vi.mocked(qlibApi.opportunities).mockResolvedValue({ items: [], run: null, status: 'no_champion', mode: 'research' })
  vi.mocked(qlibApi.executions).mockResolvedValue({ items: [], positions: [], decisions: [] })
})
afterEach(cleanup)

describe('Qlib research workbench', () => {
  it('defaults the advanced route to research and links back to everyday opportunities', async () => {
    view('/research')
    expect(await screen.findByRole('tabpanel', { name: '研究与回测' })).toBeTruthy()
    expect(screen.getByRole('link', { name: '← 返回机会页' }).getAttribute('href')).toBe('/opportunities')
    expect(qlibApi.train).not.toHaveBeenCalled()
  })
  it('never creates jobs or decisions merely by opening the page', async () => {
    view()
    expect(await screen.findByText('暂无已审批模型')).toBeTruthy()
    expect(qlibApi.train).not.toHaveBeenCalled()
    expect(qlibApi.decision).not.toHaveBeenCalled()
    expect((screen.getByRole('button', { name: '查看模拟计划' }) as HTMLButtonElement).disabled).toBe(true)
  })
  it('shows worker failure without reviving old scores', async () => {
    vi.mocked(qlibApi.health).mockResolvedValue({ available: false, error_code: 'qlib_worker_missing', runtime: null,
      monitor: { champion_id: null, prediction_date: null, fresh: false, coverage: {}, topk_change_fraction: null, recent_failures: [], schedule: settings } })
    view()
    expect(await screen.findByText(/Qlib worker 不可用/)).toBeTruthy()
    expect(screen.queryByText('评分90+')).toBeNull()
    await userEvent.click(screen.getByRole('tab', { name: '研究与回测' }))
    expect((screen.getByRole('button', { name: '运行训练与回测' }) as HTMLButtonElement).disabled).toBe(true)
  })
  it('supports keyboard tab navigation and names the selected panel', async () => {
    view()
    await screen.findByText('暂无已审批模型')
    screen.getByRole('tab', { name: '模型机会' }).focus()
    await userEvent.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { name: '研究与回测' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('tabpanel', { name: '研究与回测' })).toBeTruthy()
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: '研究与回测' }))
  })
  it('keeps API failure explicit instead of presenting a fabricated selection', async () => {
    vi.mocked(qlibApi.opportunities).mockRejectedValue(new Error('controlled connection failure'))
    view()
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText('controlled connection failure')).toBeTruthy()
    expect(qlibApi.decision).not.toHaveBeenCalled()
  })
})
