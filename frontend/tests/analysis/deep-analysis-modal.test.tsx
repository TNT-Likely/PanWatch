import i18n from '@/i18n'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { tradingAgentsApi, type DeepAnalysisResult, type ProgressResponse } from '@panwatch/api'
import { DeepAnalysisModal } from '@panwatch/biz-ui/components/deep-analysis-modal'

vi.mock('@panwatch/api', () => ({ tradingAgentsApi: { findRunning: vi.fn(), getLatestForStock: vi.fn(), getProgress: vi.fn() }, subscribeSSE: vi.fn() }))
vi.mock('@panwatch/base-ui/components/ui/toast', () => ({ useToast: () => ({ toast: vi.fn() }) }))
const result = { agent_name: 'tradingagents', title: '广汽集团', content: 'Report', analysis_date: '2026-10-01', generated_at: '2026-10-01T15:30:00+08:00', raw_data: { cost_usd: 0.0378, from_cache: true, suggestion: { action: 'hold', action_label: '持有', confidence: 7 }, token_usage: { input_tokens: 800, output_tokens: 200, total_tokens: 1000, recorded_calls: 2, completed_calls: 2, complete: true }, final_decision: 'PM report' } } as DeepAnalysisResult
const props = { open: true, onOpenChange: vi.fn(), stockId: 1, stockName: '广汽集团', stockSymbol: '601238' }
beforeEach(async () => {
  vi.clearAllMocks()
  await i18n.changeLanguage('zh-CN')
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

  it('restores the actual provider error after closing and reopening a failed run', async () => {
    const error = 'AI 服务拒绝处理本次内容，请调整问题后重试。（HTTP 400 · code=1301 · 系统检测到输入或生成内容可能包含不安全或敏感内容）'
    vi.mocked(tradingAgentsApi.findRunning).mockResolvedValue({ trace_id: 'failed-trace', status: 'failed' })
    vi.mocked(tradingAgentsApi.getProgress).mockResolvedValue({ trace_id: 'failed-trace', status: 'failed',
      stages: [], completed_stages: [], elapsed_sec: 42, total_cost_usd: 0,
      run: { agent_name: 'tradingagents', status: 'failed', error, result: '', duration_ms: 42000, model_label: 'GLM', notify_sent: false },
    })
    render(<DeepAnalysisModal {...props} />)
    expect(await screen.findByText(error)).toBeTruthy()
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '开始分析' })).toBeNull()
  })

  it('does not apply a previous stock failure when its diagnostic arrives late', async () => {
    let resolveProgress!: (value: ProgressResponse) => void
    vi.mocked(tradingAgentsApi.findRunning).mockResolvedValueOnce({ trace_id: 'failed-trace', status: 'failed' })
    vi.mocked(tradingAgentsApi.getProgress).mockReturnValueOnce(new Promise(resolve => { resolveProgress = resolve }))
    const { rerender } = render(<DeepAnalysisModal {...props} />)
    await waitFor(() => expect(tradingAgentsApi.getProgress).toHaveBeenCalledWith('failed-trace'))
    rerender(<DeepAnalysisModal {...props} stockId={2} stockSymbol="300750" stockName="宁德时代" />)
    await waitFor(() => expect(tradingAgentsApi.findRunning).toHaveBeenCalledWith('300750'))
    await act(async () => resolveProgress({ trace_id: 'failed-trace', status: 'failed',
      stages: [], completed_stages: [], elapsed_sec: 42, total_cost_usd: 0,
      run: { agent_name: 'tradingagents', status: 'failed', error: 'Previous stock diagnostic', result: '', duration_ms: 42000, model_label: 'GLM', notify_sent: false },
    }))
    expect(screen.queryByText('Previous stock diagnostic')).toBeNull()
    expect(screen.getByRole('button', { name: '开始分析' })).toBeTruthy()
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
