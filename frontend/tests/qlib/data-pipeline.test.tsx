import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { qlibApi, stocksApi } from '@panwatch/api'
import QlibDataPipeline from '@/components/qlib-data-pipeline'
import { ConfirmProvider } from '@panwatch/base-ui/components/ui/confirm-dialog'
import i18n from '@/i18n'

vi.mock('@panwatch/api', async (original) => ({
  ...(await original<typeof import('@panwatch/api')>()),
  qlibApi: Object.fromEntries(
    [
      'dataSource',
      'governance',
      'jobs',
      'configureDataSource',
      'syncDataSource',
      'cancelDataSync',
      'train',
      'publish',
    ].map((k) => [k, vi.fn()]),
  ),
  stocksApi: { list: vi.fn() },
}))
const changed = vi.fn().mockResolvedValue(undefined)
const view = () =>
  render(
    <MemoryRouter>
      <ConfirmProvider
        labels={{ title: '确认', cancel: '取消', confirm: '确认' }}
      >
        <QlibDataPipeline onChange={changed} />
      </ConfirmProvider>
    </MemoryRouter>,
  )
beforeEach(async () => {
  vi.resetAllMocks()
  changed.mockResolvedValue(undefined)
  await i18n.changeLanguage('zh-CN')
  vi.mocked(qlibApi.dataSource).mockResolvedValue({
    source: null,
    dataset_id: null,
    vendors: ['tencent'],
    job: null,
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
it('opening the page or setup does not download, train, publish or change settings', async () => {
  view()
  await screen.findByText('尚未初始化真实研究池，不是今天没有股票机会。')
  await userEvent.click(screen.getByRole('button', { name: '接入现有行情' }))
  expect(
    (
      screen.getByRole('button', {
        name: '初始化行情并训练',
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true)
  expect(qlibApi.syncDataSource).not.toHaveBeenCalled()
  expect(qlibApi.configureDataSource).not.toHaveBeenCalled()
  expect(qlibApi.train).not.toHaveBeenCalled()
  expect(qlibApi.publish).not.toHaveBeenCalled()
  await userEvent.keyboard('{Escape}')
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
})
it('explicit initialization requires valid pool and consent and queues training without approval', async () => {
  view()
  await screen.findByText('尚未初始化真实研究池，不是今天没有股票机会。')
  await userEvent.click(screen.getByRole('button', { name: '接入现有行情' }))
  await userEvent.clear(screen.getByRole('textbox', { name: /固定研究池/ }))
  await userEvent.type(
    screen.getByRole('textbox', { name: /固定研究池/ }),
    '600000,600036,000001',
  )
  await userEvent.type(
    screen.getByRole('textbox', { name: '已有数据使用依据' }),
    'Controlled research authorization',
  )
  const initialize = screen.getByRole('button', { name: '初始化行情并训练' })
  expect((initialize as HTMLButtonElement).disabled).toBe(true)
  await userEvent.click(screen.getByRole('checkbox', { name: /我确认有权/ }))
  await userEvent.click(initialize)
  await waitFor(() =>
    expect(qlibApi.syncDataSource).toHaveBeenCalledWith(
      expect.stringMatching(/^marketdata-/),
      true,
      true,
    ),
  )
  expect(qlibApi.configureDataSource).toHaveBeenCalledWith({
    symbols: ['SH600000', 'SH600036', 'SZ000001'],
    vendor: 'auto',
    license: 'Controlled research authorization',
    license_acknowledged: true,
    automatic: false,
  })
  expect(qlibApi.publish).not.toHaveBeenCalled()
})
it('duplicates and unsupported symbols cannot initialize', async () => {
  view()
  await userEvent.click(
    await screen.findByRole('button', { name: '接入现有行情' }),
  )
  const pool = screen.getByRole('textbox', { name: /固定研究池/ })
  await userEvent.clear(pool)
  await userEvent.type(pool, '600000 600000 300001')
  expect(
    (
      screen.getByRole('button', {
        name: '初始化行情并训练',
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true)
  expect(screen.getByText(/请填写 3–20 个不重复/)).not.toBeNull()
})
it('read failures do not fabricate ready data or hide existing opportunities', async () => {
  vi.mocked(qlibApi.dataSource).mockRejectedValue(new Error('503 controlled'))
  view()
  expect((await screen.findByRole('alert')).textContent).toContain(
    'qlib_marketdata_status_unavailable',
  )
  expect(qlibApi.syncDataSource).not.toHaveBeenCalled()
  expect(changed).not.toHaveBeenCalled()
})
