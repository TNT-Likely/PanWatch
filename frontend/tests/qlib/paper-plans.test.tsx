import { useState } from 'react'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import {
  qlibApi,
  type QlibPlanPreview,
  type QlibPaperPlan,
} from '@panwatch/api'
import PaperPlanDialog from '@/components/paper-plan-dialog'
import PaperPlanStatus from '@/components/paper-plan-status'
import { ConfirmProvider } from '@panwatch/base-ui/components/ui/confirm-dialog'
import { ToastProvider } from '@panwatch/base-ui/components/ui/toast'
import i18n from '@/i18n'

vi.mock('@panwatch/api', async (original) => ({
  ...(await original<typeof import('@panwatch/api')>()),
  qlibApi: Object.fromEntries(
    ['previewPlan', 'confirmPlan', 'paperPlans', 'cancelPlan'].map((k) => [
      k,
      vi.fn(),
    ]),
  ),
}))
const preview: QlibPlanPreview = {
  run_id: 'a'.repeat(32),
  model_id: 'b'.repeat(32),
  market: 'CN',
  asof_date: '2026-10-13',
  execution_date: '2026-10-14',
  execution_window: '09:30–09:35 Asia/Shanghai',
  research_mode: true,
  ready: true,
  blocking_reason: '',
  orders: [
    {
      symbol: 'SH600000',
      name: 'QA stock',
      side: 'buy',
      reference_price: 10,
      estimated_quantity: 1000,
      estimated_fee: 5,
      estimated_cash_delta: -10005,
      cash_limit: 11000,
      position_id: null,
    },
  ],
  kept: [],
  excluded: [],
  available_cash: 20000,
  cash_budget: 18000,
  estimated_fees: 5,
  account_enabled: false,
  existing_decision_id: null,
  preview_hash: 'c'.repeat(64),
  pool_count: 5573,
  ranked_count: 235,
}
const done = vi.fn()
function Harness() {
  const [open, setOpen] = useState(true)
  return (
    <PaperPlanDialog
      runId={preview.run_id}
      focusSymbol="SH600001"
      open={open}
      onOpenChange={setOpen}
      onConfirmed={done}
    />
  )
}
const view = (children = <Harness />) =>
  render(
    <MemoryRouter>
      <ConfirmProvider
        labels={{ title: '确认', cancel: '取消', confirm: '确认' }}
      >
        <ToastProvider>{children}</ToastProvider>
      </ConfirmProvider>
    </MemoryRouter>,
  )
beforeEach(async () => {
  vi.resetAllMocks()
  await i18n.changeLanguage('zh-CN')
  vi.mocked(qlibApi.previewPlan).mockResolvedValue(preview)
  vi.mocked(qlibApi.paperPlans).mockResolvedValue({
    items: [],
    account_enabled: false,
  })
})
afterEach(cleanup)

const statusPlan = (id: string, status: string): QlibPaperPlan => ({
  id,
  status,
  run_id: preview.run_id,
  model_id: preview.model_id,
  asof_date: preview.asof_date,
  execution_date: '2026-10-15',
  research_mode: true,
  cash_budget: 18000,
  orders: preview.orders,
  symbols: ['SH600000'],
  filled: 0,
  rejected: 0,
  can_cancel: status === 'waiting_open' || status === 'paused',
  events: [],
})

it('pending plans stay outside collapsed history and historical cancellations are preserved', async () => {
  vi.mocked(qlibApi.paperPlans).mockResolvedValue({
    items: [statusPlan('old', 'cancelled'), statusPlan('new', 'waiting_open')],
    account_enabled: true,
  })
  view(<PaperPlanStatus />)
  await screen.findByText('历史计划（1）')
  const history = screen.getByTestId('paper-plan-history') as HTMLDetailsElement
  expect(history.open).toBe(false)
  expect(history.querySelector('[data-plan-state="cancelled"]')).toBeTruthy()
  expect(history.querySelector('[data-plan-state="waiting_open"]')).toBeNull()
  expect(
    screen
      .getByTestId('paper-plan-status')
      .querySelector('[data-plan-state="waiting_open"]'),
  ).toBeTruthy()
  await userEvent.click(screen.getByText('历史计划（1）'))
  expect(history.open).toBe(true)
  expect(qlibApi.cancelPlan).not.toHaveBeenCalled()
})

it('compact opportunities show pending plans even if an older cancelled record comes first', async () => {
  vi.mocked(qlibApi.paperPlans).mockResolvedValue({
    items: [statusPlan('old', 'cancelled'), statusPlan('new', 'paused')],
    account_enabled: false,
  })
  view(<PaperPlanStatus compact />)
  await screen.findByText(/账户暂停，尚未执行/)
  expect(screen.queryByText(/已取消，不会执行/)).toBeNull()
  expect(
    screen.getByRole('link', { name: '历史计划（1）' }).getAttribute('href'),
  ).toBe('/paper-trading')
})

it('history-only state explains that starting does not restore old plans', async () => {
  vi.mocked(qlibApi.paperPlans).mockResolvedValue({
    items: [statusPlan('old', 'cancelled')],
    account_enabled: true,
  })
  view(<PaperPlanStatus />)
  expect(await screen.findByText(/目前没有待执行计划/)).toBeTruthy()
  expect(
    (screen.getByTestId('paper-plan-history') as HTMLDetailsElement).open,
  ).toBe(false)
  expect(qlibApi.confirmPlan).not.toHaveBeenCalled()
})

it('shows item cash caps and inspectable kept holdings and exclusion reasons', async () => {
  vi.mocked(qlibApi.previewPlan).mockResolvedValue({
    ...preview,
    kept: [{ symbol: 'SH600010', quantity: 100 }],
    excluded: [
      { symbol: 'SH688001', reason: 'qlib_security_execution_not_supported' },
    ],
  })
  view()
  await screen.findByText('逐项资金上限（含费）')
  expect(screen.getByText('11,000')).toBeTruthy()
  await userEvent.click(screen.getByText(/保留 1 个现有 Qlib 仓位/))
  expect(screen.getByText('SH600010')).toBeTruthy()
  expect(screen.getByText(/当前模拟成交仅支持普通非 ST A 股主板/)).toBeTruthy()
  expect(qlibApi.confirmPlan).not.toHaveBeenCalled()
})

it('opening and escaping the whole-portfolio preview never confirms or enables an account', async () => {
  view()
  await screen.findByText('QA stock')
  expect(screen.getByText(/整组模型组合/)).toBeTruthy()
  expect(screen.getByText(/SH600001 不在本次交易项/)).toBeTruthy()
  expect(screen.getByText(/模拟账户当前暂停/)).toBeTruthy()
  expect(
    (
      screen.getByRole('button', {
        name: '确认这份模拟计划',
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true)
  expect(qlibApi.confirmPlan).not.toHaveBeenCalled()
  await userEvent.keyboard('{Escape}')
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(qlibApi.confirmPlan).not.toHaveBeenCalled()
})

it('confirmation sends only the server preview token after explicit whole-plan consent', async () => {
  view()
  await screen.findByText('QA stock')
  vi.mocked(qlibApi.confirmPlan).mockResolvedValue({
    id: 'd'.repeat(32),
  } as never)
  await userEvent.click(
    screen.getByRole('checkbox', { name: /我确认以上整组买卖项/ }),
  )
  await userEvent.click(
    screen.getByRole('button', { name: '确认这份模拟计划' }),
  )
  await waitFor(() =>
    expect(qlibApi.confirmPlan).toHaveBeenCalledWith(
      preview.run_id,
      preview.preview_hash,
      true,
    ),
  )
  expect(done).toHaveBeenCalledOnce()
})

it('changed-preview conflict stays visible and requires a fresh preview and new consent', async () => {
  vi.mocked(qlibApi.confirmPlan).mockRejectedValue(
    new Error('qlib_preview_changed_conflict'),
  )
  view()
  await screen.findByText('QA stock')
  await userEvent.click(
    screen.getByRole('checkbox', { name: /我确认以上整组买卖项/ }),
  )
  await userEvent.click(
    screen.getByRole('button', { name: '确认这份模拟计划' }),
  )
  expect((await screen.findByRole('alert')).textContent).toContain(
    '请重新预览后确认',
  )
  expect(
    (
      screen.getByRole('button', {
        name: '确认这份模拟计划',
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true)
  await userEvent.click(screen.getByRole('button', { name: '重新预览' }))
  await waitFor(() => expect(qlibApi.previewPlan).toHaveBeenCalledTimes(2))
  expect(done).not.toHaveBeenCalled()
})

it('HK/US refusal explains an unsupported capability and cannot submit', async () => {
  vi.mocked(qlibApi.previewPlan).mockResolvedValue({
    ...preview,
    market: 'HK',
    ready: false,
    orders: [],
    blocking_reason: 'qlib_market_execution_not_supported',
    preview_hash: null,
  })
  view()
  expect(await screen.findByText(/港美股目前仅研究与观察/)).toBeTruthy()
  expect(
    (
      screen.getByRole('button', {
        name: '确认这份模拟计划',
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true)
  expect(qlibApi.confirmPlan).not.toHaveBeenCalled()
})

it('no plans never suggests that watching or rank updates execute automatically', async () => {
  view(<PaperPlanStatus />)
  expect(await screen.findByText(/还没有确认模拟计划/)).toBeTruthy()
  expect(screen.getByText(/关注是收藏/)).toBeTruthy()
  expect(qlibApi.confirmPlan).not.toHaveBeenCalled()
})
