import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { TraceTimeline } from '@/components/assistant/TraceTimeline'

describe('TraceTimeline', () => {
  it('renders factual runtime events without reasoning content', () => {
    render(
      <TraceTimeline
        events={[
          { event: 'run_started', data: { task_id: 7 } },
          { event: 'context_prepared', data: { compressed: true } },
          { event: 'tool_call_start', data: { name: 'get_portfolio', arguments: { market: 'CN' } } },
          { event: 'tool_result', data: { name: 'get_portfolio', ok: true, preview: '持仓查询完成' } },
          { event: 'done', data: {} },
        ]}
      />,
    )

    expect(screen.getByTestId('assistant-trace')).toBeTruthy()
    expect(screen.getByText(/已完成/)).toBeTruthy()
    expect(screen.queryByText('上下文已压缩并准备')).toBeNull()
    expect(screen.queryByText('调用工具：get_portfolio')).toBeNull()
    expect(screen.queryByText(/思考过程|chain of thought/i)).toBeNull()
  })

  it('expands the factual steps from the compact summary', async () => {
    const user = userEvent.setup()
    render(
      <TraceTimeline
        events={[
          { event: 'tool_call_start', data: { name: 'get_portfolio', arguments: { market: 'CN' } } },
          { event: 'tool_result', data: { name: 'get_portfolio', ok: true, preview: '持仓查询完成' } },
          { event: 'done', data: {} },
        ]}
      />,
    )

    await user.click(screen.getByRole('button', { name: /执行记录/ }))

    expect(screen.getByText('调用工具：get_portfolio')).toBeTruthy()
    expect(screen.getByText('{"market":"CN"}')).toBeTruthy()
    expect(screen.getByText('持仓查询完成')).toBeTruthy()
  })
})
