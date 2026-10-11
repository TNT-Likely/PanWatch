import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation, Link } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import GettingStartedPage from '@/pages/GettingStarted'
import { GuidedTourProvider, useGuidedTour, resumeStep, tourSteps } from '@/components/onboarding/GuidedTour'
import FirstAnalysis from '@/components/onboarding/FirstAnalysis'
import { Onboarding } from '@panwatch/biz-ui/components/onboarding'
import { onboardingApi, type SetupStatus } from '@panwatch/api/onboarding'
import i18n from '@/i18n'

vi.mock('@panwatch/api/onboarding', () => ({ onboardingApi: { status: vi.fn(), update: vi.fn(), quote: vi.fn(), analyze: vi.fn() } }))
const stock = { id: 1, symbol: '600519', name: '测试标的', market: 'CN' }
const quote = { current_price: 123.4, change_pct: 1.2, source: 'controlled', observed_at: '2026-10-10T02:00:00Z', source_time: '2026-10-09T15:00:00+08:00' }
const model = { id: 5, name: 'QA', model: 'qa-model', is_default: true, verified: true }
let current: SetupStatus
function state(overrides: Partial<SetupStatus> = {}): SetupStatus {
  return { goal: 'ai', started: false, deferred: false, tour_step: null, selected_stock: null, models: [], channels: [],
    quote: null, analysis: null, completed: false, completed_count: 0, required_count: 4,
    steps: (['stock', 'quote', 'ai', 'analysis', 'alert', 'notify'] as const).map(key => ({ key, status: 'pending', required: !['alert', 'notify'].includes(key) })), ...overrides }
}
function Controls() {
  const tour = useGuidedTour()
  const location = useLocation()
  return <><output data-testid="path">{location.pathname}</output>
    <button data-tour="add-stock" onClick={() => void tour?.selectStock(1)}>保存真实自选</button>
    <button data-tour="refresh-quotes" onClick={() => void tour?.observeQuote()}>实际刷新</button>
    <div data-tour="watchlist"><button onClick={() => void tour?.selectStock(1)}>选择已有股票</button></div>
    <div data-tour-stock="1">股票报价</div>
    <button data-tour="add-alert">实际提醒入口</button>
    <section id="sec-ai">真实模型设置</section><section id="sec-notify">真实渠道设置</section>
    <Link to="/history">离开页面</Link><FirstAnalysis />
  </>
}
const renderTour = (route = '/getting-started') => render(<MemoryRouter initialEntries={[route]}><GuidedTourProvider><Routes>
  <Route path="/getting-started" element={<GettingStartedPage />} /><Route path="*" element={<Controls />} />
</Routes></GuidedTourProvider></MemoryRouter>)

beforeEach(async () => {
  vi.resetAllMocks()
  await i18n.changeLanguage('zh-CN')
  current = state()
  vi.mocked(onboardingApi.status).mockImplementation(async () => current)
  vi.mocked(onboardingApi.update).mockImplementation(async patch => { current = { ...current, ...patch }; return current })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ x: 100, y: 100, left: 100, top: 100, width: 200, height: 40, right: 300, bottom: 140, toJSON: () => ({}) })
  HTMLElement.prototype.scrollIntoView = vi.fn()
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear() })

describe('interactive page tour', () => {
  it('opens a goal dialog, then navigates to actual controls without automatic model or quote actions', async () => {
    localStorage.setItem('panwatch_onboarding_completed', 'true')
    renderTour()
    const dialog = await screen.findByRole('dialog')
    await waitFor(() => expect((dialog.querySelector('button:last-child') as HTMLButtonElement).disabled).toBe(false))
    await userEvent.click(screen.getAllByRole('button', { name: '开始操作引导' }).at(-1)!)
    expect(await screen.findByText('先添加一只自选')).toBeTruthy()
    expect(screen.getByTestId('path').textContent).toBe('/portfolio')
    expect(onboardingApi.update).toHaveBeenCalledWith({ goal: 'ai', tour_step: 'stock', deferred: false })
    expect(onboardingApi.quote).not.toHaveBeenCalled()
    expect(onboardingApi.analyze).not.toHaveBeenCalled()
  })
  it('lets the actual stock control save selection and moves the spotlight to refresh', async () => {
    current = state({ tour_step: 'stock' })
    renderTour('/portfolio')
    await screen.findByText('先添加一只自选')
    await userEvent.click(screen.getByRole('button', { name: '保存真实自选' }))
    expect(onboardingApi.update).toHaveBeenCalledWith({ stock_id: 1, tour_step: 'quote', deferred: false })
    expect(await screen.findByText('亲手刷新一次行情')).toBeTruthy()
    expect(onboardingApi.quote).not.toHaveBeenCalled()
  })
  it('shows a retryable quote failure and does not enable Next or start analysis', async () => {
    current = state({ tour_step: 'quote', selected_stock: stock })
    vi.mocked(onboardingApi.quote).mockRejectedValue(new Error('行情暂不可用'))
    renderTour('/portfolio')
    await screen.findByText('亲手刷新一次行情')
    await userEvent.click(screen.getByRole('button', { name: '实际刷新' }))
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', '行情暂不可用')
    expect((screen.getByRole('button', { name: '下一步' }) as HTMLButtonElement).disabled).toBe(true)
    expect(onboardingApi.analyze).not.toHaveBeenCalled()
  })
  it('requires successful facts before Next navigates to the settings page', async () => {
    current = state({ tour_step: 'quote', selected_stock: stock })
    vi.mocked(onboardingApi.quote).mockImplementation(async () => {
      current = { ...current, quote, steps: current.steps.map(step => step.key === 'quote' ? { ...step, status: 'complete' } : step) }
      return current
    })
    renderTour('/portfolio')
    await screen.findByText('亲手刷新一次行情')
    await userEvent.click(screen.getByRole('button', { name: '实际刷新' }))
    await screen.findByText('查看这只股票的行情')
    await userEvent.click(screen.getByRole('button', { name: '下一步' }))
    expect(await screen.findByText('连接并测试默认模型')).toBeTruthy()
    expect(screen.getByTestId('path').textContent).toBe('/settings')
  })
  it('pauses on Escape without losing the saved step and resumes after opening the launcher', async () => {
    current = state({ tour_step: 'quote', selected_stock: stock })
    const first = renderTour('/portfolio')
    await screen.findByText('亲手刷新一次行情')
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(onboardingApi.update).toHaveBeenCalledWith({ deferred: true }))
    expect(current.tour_step).toBe('quote')
    first.unmount()
    renderTour()
    await userEvent.click(await screen.findByRole('button', { name: '恢复引导' }))
    expect(await screen.findByText('亲手刷新一次行情')).toBeTruthy()
    expect(current.deferred).toBe(false)
  })
  it('retains progress when leaving a route and returns without repeating a user action', async () => {
    current = state({ tour_step: 'stock' })
    renderTour('/portfolio')
    await screen.findByText('先添加一只自选')
    await userEvent.click(screen.getByRole('link', { name: '离开页面' }))
    await screen.findByText('你已离开当前引导页面。进度已保存。')
    await userEvent.click(screen.getByRole('button', { name: '回到当前步骤' }))
    expect(await screen.findByText('先添加一只自选')).toBeTruthy()
    expect(onboardingApi.update).not.toHaveBeenCalled()
  })
  it('skips optional steps with persisted state, without claiming a required action succeeded', async () => {
    current = state({ tour_step: 'alert' })
    renderTour('/alerts')
    await screen.findByText('设置一个价格提醒')
    await userEvent.click(screen.getByRole('button', { name: '跳过此步' }))
    expect(onboardingApi.update).toHaveBeenCalledWith({ skip: 'alert', tour_step: 'notify', deferred: false })
    expect(await screen.findByText('测试通知渠道')).toBeTruthy()
    expect(current.completed).toBe(false)
  })
  it('deduplicates analysis generation, restores a running task, and preserves errors', async () => {
    current = state({ tour_step: 'analysis', selected_stock: stock, quote, models: [model] })
    let resolve!: (value: SetupStatus) => void
    vi.mocked(onboardingApi.analyze).mockReturnValue(new Promise(yes => { resolve = yes }))
    renderTour('/portfolio')
    await userEvent.click(await screen.findByRole('button', { name: '打开首次分析' }))
    const run = await screen.findByRole('button', { name: '生成首次分析' })
    await userEvent.dblClick(run)
    expect(onboardingApi.analyze).toHaveBeenCalledTimes(1)
    await act(async () => resolve({ ...current, analysis: { id: 7, trace_id: 'qa', status: 'running', content: '', error_code: '', error_message: '', model_label: 'QA', created_at: '2026-10-10T00:00:00Z' } }))
    expect((screen.getByRole('button', { name: '生成首次分析' }) as HTMLButtonElement).disabled).toBe(true)
    expect(await screen.findByText(/你可以离开页面/)).toBeTruthy()
  })
  it('uses the saved step and omits AI steps from the quotes goal', () => {
    expect(tourSteps('quotes')).toEqual(['stock', 'quote', 'alert', 'notify'])
    expect(resumeStep(state({ goal: 'quotes', tour_step: 'notify' }))).toBe('notify')
    expect(resumeStep(state({ goal: 'quotes', tour_step: 'ai' }))).toBe('stock')
  })
  it('continues to offer the launcher after a deferred dashboard banner', async () => {
    current = state({ deferred: true, tour_step: 'quote' })
    render(<MemoryRouter><Onboarding /></MemoryRouter>)
    expect(await screen.findByRole('link', { name: '恢复引导' })).toHaveProperty('pathname', '/getting-started')
  })
})
