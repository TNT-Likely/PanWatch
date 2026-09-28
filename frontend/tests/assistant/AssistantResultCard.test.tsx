import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AssistantResultCard } from '@/components/assistant/AssistantResultCard'
import type { AssistantResult } from '@panwatch/api'

const result: AssistantResult = {
  schema_version: 1,
  summary: '贵州茅台行情结论',
  facts: [{ text: 'CN:600519 最新价为 100。', evidence_ids: ['ev-1'] }],
  inferences: ['短期趋势偏强。'],
  risks: ['行情存在波动。'],
  missing_data: [],
  evidence: [{
    id: 'ev-1',
    tool_name: 'get_stock_quote',
    source_name: 'PanWatch 行情',
    source_url: 'https://example.com/quote',
    summary: '最新价 100',
    observed_at: '2026-09-29T01:00:00Z',
    data_at: '2026-09-29T01:00:00Z',
    freshness: 'fresh',
    freshness_basis: 'as_of',
    symbol: '600519',
    market: 'CN',
  }],
  next_actions: [{
    id: 'open-chart',
    kind: 'navigate',
    label: '打开 K 线',
    payload: { path: '/portfolio?view=kline&symbol=600519&market=CN' },
    requires_approval: false,
  }],
}

describe('AssistantResultCard', () => {
  it('shows evidence and accepts a validated chart deep link', async () => {
    const user = userEvent.setup()
    const onNavigate = vi.fn()
    render(
      <AssistantResultCard
        result={result}
        onPrefill={vi.fn()}
        onNavigate={onNavigate}
        onSubmitPrompt={vi.fn()}
      />,
    )

    expect(screen.getByText('CN:600519 最新价为 100。')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: /依据/ }))
    expect(screen.getByRole('link', { name: /PanWatch 行情/ }).getAttribute('href')).toBe('https://example.com/quote')

    await user.click(screen.getByRole('button', { name: '打开 K 线' }))
    expect(onNavigate).toHaveBeenCalledWith('/portfolio?view=kline&symbol=600519&market=CN')
  })

  it('rejects an external navigation action', async () => {
    const user = userEvent.setup()
    const onNavigate = vi.fn()
    render(
      <AssistantResultCard
        result={{
          ...result,
          next_actions: [{
            ...result.next_actions[0],
            payload: { path: 'https://example.com/portfolio' },
          }],
        }}
        onPrefill={vi.fn()}
        onNavigate={onNavigate}
        onSubmitPrompt={vi.fn()}
      />,
    )

    await user.click(screen.getByRole('button', { name: '打开 K 线' }))
    expect(onNavigate).not.toHaveBeenCalled()
  })
})
