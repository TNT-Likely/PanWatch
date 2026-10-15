import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it } from 'vitest'
import type { QlibAssessment } from '@panwatch/api'
import StrategySummary from '@/components/strategy-summary'
import i18n from '@/i18n'

beforeEach(async () => {
  await i18n.changeLanguage('zh-CN')
})
afterEach(cleanup)
const view = (assessment?: QlibAssessment) =>
  render(
    <MemoryRouter>
      <StrategySummary assessment={assessment} />
    </MemoryRouter>,
  )
it('makes negative research evidence and legacy policy visible without inventing recommendations', () => {
  view({
    status: 'failed',
    eligible: false,
    reasons: [
      'qlib_strategy_performance_failed',
      'qlib_strategy_backtest_mismatch',
    ],
    policy_aligned: false,
    rank_ic: -0.01,
    net_return: -0.302,
    max_drawdown: 0.377,
    test_days: 144,
    paper_policy: 'target-v2',
    horizon: 5,
    topk: 10,
  })
  expect(screen.getByText(/暂无合格自动策略/)).toBeTruthy()
  expect(screen.getByText('-30.2%')).toBeTruthy()
  expect(screen.getByText('37.7%')).toBeTruthy()
  expect(screen.getByText(/不能证明当前模拟效果/)).toBeTruthy()
  expect(screen.getByText(/不提供自动止盈止损/)).toBeTruthy()
  expect(
    screen
      .getByRole('link', { name: '查看研究报告与验证条件' })
      .getAttribute('href'),
  ).toBe('/research')
})
it('missing evidence never defaults to an approved strategy', () => {
  view()
  expect(screen.getByText(/暂无合格自动策略/)).toBeTruthy()
  expect(screen.queryByText(/已通过发布门槛/)).toBeNull()
  expect(screen.getAllByText('—')).toHaveLength(3)
})
