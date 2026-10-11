import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation, Link } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import GettingStartedPage from '@/pages/GettingStarted'
import { GuidedTourProvider, useGuidedTour, resumeStep, tourSteps } from '@/components/onboarding/GuidedTour'
import FirstAnalysis from '@/components/onboarding/FirstAnalysis'
import { onboardingApi, type SetupStatus } from '@panwatch/api/onboarding'
import i18n from '@/i18n'
import { featureTracks, trackSteps } from '@/components/onboarding/tracks'

vi.mock('@panwatch/api/onboarding', () => ({ onboardingApi: { status: vi.fn(), update: vi.fn(), quote: vi.fn(), analyze: vi.fn() } }))
const stock = { id: 1, symbol: '600519', name: '测试标的', market: 'CN' }
const quote = { current_price: 123.4, change_pct: 1.2, source: 'controlled', observed_at: '2026-10-10T02:00:00Z', source_time: '2026-10-09T15:00:00+08:00' }
const model = { id: 5, name: 'QA', model: 'qa-model', is_default: true, verified: true }
let current: SetupStatus
function state(overrides: Partial<SetupStatus> = {}): SetupStatus {
  return { goal: 'ai', active_track: 'main', tracks: featureTracks.map(key => ({ key, step: null, started: false, completed: false, completed_steps: [], total: 3 })), started: !!overrides.tour_step || !!overrides.selected_stock, deferred: false, tour_step: null, selected_stock: null, models: [], channels: [],
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
    <button data-tour="opportunity-filters">实际机会筛选</button><div data-tour="opportunity-signals">候选列表</div><div data-tour="evaluations">验证中心</div>
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
  vi.mocked(onboardingApi.update).mockImplementation(async patch => {
    current = { ...current, ...patch, active_track: patch.track || current.active_track, started: true }
    if (patch.track && patch.track !== 'main') current.tracks = current.tracks.map(item => item.key === patch.track ? { ...item, started: true, step: !item.step || item.step === 'done' || patch.restart ? trackSteps[item.key][0].key : item.step } : item)
    if (patch.complete_step) current.tracks = current.tracks.map(item => {
      if (item.key !== current.active_track) return item
      const steps = trackSteps[item.key]
      const index = steps.findIndex(step => step.key === patch.complete_step)
      const completed_steps = [...new Set([...item.completed_steps, patch.complete_step!])]
      return { ...item, completed_steps, completed: completed_steps.length === steps.length, step: steps[index + 1]?.key || 'done' }
    })
    return current
  })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ x: 100, y: 100, left: 100, top: 100, width: 200, height: 40, right: 300, bottom: 140, toJSON: () => ({}) })
  HTMLElement.prototype.scrollIntoView = vi.fn()
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear() })

describe('interactive page tour', () => {
  it('automatically opens the unified main tour, then navigates to actual controls without automatic model or quote actions', async () => {
    localStorage.setItem('panwatch_onboarding_completed', 'true')
    renderTour()
    const dialog = await screen.findByRole('dialog')
    expect(screen.queryByRole('group', { name: '选择体验目标' })).toBeNull()
    expect(screen.getAllByRole('list', { name: '完整操作流程' }).at(-1)?.children.length).toBe(6)
    await waitFor(() => expect((dialog.querySelector('button:last-child') as HTMLButtonElement).disabled).toBe(false))
    await userEvent.click(screen.getAllByRole('button', { name: '开始操作引导' }).at(-1)!)
    expect(await screen.findByText('先添加一只自选')).toBeTruthy()
    expect(screen.getByTestId('path').textContent).toBe('/portfolio')
    expect(onboardingApi.update).toHaveBeenCalledWith({ track: 'main', tour_step: 'stock', deferred: false })
    expect(onboardingApi.quote).not.toHaveBeenCalled()
    expect(onboardingApi.analyze).not.toHaveBeenCalled()
  })
  it('lets the actual stock control save selection and moves the spotlight to refresh', async () => {
    current = state({ tour_step: 'stock' })
    renderTour('/portfolio')
    await screen.findByText('先添加一只自选')
    await userEvent.click(screen.getByRole('button', { name: '保存真实自选' }))
    expect(onboardingApi.update).toHaveBeenCalledWith({ stock_id: 1, track: 'main', tour_step: 'quote', deferred: false })
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
    await userEvent.click(screen.getAllByRole('button', { name: '恢复引导' }).at(-1)!)
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
    expect(onboardingApi.update).toHaveBeenCalledWith({ skip: 'alert', track: 'main', tour_step: 'notify', deferred: false })
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
  it('uses the saved step in one sequence that always includes quotes and AI', () => {
    expect(tourSteps()).toEqual(['stock', 'quote', 'ai', 'analysis', 'alert', 'notify'])
    expect(resumeStep(state({ tour_step: 'notify' }))).toBe('notify')
    expect(resumeStep(state({ tour_step: 'ai' }))).toBe('ai')
  })
  it.each([
    { tour_step: 'done', completed: true, deferred: true },
    { tour_step: 'done', completed: true, deferred: false },
    { tour_step: 'quote', deferred: false },
    { tour_step: 'analysis', deferred: true },
    { tour_step: 'analysis', active_track: 'paper', deferred: false },
  ] satisfies Partial<SetupStatus>[])('hides the first-analysis entry outside the active main analysis step (%j)', async overrides => {
    current = state({ selected_stock: stock, ...overrides })
    renderTour('/portfolio')
    await act(async () => {})
    expect(screen.queryByRole('button', { name: '打开首次分析' })).toBeNull()
    expect(onboardingApi.analyze).not.toHaveBeenCalled()
  })
  it('keeps a successful analysis readable during a replay, then removes its entry on Next', async () => {
    current = state({ tour_step: 'analysis', selected_stock: stock, quote, models: [model], completed: true,
      analysis: { id: 7, trace_id: 'qa', status: 'success', content: '已保存的首次分析结果', error_code: '', error_message: '', model_label: 'QA', created_at: '2026-10-10T00:00:00Z' } })
    current.steps = current.steps.map(step => step.key === 'analysis' ? { ...step, status: 'complete' } : step)
    renderTour('/portfolio')
    await userEvent.click(await screen.findByRole('button', { name: '打开首次分析' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('已保存的首次分析结果')).toBeTruthy()
    await userEvent.click(await within(dialog).findByRole('button', { name: '下一步' }))
    await screen.findByText('设置一个价格提醒')
    expect(screen.queryByRole('button', { name: '打开首次分析' })).toBeNull()
    expect(onboardingApi.analyze).not.toHaveBeenCalled()
  })
})


describe('guide center', () => {
  it('keeps all optional routes available after main completion, preserving main state', async () => {
    current = state({ started: true, completed: true, completed_count: 4, deferred: true, tour_step: 'done' })
    renderTour()
    await screen.findByRole('heading', { name: '引导中心' })
    expect(screen.getAllByRole('button', { name: '开始探索' })).toHaveLength(5)
    await userEvent.click(screen.getAllByRole('button', { name: '开始探索' })[0])
    await screen.findByText('筛选你关心的机会')
    expect(screen.getByTestId('path').textContent).toBe('/opportunities')
    expect(current.tour_step).toBe('done')
    expect(onboardingApi.analyze).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: '我已了解，继续' }))
    await screen.findByText('阅读信号，再决定是否关注')
    await userEvent.click(screen.getByRole('button', { name: '我已了解，继续' }))
    await screen.findByText('回看建议的后续表现')
    expect(screen.getByTestId('path').textContent).toBe('/evaluations')
    await userEvent.click(screen.getByRole('button', { name: '完成并返回中心' }))
    await screen.findByRole('heading', { name: '引导中心' })
    expect(screen.getAllByRole('button', { name: '再次体验' })).toHaveLength(2)
  })
  it('starts automatically at first sign-in, but does not reopen after a persistent pause', async () => {
    const first = renderTour('/')
    await screen.findByRole('dialog')
    await userEvent.click(screen.getByRole('button', { name: '稍后处理' }))
    await waitFor(() => expect(current.deferred).toBe(true))
    first.unmount()
    renderTour('/')
    await screen.findByTestId('path')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByTestId('path').textContent).toBe('/')
  })
  it('resumes a paused replay from its saved step instead of restarting it', async () => {
    current = state({ started: true, deferred: true, tour_step: 'ai', active_track: 'opportunities' })
    current.tracks = current.tracks.map(item => item.key === 'opportunities' ? {
      ...item, started: true, completed: true, step: 'signals', completed_steps: ['filters', 'signals', 'evaluation'],
    } : item)
    renderTour()
    const card = await screen.findByRole('region', { name: '发现与评估机会' })
    await userEvent.click(within(card).getByRole('button', { name: '恢复引导' }))
    await screen.findByText('阅读信号，再决定是否关注')
    expect(onboardingApi.update).toHaveBeenCalledWith({ track: 'opportunities', restart: false, deferred: false })
    expect(current.tour_step).toBe('ai')
  })
})
