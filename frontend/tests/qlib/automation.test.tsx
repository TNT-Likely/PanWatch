import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, afterEach, it, expect, vi } from 'vitest'
import { qlibApi, type QlibAutomationStatus } from '@panwatch/api'
import { ConfirmProvider } from '@panwatch/base-ui/components/ui/confirm-dialog'
import PaperAutomation from '@/components/paper-automation'
import i18n from '@/i18n'

vi.mock('@panwatch/api', async (original) => ({
  ...(await original<typeof import('@panwatch/api')>()),
  qlibApi: { automationStatus: vi.fn(), configureAutomation: vi.fn() },
}))
const status: QlibAutomationStatus = {
  mode: 'automatic',
  authorized: false,
  model_id: 'a'.repeat(32),
  model_name: 'QA model',
  account_enabled: false,
  can_authorize: true,
  capital: {
    account_id: 1,
    initial_capital: 1000000,
    allocations: { CN: 0.5, HK: 0.3, US: 0.2 },
    market_principal: 500000,
    target_ratio: 0.9,
    capital_policy: 'market_pool_target_v2',
    capital_config_hash: 'c'.repeat(64),
  },
  bound_market_principal: null,
  expires_at: null,
  grant_id: null,
  phase: 'needs_authorization',
  reason: '',
  latest_decision_id: null,
}
const view = () =>
  render(
    <MemoryRouter>
      <ConfirmProvider
        labels={{ title: '确认', cancel: '取消', confirm: '确认' }}
      >
        <PaperAutomation />
      </ConfirmProvider>
    </MemoryRouter>,
  )
beforeEach(async () => {
  vi.resetAllMocks()
  await i18n.changeLanguage('zh-CN')
  vi.mocked(qlibApi.automationStatus).mockResolvedValue(status)
})
afterEach(cleanup)

it('rereads account funds on opening consent instead of waiting for polling', async () => {
  vi.mocked(qlibApi.automationStatus)
    .mockResolvedValueOnce(status)
    .mockResolvedValue({
      ...status,
      capital: {
        ...status.capital!,
        allocations: { CN: 0.6, HK: 0.2, US: 0.2 },
        market_principal: 600000,
        capital_config_hash: 'd'.repeat(64),
      },
    })
  view()
  await userEvent.click(
    await screen.findByRole('button', { name: '启动自动选股与模拟' }),
  )
  expect(await screen.findByText(/分配本金 600,000/)).toBeTruthy()
  expect(screen.queryByText(/分配本金 500,000/)).toBeNull()
})

it('stop revokes consent while preserving automatic as the preferred mode', async () => {
  vi.mocked(qlibApi.automationStatus).mockResolvedValue({
    ...status,
    authorized: true,
    phase: 'planned',
  })
  vi.mocked(qlibApi.configureAutomation).mockResolvedValue(status)
  view()
  await userEvent.click(
    await screen.findByRole('button', { name: '停用自动模式' }),
  )
  await userEvent.click(
    screen.getByRole('button', { name: '确认', exact: true }),
  )
  await waitFor(() =>
    expect(qlibApi.configureAutomation).toHaveBeenCalledWith({
      mode: 'automatic',
      enabled: false,
    }),
  )
})
it('automatic is default but opening and escaping never authorize or activate', async () => {
  view()
  await userEvent.click(
    await screen.findByRole('button', { name: '启动自动选股与模拟' }),
  )
  expect(screen.getByText(/一次授权后/)).toBeTruthy()
  expect(
    (
      screen.getByRole('button', {
        name: '授权并启动自动模式',
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true)
  await userEvent.keyboard('{Escape}')
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(qlibApi.configureAutomation).not.toHaveBeenCalled()
})
it('explicit standing consent sends model budget lease and stable request key', async () => {
  view()
  await userEvent.click(
    await screen.findByRole('button', { name: '启动自动选股与模拟' }),
  )
  expect(screen.queryByRole('spinbutton')).toBeNull()
  expect(screen.getByText(/分配本金 500,000/)).toBeTruthy()
  await userEvent.click(
    screen.getByRole('checkbox', { name: /我授权按以上模拟盘资金配置/ }),
  )
  vi.mocked(qlibApi.configureAutomation).mockResolvedValue({
    ...status,
    authorized: true,
    phase: 'blocked',
    reason: 'qlib_automation_source_failed',
  })
  await userEvent.click(
    screen.getByRole('button', { name: '授权并启动自动模式' }),
  )
  await waitFor(() =>
    expect(qlibApi.configureAutomation).toHaveBeenCalledOnce(),
  )
  expect(qlibApi.configureAutomation).toHaveBeenCalledWith({
    mode: 'automatic',
    request_key: expect.any(String),
    model_id: status.model_id,
    capital_config_hash: status.capital!.capital_config_hash,
    days: 30,
    acknowledge_research: true,
  })
  expect(await screen.findByText(/行情同步尚未成功/)).toBeTruthy()
})
it('unallocated market cannot activate and funds have only one editing entry', async () => {
  vi.mocked(qlibApi.automationStatus).mockResolvedValue({
    ...status,
    capital: {
      ...status.capital!,
      market_principal: 0,
      allocations: { CN: 0, HK: 0.5, US: 0.5 },
    },
  })
  view()
  await userEvent.click(
    await screen.findByRole('button', { name: '启动自动选股与模拟' }),
  )
  expect(screen.queryByRole('spinbutton')).toBeNull()
  expect(
    screen
      .getByRole('link', { name: '前往模拟盘调整资金配置' })
      .getAttribute('href'),
  ).toBe('/paper-trading')
  await userEvent.click(
    screen.getByRole('checkbox', { name: /我授权按以上模拟盘资金配置/ }),
  )
  expect(
    (
      screen.getByRole('button', {
        name: '授权并启动自动模式',
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true)
  expect(qlibApi.configureAutomation).not.toHaveBeenCalled()
})
it('switching manual requires a confirm and cancels standing authority only on acceptance', async () => {
  view()
  await userEvent.click(
    await screen.findByRole('button', { name: '切换手动模式' }),
  )
  await userEvent.click(
    screen.getByRole('button', { name: '取消', exact: true }),
  )
  expect(qlibApi.configureAutomation).not.toHaveBeenCalled()
})
