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
  it('shows source trading date, market status and evidence for a diagnosis judgment', async () => {
    const user = userEvent.setup()
    render(<AssistantResultCard result={{ ...result,
      judgments: [{ step_id: '1', title: '茅台趋势', text: '历史报价无法证明当前趋势', status: 'completed', evidence_ids: ['ev-1'] }],
      evidence: [{ ...result.evidence[0], data_at: '2026-09-29', quote_date: '2026-09-29', market_status: 'closed', freshness: 'stale', freshness_basis: 'quote_date' }],
    }} onPrefill={vi.fn()} onNavigate={vi.fn()} onSubmitPrompt={vi.fn()} />)
    expect(screen.getByText('茅台趋势')).toBeTruthy()
    expect(screen.getByText('历史报价无法证明当前趋势')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: /依据/ }))
    expect(screen.getByText('报价交易日 2026-09-29')).toBeTruthy()
    expect(screen.getByText('市场状态 closed')).toBeTruthy()
    expect(screen.getByText('数据截至 2026-09-29')).toBeTruthy()
  })

  it('labels discovery and local snapshots while retaining unknown quote time', async () => {
    const user = userEvent.setup()
    render(<AssistantResultCard result={{ ...result, evidence: [
      { ...result.evidence[0], id: 'discovery', tool_name: 'tool_search', source_name: 'tool_search',
        evidence_kind: 'tool_discovery', data_at: null, freshness: 'unknown' },
      { ...result.evidence[0], id: 'rules', tool_name: 'get_price_alerts', source_name: '规则',
        evidence_kind: 'local_snapshot', freshness: 'unknown' },
      { ...result.evidence[0], id: 'quote', data_at: null, freshness: 'unknown' },
    ] }} onPrefill={vi.fn()} onNavigate={vi.fn()} onSubmitPrompt={vi.fn()} />)
    await user.click(screen.getByRole('button', { name: /依据/ }))
    expect(screen.getByText('工具发现')).toBeTruthy()
    expect(screen.getByText('查询快照')).toBeTruthy()
    expect(screen.getByText(/执行于 .*2026/)).toBeTruthy()
    expect(screen.getByText(/查询于 .*2026/)).toBeTruthy()
    expect(screen.getAllByText('时间未知')).toHaveLength(1)
    expect(screen.queryByText('较新')).toBeNull()
    expect(screen.queryByText(/数据截至/)).toBeNull()
  })

  it('does not replace a historical snapshot timestamp with the current date', async () => {
    const user = userEvent.setup()
    render(<AssistantResultCard result={{ ...result, evidence: [{ ...result.evidence[0],
      tool_name: 'get_price_alerts', evidence_kind: 'local_snapshot', data_at: '2021-02-03',
      observed_at: '2026-09-29T01:00:00Z', freshness: 'stale',
    }] }} onPrefill={vi.fn()} onNavigate={vi.fn()} onSubmitPrompt={vi.fn()} />)
    await user.click(screen.getByRole('button', { name: /依据/ }))
    expect(screen.getByText('查询于 2021-02-03')).toBeTruthy()
    expect(screen.queryByText('可能过期')).toBeNull()
  })

})
