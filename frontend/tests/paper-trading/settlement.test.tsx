import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { paperTradingApi } from '@panwatch/api'
import PaperTradingPage from '@/pages/PaperTrading'
import i18n from '@/i18n'
import { MarketColorProvider } from '@/hooks/use-market-colors'

vi.mock('@panwatch/api', () => ({ paperTradingApi: {
  getAccount: vi.fn(), listPositions: vi.fn(), getMetrics: vi.fn(), listTrades: vi.fn(),
} }))
vi.mock('@panwatch/base-ui/components/ui/toast', () => ({ useToast: () => ({ toast: vi.fn() }) }))

const account = {
  initial_capital: 10000, current_capital: 11000, cash_balance: 11000,
  settled_cash: 1000, unsettled_cash: 10000, buying_power: 1000,
  total_equity: 11000, total_pnl: 0, unrealized_pnl: 0, total_trades: 1,
  winning_trades: 0, win_rate: 0, max_drawdown_pct: 0, enabled: false,
  market_allocations: { CN: .5, HK: .3, US: .2 },
  pending_settlements: [{ trade_id: 1, market: 'US', amount: 10000,
    remaining_amount: 10000, settlement_date: '2026-10-13', status: 'pending' }],
}

beforeEach(async () => {
  vi.resetAllMocks()
  await i18n.changeLanguage('zh-CN')
  vi.mocked(paperTradingApi.getAccount).mockResolvedValue(account as never)
  vi.mocked(paperTradingApi.listPositions).mockResolvedValue([])
  vi.mocked(paperTradingApi.getMetrics).mockResolvedValue({ equity_curve: [], strategy_performance: [] } as never)
  vi.mocked(paperTradingApi.listTrades).mockResolvedValue({ total: 0, items: [] })
})
afterEach(cleanup)

it('uses buying power rather than the cash balance and shows the settlement schedule', async () => {
  render(<MarketColorProvider><PaperTradingPage /></MarketColorProvider>)
  const label = await screen.findByText('购买力')
  expect(within(label.closest('.card')!).getByText('1,000.00')).toBeTruthy()
  expect(screen.getByText('现金余额')).toBeTruthy()
  expect(screen.getByText('待交收卖出款')).toBeTruthy()
  expect(screen.getByText('2026-10-13')).toBeTruthy()
  await userEvent.click(screen.getByRole('button', { name: '美股 20%' }))
  expect(await screen.findByText(/现金账户：仅用已交收可用资金买入/)).toBeTruthy()
  expect(paperTradingApi.getAccount).toHaveBeenLastCalledWith('US')
})

it('explains CN reuse and updates funding after refresh', async () => {
  render(<MarketColorProvider><PaperTradingPage /></MarketColorProvider>)
  await screen.findByText('购买力')
  await userEvent.click(screen.getByRole('button', { name: 'A股 50%' }))
  expect(await screen.findByText(/A 股卖出款当日可用于买入/)).toBeTruthy()
  vi.mocked(paperTradingApi.getAccount).mockResolvedValue({ ...account, buying_power: 11000,
    settled_cash: 11000, unsettled_cash: 0, pending_settlements: [] } as never)
  await userEvent.click(screen.getByRole('button', { name: '刷新' }))
  expect(await screen.findByText('暂无待交收卖出款。')).toBeTruthy()
  expect(screen.queryByText('2026-10-13')).toBeNull()
  expect(within(screen.getByText('购买力').closest('.card')!).getByText('11,000.00')).toBeTruthy()
})

it('shows unknown dates without guessing a weekday', async () => {
  vi.mocked(paperTradingApi.getAccount).mockResolvedValue({ ...account,
    pending_settlements: [{ ...account.pending_settlements[0], settlement_date: null, status: 'unknown' }] } as never)
  render(<MarketColorProvider><PaperTradingPage /></MarketColorProvider>)
  expect(await screen.findByText('待更新交收日历')).toBeTruthy()
})

it('shows English settlement details in trade history', async () => {
  await i18n.changeLanguage('en-US')
  vi.mocked(paperTradingApi.listTrades).mockResolvedValue({ total: 1, items: [{ id: 1,
    stock_symbol: 'TEST', stock_market: 'US', stock_name: 'Settlement test',
    quantity: 100, entry_price: 10, exit_price: 11, pnl: 100, pnl_pct: 10,
    exit_reason: 'manual', strategy_code: 'trend_follow', holding_days: 0,
    closed_at: '2026-10-09', settlement_date: '2026-10-13', settlement_status: 'pending',
  }] } as never)
  render(<MarketColorProvider><PaperTradingPage /></MarketColorProvider>)
  expect(await screen.findByText('Settled available cash')).toBeTruthy()
  await userEvent.click(await screen.findByRole('button', { name: /^Closed trades/ }))
  const dialog = screen.getByRole('dialog')
  expect(within(dialog).getByText('Cash settlement')).toBeTruthy()
  expect(within(dialog).getByText('Pending')).toBeTruthy()
  expect(within(dialog).getByText('2026-10-13')).toBeTruthy()
})
