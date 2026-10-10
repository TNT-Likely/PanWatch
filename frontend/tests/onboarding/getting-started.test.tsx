import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import GettingStartedPage from '@/pages/GettingStarted'
import { Onboarding } from '@panwatch/biz-ui/components/onboarding'
import { onboardingApi, type SetupStatus } from '@panwatch/api/onboarding'
import { stocksApi } from '@panwatch/api/stocks'
import { fetchAPI } from '@panwatch/api/client'
import i18n from '@/i18n'

vi.mock('@panwatch/api/onboarding', () => ({ onboardingApi: { status: vi.fn(), update: vi.fn(), quote: vi.fn(), analyze: vi.fn() } }))
vi.mock('@panwatch/api/stocks', () => ({ stocksApi: { list: vi.fn(), create: vi.fn() } }))
vi.mock('@panwatch/api/client', async importOriginal => ({ ...await importOriginal<object>(), fetchAPI: vi.fn() }))

const stock = { id: 1, symbol: '600519', name: '测试标的', market: 'CN' }
function state(overrides: Partial<SetupStatus> = {}): SetupStatus {
  return { goal: 'ai', started: false, deferred: false, selected_stock: null, models: [], channels: [],
    quote: null, analysis: null, completed: false, completed_count: 0, required_count: 4,
    steps: (['stock', 'quote', 'ai', 'analysis', 'alert', 'notify'] as const).map(key => ({ key, status: 'pending', required: !['alert', 'notify'].includes(key) })), ...overrides }
}
const quote = { current_price: 123.4, change_pct: 1.2, source: 'controlled', observed_at: '2026-10-10T02:00:00Z', source_time: '2026-10-09T15:00:00+08:00' }

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(onboardingApi.status).mockResolvedValue(state())
  vi.mocked(stocksApi.list).mockResolvedValue([stock])
  vi.mocked(onboardingApi.update).mockResolvedValue(state())
})
afterEach(() => { cleanup(); localStorage.clear() })
const renderPage = () => render(<MemoryRouter><GettingStartedPage /></MemoryRouter>)

describe('recoverable getting started', () => {
  it('does not treat example symbols or the old browser flag as completed setup', async () => {
    localStorage.setItem('panwatch_onboarding_completed', 'true')
    renderPage()
    expect(await screen.findByText('已完成 0 / 4 个必要步骤')).toBeTruthy()
    expect(onboardingApi.quote).not.toHaveBeenCalled()
    expect(onboardingApi.analyze).not.toHaveBeenCalled()
    expect(fetchAPI).not.toHaveBeenCalled()
  })

  it('stores explicit stock selection and advances to viewing its quote', async () => {
    vi.mocked(onboardingApi.update).mockResolvedValue(state({ selected_stock: stock, completed_count: 1 }))
    renderPage()
    await userEvent.click(await screen.findByRole('combobox', { name: '从自选列表选择' }))
    await userEvent.click(screen.getByRole('option', { name: '测试标的 (600519) · CN' }))
    expect(onboardingApi.update).toHaveBeenCalledWith({ stock_id: 1, deferred: false })
    expect(await screen.findByRole('button', { name: '查看行情' })).toBeTruthy()
    expect(onboardingApi.quote).not.toHaveBeenCalled()
  })

  it('shows the quotes goal as complete without requiring AI or notifications', async () => {
    const initial = state({ goal: 'quotes', selected_stock: stock, completed_count: 1, required_count: 2 })
    vi.mocked(onboardingApi.status).mockResolvedValue(initial)
    vi.mocked(onboardingApi.quote).mockResolvedValue({ ...initial, quote, completed: true, completed_count: 2 })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /^看到第一条行情/ }))
    await userEvent.click(screen.getByRole('button', { name: '查看行情' }))
    expect(await screen.findByText('你已完成所选目标')).toBeTruthy()
    expect(screen.getByText('数据源：controlled')).toBeTruthy()
    expect(onboardingApi.analyze).not.toHaveBeenCalled()
  })

  it('does not mark a failed quote request as completed and offers a retry', async () => {
    vi.mocked(onboardingApi.status).mockResolvedValue(state({ selected_stock: stock, completed_count: 1 }))
    vi.mocked(onboardingApi.quote).mockRejectedValue(new Error('行情暂不可用'))
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /^看到第一条行情/ }))
    await userEvent.click(screen.getByRole('button', { name: '查看行情' }))
    expect(await screen.findByText('行情暂不可用')).toBeTruthy()
    expect(screen.queryByText('你已完成所选目标')).toBeNull()
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy()
  })

  it('requires a deliberate model test and does not launch analysis automatically', async () => {
    const initial = state({ selected_stock: stock, quote, models: [{ id: 5, name: 'QA', model: 'qa-model', is_default: true, verified: false }] })
    vi.mocked(onboardingApi.status).mockResolvedValue(initial)
    vi.mocked(fetchAPI).mockResolvedValue({ ok: true })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /^连接并测试 AI/ }))
    expect(fetchAPI).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: '测试默认模型' }))
    await waitFor(() => expect(fetchAPI).toHaveBeenCalledWith('/providers/models/5/test', { method: 'POST', timeoutMs: 60000 }))
    expect(onboardingApi.analyze).not.toHaveBeenCalled()
  })

  it('blocks repeat clicks while first analysis is being queued and restores a running task', async () => {
    const initial = state({ selected_stock: stock, quote, models: [{ id: 5, name: 'QA', model: 'qa-model', is_default: true, verified: true }] })
    vi.mocked(onboardingApi.status).mockResolvedValue(initial)
    let resolve!: (value: SetupStatus) => void
    vi.mocked(onboardingApi.analyze).mockReturnValue(new Promise(yes => { resolve = yes }))
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /^完成首次分析/ }))
    const run = screen.getByRole('button', { name: '生成首次分析' })
    await userEvent.dblClick(run)
    expect(onboardingApi.analyze).toHaveBeenCalledTimes(1)
    await act(async () => resolve({ ...initial, analysis: { id: 7, trace_id: 'qa', status: 'running', content: '', error_code: '', error_message: '', model_label: 'QA', created_at: '2026-10-10T00:00:00Z' } }))
    expect(await screen.findByText(/你可以离开页面/)).toBeTruthy()
    expect((screen.getByRole('button', { name: '生成首次分析' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('retains a failed analysis after reload and displays a useful error', async () => {
    vi.mocked(onboardingApi.status).mockResolvedValue(state({ selected_stock: stock, quote,
      analysis: { id: 7, trace_id: 'qa', status: 'failed', content: '', error_code: 'onboarding_analysis_interrupted', error_message: '服务重启中断了分析，请重新生成。', model_label: 'QA', created_at: '2026-10-10T00:00:00Z' } }))
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /^完成首次分析/ }))
    expect(await screen.findByText('服务重启中断了分析，请重新生成。')).toBeTruthy()
    expect(screen.queryByText('你已完成所选目标')).toBeNull()
  })

  it('saves optional skips without pretending the required goal is finished', async () => {
    vi.mocked(onboardingApi.status).mockResolvedValue(state({ selected_stock: stock }))
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /^接收外部通知/ }))
    await userEvent.click(screen.getAllByRole('button', { name: '稍后处理' }).at(-1)!)
    expect(onboardingApi.update).toHaveBeenCalledWith({ skip: 'notify' })
    expect(screen.queryByText('你已完成所选目标')).toBeNull()
  })

  it('offers a persistent continuation link after deferring the dashboard card', async () => {
    vi.mocked(onboardingApi.status).mockResolvedValue(state({ deferred: true }))
    render(<MemoryRouter><Onboarding /></MemoryRouter>)
    expect((await screen.findByRole('link', { name: '恢复引导' })).getAttribute('href')).toBe('/getting-started')
  })

  it('renders the entire guide in English and refreshes on returning to the window', async () => {
    await i18n.changeLanguage('en-US')
    renderPage()
    expect(await screen.findByRole('heading', { name: 'Getting started' })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Start with quotes/ })).toBeTruthy()
    const count = vi.mocked(onboardingApi.status).mock.calls.length
    fireEvent(window, new Event('focus'))
    await waitFor(() => expect(vi.mocked(onboardingApi.status).mock.calls.length).toBe(count + 1))
  })
})
