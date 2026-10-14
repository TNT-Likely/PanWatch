import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { qlibApi, type QlibUniverseStatus } from '@panwatch/api'
import QlibDataPipeline from '@/components/qlib-data-pipeline'
import { ConfirmProvider } from '@panwatch/base-ui/components/ui/confirm-dialog'
import i18n from '@/i18n'

vi.mock('@panwatch/api', async (original) => ({
  ...(await original<typeof import('@panwatch/api')>()),
  qlibApi: Object.fromEntries(
    [
      'universe',
      'governance',
      'jobs',
      'configureUniverse',
      'universeJob',
      'universeJobAction',
      'universeMembers',
      'publish',
    ].map((k) => [k, vi.fn()]),
  ),
}))
const changed = vi.fn().mockResolvedValue(undefined)
const markets = Object.fromEntries(
  ['CN', 'HK', 'US'].map((m) => [
    m,
    {
      total: 0,
      counts: {},
      reasons: {},
      vendors: m === 'CN' ? ['tencent'] : [],
      champion_id: null,
      completed_day: '2026-10-13',
      predicted: 0,
      prediction_date: null,
      catalog: null,
    },
  ]),
) as QlibUniverseStatus['markets']
const view = () =>
  render(
    <MemoryRouter>
      <ConfirmProvider
        labels={{ title: '确认', cancel: '取消', confirm: '确认' }}
      >
        <QlibDataPipeline market="HK" onChange={changed} />
      </ConfirmProvider>
    </MemoryRouter>,
  )
beforeEach(async () => {
  vi.resetAllMocks()
  changed.mockResolvedValue(undefined)
  await i18n.changeLanguage('zh-CN')
  vi.mocked(qlibApi.universe).mockResolvedValue({
    config: null,
    markets,
    jobs: [],
  })
  vi.mocked(qlibApi.governance).mockResolvedValue({
    champion_id: null,
    revision: 0,
    settings: {
      enabled: false,
      dataset_id: null,
      daily_hour: 18,
      retrain_every_days: 30,
    },
    models: [],
    audit: [],
  })
  vi.mocked(qlibApi.jobs).mockResolvedValue({ items: [] })
})
afterEach(cleanup)
it('opening full universe setup never syncs, trains, approves or changes source settings', async () => {
  view()
  await userEvent.click(
    await screen.findByRole('button', { name: '初始化完整研究池' }),
  )
  expect(
    (
      screen.getByRole('button', {
        name: '保存并获取完整目录',
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true)
  expect(screen.queryByRole('textbox', { name: /固定研究池/ })).toBeNull()
  expect(screen.getByText(/北交所/)).toBeTruthy()
  expect(qlibApi.universeJob).not.toHaveBeenCalled()
  expect(qlibApi.publish).not.toHaveBeenCalled()
  await userEvent.keyboard('{Escape}')
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
})
it('consented setup fetches all three catalogs without auto-training or approval', async () => {
  view()
  await userEvent.click(
    await screen.findByRole('button', { name: '初始化完整研究池' }),
  )
  await userEvent.type(
    screen.getByRole('textbox', { name: '数据使用依据' }),
    'Controlled research authorization',
  )
  await userEvent.click(screen.getByRole('checkbox', { name: /我确认有权/ }))
  await userEvent.click(
    screen.getByRole('button', { name: '保存并获取完整目录' }),
  )
  await waitFor(() =>
    expect(qlibApi.universeJob).toHaveBeenCalledWith('catalog'),
  )
  expect(qlibApi.configureUniverse).toHaveBeenCalledWith({
    markets: ['CN', 'HK', 'US'],
    license: 'Controlled research authorization',
    license_acknowledged: true,
    automatic: false,
    training_stocks: 128,
  })
  expect(qlibApi.publish).not.toHaveBeenCalled()
})
it('training budget is not a pool size and a disabled overseas source is explicit', async () => {
  vi.mocked(qlibApi.universe).mockResolvedValue({
    config: {
      markets: ['CN', 'HK', 'US'],
      license: 'CC0 research',
      license_acknowledged: true,
      automatic: false,
      training_stocks: 128,
    },
    markets: {
      ...markets,
      HK: { ...markets.HK, total: 2825, counts: { pending: 2825 } },
    },
    jobs: [],
  })
  view()
  expect(await screen.findByText('2825')).toBeTruthy()
  expect(screen.getByText(/训练样本预算 128 只，不限制预测池/)).toBeTruthy()
  expect(
    (
      screen.getByRole('button', {
        name: '同步本市场全部日线',
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true)
  expect(
    screen.getByRole('link', { name: '数据源设置' }).getAttribute('href'),
  ).toBe('/datasources')
})
it('read failures stay explicit and never fabricate coverage', async () => {
  vi.mocked(qlibApi.universe).mockRejectedValue(new Error('503 controlled'))
  view()
  expect((await screen.findByRole('alert')).textContent).toContain(
    'qlib_universe_status_unavailable',
  )
  expect(qlibApi.universeJob).not.toHaveBeenCalled()
})

it('a later successful training never hides unfinished history or its resume action', async () => {
  const job = {
    market: 'HK' as const,
    progress: { phase: 'history', completed: 900, total: 2825 },
    result_id: null,
    resumable: false,
    error_code: '',
  }
  vi.mocked(qlibApi.universe).mockResolvedValue({
    config: null,
    markets,
    jobs: [
      { ...job, id: 'new-train', kind: 'train', status: 'succeeded' },
      {
        ...job,
        id: 'old-history',
        kind: 'history',
        status: 'failed',
        resumable: true,
        error_code: 'qlib_source_unavailable_resume_sync',
      },
    ],
  })
  view()
  expect((await screen.findByRole('alert')).textContent).toContain(
    'qlib_source_unavailable_resume_sync',
  )
  await userEvent.click(screen.getByRole('button', { name: '继续未完成同步' }))
  await waitFor(() =>
    expect(qlibApi.universeJobAction).toHaveBeenCalledWith(
      'old-history',
      'resume',
    ),
  )
})
