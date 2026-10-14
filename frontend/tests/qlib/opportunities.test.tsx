import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  fetchAPI,
  qlibApi,
  stocksApi,
  defaultQlibConfig,
  type QlibPrediction,
  type QlibRun,
} from '@panwatch/api'
import Opportunities from '@/pages/Opportunities'
import { ConfirmProvider } from '@panwatch/base-ui/components/ui/confirm-dialog'
import { ToastProvider } from '@panwatch/base-ui/components/ui/toast'
import i18n from '@/i18n'

const { insight } = vi.hoisted(() => ({ insight: vi.fn() }))
vi.mock('@/components/qlib-data-pipeline', () => ({
  default: () => <div data-testid="data-pipeline" />,
}))
vi.mock('@panwatch/biz-ui/components/stock-insight-modal', () => ({
  default: (props: { symbol: string; researchContext: React.ReactNode }) => {
    insight(props)
    return (
      <div role="dialog" aria-label="Shared stock details">
        {props.symbol}
        {props.researchContext}
      </div>
    )
  },
}))
vi.mock('@panwatch/api', async (original) => ({
  ...(await original<typeof import('@panwatch/api')>()),
  fetchAPI: vi.fn(),
  stocksApi: { list: vi.fn(), create: vi.fn(), remove: vi.fn() },
  qlibApi: Object.fromEntries(
    [
      'health',
      'opportunities',
      'selections',
      'basket',
      'setBasket',
      'train',
      'decision',
      'predict',
    ].map((name) => [name, vi.fn()]),
  ),
}))
const predictions: QlibPrediction[] = Array.from({ length: 8 }, (_, i) => ({
  symbol: `SH60000${i}`,
  market: 'CN',
  rank: i + 1,
  rank_percentile: 100 - (i * 100) / 7,
  raw_score: 0.9 - i / 2,
  rank_change: null,
  tradable: true,
  reference_close: 12.34,
  factor_inputs: { KMID: 0.123 },
  explanation_kind: 'processed_inputs',
}))
const run: QlibRun = {
  id: 'a'.repeat(32),
  model_id: 'b'.repeat(32),
  dataset_id: 'c'.repeat(32),
  experiment_id: 'd'.repeat(32),
  asof_date: '2026-10-13',
  horizon: 5,
  data_kind: 'synthetic',
  config: { ...defaultQlibConfig, topk: 3 },
}
const view = () =>
  render(
    <MemoryRouter>
      <ConfirmProvider
        labels={{ title: '确认', cancel: '取消', confirm: '确认' }}
      >
        <ToastProvider>
          <Opportunities />
        </ToastProvider>
      </ConfirmProvider>
    </MemoryRouter>,
  )
const choose = async (label: string, option: string) => {
  await userEvent.click(screen.getByRole('combobox', { name: label }))
  await userEvent.click(screen.getByRole('option', { name: option }))
}
const market = () => {
  vi.mocked(qlibApi.opportunities).mockResolvedValue({
    items: predictions,
    run: { ...run, data_kind: 'market' },
    status: 'ready',
    mode: 'research',
  })
  vi.mocked(qlibApi.selections).mockResolvedValue({ items: [] })
}
beforeEach(async () => {
  vi.resetAllMocks()
  localStorage.clear()
  await i18n.changeLanguage('zh-CN')
  vi.mocked(qlibApi.health).mockResolvedValue({
    available: true,
    error_code: '',
    runtime: {},
    monitor: {
      fresh: true,
      prediction_date: run.asof_date,
      coverage: {},
      topk_change_fraction: null,
      recent_failures: [],
    },
  })
  vi.mocked(qlibApi.opportunities).mockResolvedValue({
    items: predictions,
    run,
    status: 'ready',
    mode: 'research',
  })
  vi.mocked(qlibApi.selections).mockResolvedValue({
    items: [{ ...run, predictions }],
  })
  vi.mocked(qlibApi.basket).mockResolvedValue({ items: [] })
  vi.mocked(qlibApi.setBasket).mockResolvedValue({
    items: [{ symbol: 'SH600000', run_id: run.id }],
  })
  vi.mocked(stocksApi.list).mockResolvedValue([
    { id: 1, symbol: '600000', name: '测试证券', market: 'CN' },
  ])
  vi.mocked(fetchAPI).mockResolvedValue([{ stock_id: 1, quantity: 100 }])
})
afterEach(cleanup)

describe('everyday opportunities page', () => {
  it('recovers malformed persisted state without crashing the page', async () => {
    localStorage.setItem('panwatch_qlib_opportunity_filters_v1', 'null')
    view()
    await screen.findByRole('button', { name: '查看 样例股票 600000 详情' })
    expect(
      screen.getAllByRole('button', { name: /^查看 样例股票.*详情$/ }),
    ).toHaveLength(8)
  })
  it('requires confirmation to remove a real unheld watch and preserves it on cancel', async () => {
    market()
    vi.mocked(fetchAPI).mockResolvedValue([])
    view()
    await screen.findByRole('button', { name: '查看 测试证券 详情' })
    await userEvent.click(
      screen.getByRole('button', { name: '取消关注 600000' }),
    )
    await userEvent.click(
      screen.getByRole('button', { name: '取消', exact: true }),
    )
    expect(stocksApi.remove).not.toHaveBeenCalled()
    await userEvent.click(
      screen.getByRole('button', { name: '取消关注 600000' }),
    )
    await userEvent.click(
      screen.getByRole('button', { name: '确认', exact: true }),
    )
    await waitFor(() => expect(stocksApi.remove).toHaveBeenCalledWith(1))
    expect(qlibApi.decision).not.toHaveBeenCalled()
  })
  it('opens directly on familiar overview, filters and cards without research controls or writes', async () => {
    view()
    await screen.findByRole('button', { name: '查看 样例股票 600000 详情' })
    expect(
      screen.getByRole('heading', { name: '机会页', level: 1 }),
    ).toBeTruthy()
    expect(
      screen.getByRole('link', { name: '量化研究' }).getAttribute('href'),
    ).toBe('/research')
    expect(
      screen.getByRole('link', { name: '查看模型组合' }).getAttribute('href'),
    ).toBe('/research?tab=paper')
    expect(screen.queryByRole('tablist')).toBeNull()
    expect(screen.queryByText('原始模型分')).toBeNull()
    expect(
      screen.queryByRole('button', { name: '创建下一开盘模拟决策' }),
    ).toBeNull()
    expect(qlibApi.train).not.toHaveBeenCalled()
    expect(qlibApi.decision).not.toHaveBeenCalled()
    expect(qlibApi.predict).not.toHaveBeenCalled()
    expect(stocksApi.list).not.toHaveBeenCalled()
    expect(fetchAPI).not.toHaveBeenCalled()
  })
  it('stages, applies, clears and persists filters using full-pool ranks', async () => {
    view()
    await screen.findByRole('button', { name: '查看 样例股票 600000 详情' })
    await choose('排名范围', '排名前 10%')
    expect(
      screen.getAllByRole('button', { name: /^查看 样例股票.*详情$/ }),
    ).toHaveLength(8)
    await userEvent.click(screen.getByRole('button', { name: '应用筛选' }))
    expect(
      screen.getAllByRole('button', { name: /^查看 样例股票.*详情$/ }),
    ).toHaveLength(1)
    expect(
      JSON.parse(localStorage.getItem('panwatch_qlib_opportunity_filters_v1')!)
        .top,
    ).toBe('10')
    await userEvent.click(screen.getByRole('button', { name: '清空筛选' }))
    expect(
      screen.getAllByRole('button', { name: /^查看 样例股票.*详情$/ }),
    ).toHaveLength(8)
  })
  it('searches symbols and shows an honest empty result', async () => {
    view()
    await screen.findByRole('button', { name: '查看 样例股票 600000 详情' })
    await userEvent.type(
      screen.getByRole('textbox', { name: '搜索名称或代码' }),
      '600007',
    )
    await userEvent.click(screen.getByRole('button', { name: '应用筛选' }))
    expect(
      screen.getAllByRole('button', { name: /^查看 样例股票.*详情$/ }),
    ).toHaveLength(1)
    await userEvent.clear(
      screen.getByRole('textbox', { name: '搜索名称或代码' }),
    )
    await userEvent.type(
      screen.getByRole('textbox', { name: '搜索名称或代码' }),
      '不存在',
    )
    await userEvent.click(screen.getByRole('button', { name: '应用筛选' }))
    expect(screen.getByText('暂无符合条件的股票，试试清空筛选。')).toBeTruthy()
  })
  it('opens synthetic details without real quotes or company identities; Escape restores focus', async () => {
    view()
    const button = await screen.findByRole('button', {
      name: '查看 样例股票 600000 详情',
    })
    await userEvent.click(button)
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(insight).not.toHaveBeenCalled()
    await userEvent.click(screen.getByText('模型输入与版本'))
    expect(screen.getByText(/0.90000/)).toBeTruthy()
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(document.activeElement).toBe(button)
  })
  it('follows a demo only in the research basket', async () => {
    view()
    await screen.findByRole('button', { name: '关注 600000' })
    await userEvent.click(screen.getByRole('button', { name: '关注 600000' }))
    await waitFor(() =>
      expect(qlibApi.setBasket).toHaveBeenCalledWith(run.id, 'SH600000', true),
    )
    expect(
      await screen.findByRole('button', { name: '取消关注 600000' }),
    ).toBeTruthy()
    expect(stocksApi.create).not.toHaveBeenCalled()
    expect(stocksApi.remove).not.toHaveBeenCalled()
  })
  it('does not hide demos behind a persisted real-holding filter', async () => {
    localStorage.setItem(
      'panwatch_qlib_opportunity_filters_v1',
      JSON.stringify({
        source: 'all',
        holding: 'held',
        top: 'all',
        availability: 'all',
        search: '',
      }),
    )
    view()
    await screen.findByRole('button', { name: '查看 样例股票 600000 详情' })
    expect(
      screen.getAllByRole('button', { name: /^查看 样例股票.*详情$/ }),
    ).toHaveLength(8)
    expect(
      (screen.getByRole('combobox', { name: '真实持仓' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true)
  })
  it('joins real names/holdings and opens the existing normalized stock detail component', async () => {
    market()
    view()
    const button = await screen.findByRole('button', {
      name: '查看 测试证券 详情',
    })
    await userEvent.click(button)
    expect(
      await screen.findByRole('dialog', { name: 'Shared stock details' }),
    ).toBeTruthy()
    expect(insight.mock.calls.at(-1)![0]).toMatchObject({
      symbol: '600000',
      market: 'CN',
      stockName: '测试证券',
      hasPosition: true,
    })
    expect(screen.getByText(/这部分是 Qlib 冻结排名/)).toBeTruthy()
    expect(
      (
        screen.getByRole('button', {
          name: '取消关注 600000',
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true)
  })
  it('filters real held and watched candidates', async () => {
    market()
    view()
    await screen.findByRole('button', { name: '查看 测试证券 详情' })
    await choose('真实持仓', '持仓中')
    await userEvent.click(screen.getByRole('button', { name: '应用筛选' }))
    expect(
      screen.getAllByRole('button', { name: /^查看 .*详情$/ }),
    ).toHaveLength(1)
  })
  it('creates a real follow only after a user action with normalized CN identity', async () => {
    market()
    view()
    await screen.findByRole('button', { name: '查看 测试证券 详情' })
    expect(stocksApi.create).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: '关注 600001' }))
    await waitFor(() =>
      expect(stocksApi.create).toHaveBeenCalledWith({
        symbol: '600001',
        name: '600001',
        market: 'CN',
        asset_type: 'stock',
      }),
    )
    expect(qlibApi.setBasket).not.toHaveBeenCalled()
    expect(qlibApi.decision).not.toHaveBeenCalled()
  })
  it('keeps failed personal metadata unknown and does not revive scores', async () => {
    market()
    vi.mocked(stocksApi.list).mockRejectedValue(
      new Error('watchlist unavailable'),
    )
    view()
    await screen.findByText(/自选或真实持仓信息暂不可用/)
    expect(
      (screen.getByRole('combobox', { name: '真实持仓' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true)
    expect(
      (screen.getByRole('button', { name: '关注 600000' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true)
    expect(
      screen.getAllByRole('button', { name: /^查看 .*详情$/ }),
    ).toHaveLength(8)
  })
  it('shows honest setup and failure states and never trains on refresh', async () => {
    vi.mocked(qlibApi.opportunities).mockResolvedValue({
      items: [],
      run: null,
      status: 'no_champion',
      mode: 'research',
    })
    view()
    await screen.findByText(/尚未设置选股模型/)
    expect(
      screen
        .getByRole('link', { name: '前往量化研究完成数据与模型设置' })
        .getAttribute('href'),
    ).toBe('/research?tab=data')
    vi.mocked(qlibApi.opportunities).mockRejectedValue(
      new Error('controlled failure'),
    )
    await userEvent.click(screen.getByRole('button', { name: '刷新' }))
    expect((await screen.findByRole('alert')).textContent).toContain(
      'controlled failure',
    )
    expect(qlibApi.train).not.toHaveBeenCalled()
    expect(qlibApi.predict).not.toHaveBeenCalled()
  })
})
