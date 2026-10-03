import i18n from '@/i18n'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { tradingAgentsApi, type DeepAnalysisResult } from '@panwatch/api'
import { DeepAnalysisModal } from '@panwatch/biz-ui/components/deep-analysis-modal'

vi.mock('@panwatch/api', () => ({ tradingAgentsApi: { findRunning: vi.fn(), getLatestForStock: vi.fn() }, subscribeSSE: vi.fn() }))
vi.mock('@panwatch/base-ui/components/ui/toast', () => ({ useToast: () => ({ toast: vi.fn() }) }))
const result = { agent_name: 'tradingagents', title: '广汽集团', content: 'Report', analysis_date: '2026-10-01', generated_at: '2026-10-01T15:30:00+08:00', raw_data: { cost_usd: 0.0378, from_cache: true, suggestion: { action: 'hold', action_label: '持有', confidence: 7 }, token_usage: { input_tokens: 800, output_tokens: 200, total_tokens: 1000, recorded_calls: 2, completed_calls: 2, complete: true }, final_decision: 'PM report' } } as DeepAnalysisResult
const props = { open: true, onOpenChange: vi.fn(), stockId: 1, stockName: '广汽集团', stockSymbol: '601238' }
beforeEach(() => {
  vi.mocked(tradingAgentsApi.findRunning).mockResolvedValue({ trace_id: null, status: 'none' })
  vi.mocked(tradingAgentsApi.getLatestForStock).mockResolvedValue(null)
})

describe('deep analysis modal', () => {
  it('opens a historical cached report with dates, usage and the matching detail route', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    render(<DeepAnalysisModal {...props} initialResult={result} />)
    expect(await screen.findByText('报告日期：2026-10-01')).toBeTruthy()
    expect(screen.getByText('Token 合计 1,000')).toBeTruthy()
    expect(screen.getByText(/缓存报告：展示此前生成/)).toBeTruthy()
    expect(screen.queryByText(/0\.0378|本月预算|今天已经分析/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '查看详细页' }))
    expect(open).toHaveBeenCalledWith('/analysis/601238/2026-10-01', '_blank')
    open.mockRestore()
  })
  it('can start without requesting or waiting for a monthly budget', async () => {
    render(<DeepAnalysisModal {...props} />)
    await waitFor(() => expect(tradingAgentsApi.findRunning).toHaveBeenCalledWith('601238'))
    await waitFor(() => expect(tradingAgentsApi.getLatestForStock).toHaveBeenCalledWith('601238'))
    expect(screen.getByRole('button', { name: '开始分析' }).hasAttribute('disabled')).toBe(false)
    expect(screen.queryByText(/本月预算|预估成本|预算已用尽/)).toBeNull()
  })
})

it('shows REVIEW as a review state in an English historical report', async () => {
  await i18n.changeLanguage('en-US')
  const review = { ...result, raw_data: { ...result.raw_data, suggestion: {
    ...result.raw_data.suggestion, action: 'hold' as const, action_label: '待人工复核', rating_raw: 'review' as const, review_required: true,
  } } }
  render(<DeepAnalysisModal {...props} initialResult={review} />)
  expect(await screen.findByText('Review required')).toBeTruthy()
  expect(screen.queryByText('Hold')).toBeNull()
})
