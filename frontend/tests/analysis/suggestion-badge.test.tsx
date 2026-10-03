import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '@/i18n'
import { SuggestionBadge, type SuggestionInfo, type KlineSummary } from '@panwatch/biz-ui/components/suggestion-badge'
import { suggestionPresentation } from '@panwatch/biz-ui/components/suggestion-action'

afterEach(cleanup)
const technical = { trend: '多头排列', rsi_status: '超买' } as KlineSummary
const suggestion: SuggestionInfo = {
  action: 'watch', action_label: '观望', attention_required: true,
  signal: '风险上升', reason: '波动加大', should_alert: true,
  agent_name: 'premarket_outlook', agent_label: '盘前分析',
}

describe('suggestion badges', () => {
  it('shows a single indicator badge without fabricating an AI result', () => {
    render(<SuggestionBadge suggestion={null} kline={technical} hasPosition />)
    expect(screen.getAllByRole('button', { name: '持有' })).toHaveLength(1)
    expect(screen.queryByText('AI')).toBeNull()
    expect(screen.queryByText(/指标 ·/)).toBeNull()
  })
  it('keeps the AI corner tag, independent attention state and technical action', () => {
    render(<SuggestionBadge suggestion={suggestion} kline={technical} hasPosition />)
    expect(screen.getByRole('button', { name: '提醒 AI' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '持有' })).toBeTruthy()
    expect(screen.getByText('AI').className).toContain('absolute')
    expect(screen.queryByText(/AI ·|指标 ·/)).toBeNull()
  })
  it('does not display watch as the conclusion of missing technical data', () => {
    render(<SuggestionBadge suggestion={suggestion} kline={null} />)
    expect(screen.getByRole('button', { name: '暂无数据' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '观望' })).toBeNull()
  })
  it('recovers REVIEW from an old label even when the stored direction is hold', () => {
    render(<SuggestionBadge suggestion={{ ...suggestion, action: 'hold', action_label: '待人工复核',
      agent_name: 'tradingagents', attention_required: false }} showTechnicalCompanion={false} />)
    expect(screen.getByRole('button', { name: '待复核 AI' })).toBeTruthy()
    expect(screen.queryByText('持有')).toBeNull()
  })
  it('preserves attention and review in English', async () => {
    await i18n.changeLanguage('en-US')
    const { rerender } = render(<SuggestionBadge suggestion={suggestion} showTechnicalCompanion={false} />)
    expect(screen.getByRole('button', { name: 'Alert AI' })).toBeTruthy()
    rerender(<SuggestionBadge suggestion={{ ...suggestion, action: 'hold', rating_raw: 'review',
      review_required: true }} showTechnicalCompanion={false} />)
    expect(screen.getByRole('button', { name: 'Review required AI' })).toBeTruthy()
  })
  it('recovers five-tier ratings and persisted state without conflating them', () => {
    expect(suggestionPresentation({ action: 'buy', action_label: '增持' }).action).toBe('add')
    expect(suggestionPresentation({ action: 'sell', rating_raw: 'underweight' }).action).toBe('reduce')
    expect(suggestionPresentation({ action: 'hold', meta: { suggestion_state: { review_required: true } } }).review).toBe(true)
  })
})
