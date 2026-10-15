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
  recommended_cash_limit: 450000,
  max_buy_cash: null,
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
  await userEvent.clear(screen.getByRole('spinbutton'))
  await userEvent.type(screen.getByRole('spinbutton'), '20000')
  await userEvent.click(
    screen.getByRole('checkbox', { name: /我授权以上预算/ }),
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
    max_buy_cash: 20000,
    days: 30,
    acknowledge_research: true,
  })
  expect(await screen.findByText(/行情同步尚未成功/)).toBeTruthy()
})
it('excess budget and unchecked consent cannot activate', async () => {
  view()
  await userEvent.click(
    await screen.findByRole('button', { name: '启动自动选股与模拟' }),
  )
  await userEvent.clear(screen.getByRole('spinbutton'))
  await userEvent.type(screen.getByRole('spinbutton'), '999999')
  await userEvent.click(
    screen.getByRole('checkbox', { name: /我授权以上预算/ }),
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
