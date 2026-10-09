import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { paperTradingApi, type PaperTradingPositionItem } from '@panwatch/api'
import PaperTradingPage from '@/pages/PaperTrading'
import i18n from '@/i18n'
import { MarketColorProvider } from '@/hooks/use-market-colors'

const { toast } = vi.hoisted(() => ({ toast: vi.fn() }))
vi.mock('@panwatch/api', () => ({ paperTradingApi: {
  getAccount: vi.fn(), listPositions: vi.fn(), getMetrics: vi.fn(), listTrades: vi.fn(), closePosition: vi.fn(),
} }))
vi.mock('@panwatch/base-ui/components/ui/toast', () => ({ useToast: () => ({ toast }) }))

const locked: PaperTradingPositionItem = {
  id: 1, stock_symbol: '600519', stock_market: 'CN', stock_name: '今日买入', quantity: 100,
  entry_price: 10, current_price: 10, unrealized_pnl: 0, unrealized_pnl_pct: 0,
  status: 'open', sellable_quantity: 0, sell_block_reason: 'paper_trading_t1_locked',
  signal_snapshot_date: '', signal_action: 'buy', strategy_code: 'trend_follow', holding_days: 0,
  opened_at: '2026-10-09T10:00:00+08:00', closed_at: '', updated_at: '',
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(paperTradingApi.getAccount).mockResolvedValue({ initial_capital: 100000, current_capital: 99000,
    total_equity: 100000, total_pnl: 0, unrealized_pnl: 0, total_trades: 0, winning_trades: 0,
    win_rate: 0, max_drawdown_pct: 0, enabled: true } as never)
  vi.mocked(paperTradingApi.listPositions).mockResolvedValue([locked, { ...locked, id: 2, stock_name: '昨日买入',
    sellable_quantity: 100, sell_block_reason: null }])
  vi.mocked(paperTradingApi.getMetrics).mockResolvedValue({ equity_curve: [], strategy_performance: [] } as never)
  vi.mocked(paperTradingApi.listTrades).mockResolvedValue({ total: 0, items: [] })
  vi.mocked(paperTradingApi.closePosition).mockResolvedValue({ ok: true })
})
afterEach(cleanup)

it('explains the lock and prevents same-day UI sells while allowing settled sells', async () => {
  render(<MarketColorProvider><PaperTradingPage /></MarketColorProvider>)
  const row = (await screen.findByText('今日买入')).closest('tr')!
  const button = within(row).getByRole('button', { name: '平仓' }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
  expect(within(row).getByText('T+1：当日买入，下一交易日可卖')).toBeTruthy()
  await userEvent.click(button)
  expect(paperTradingApi.closePosition).not.toHaveBeenCalled()
  const settledRow = screen.getByText('昨日买入').closest('tr')!
  await userEvent.click(within(settledRow).getByRole('button', { name: '平仓' }))
  expect(paperTradingApi.closePosition).toHaveBeenCalledWith(2)
})

it('refreshes settlement availability from the server on a new trading date', async () => {
  render(<MarketColorProvider><PaperTradingPage /></MarketColorProvider>)
  await screen.findByText('今日买入')
  vi.mocked(paperTradingApi.listPositions).mockResolvedValue([{ ...locked, sellable_quantity: 100, sell_block_reason: null }])
  await userEvent.click(screen.getByRole('button', { name: '刷新' }))
  await screen.findByText('今日买入')
  expect((screen.getByRole('button', { name: '平仓' }) as HTMLButtonElement).disabled).toBe(false)
  expect(screen.queryByText('T+1：当日买入，下一交易日可卖')).toBeNull()
})

it('shows the specific server error if settlement changes before the close request', async () => {
  vi.mocked(paperTradingApi.closePosition).mockRejectedValue(new Error('A 股实行 T+1'))
  render(<MarketColorProvider><PaperTradingPage /></MarketColorProvider>)
  const row = (await screen.findByText('昨日买入')).closest('tr')!
  await userEvent.click(within(row).getByRole('button', { name: '平仓' }))
  expect(toast).toHaveBeenCalledWith('A 股实行 T+1', 'error')
})

it('explains the lock in English', async () => {
  await i18n.changeLanguage('en-US')
  render(<MarketColorProvider><PaperTradingPage /></MarketColorProvider>)
  expect(await screen.findByText('T+1: bought today; sell from the next trading day')).toBeTruthy()
})
