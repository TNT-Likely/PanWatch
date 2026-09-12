import { render, screen } from '@testing-library/react'
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
    expect(screen.getByText('上下文已压缩并准备')).toBeTruthy()
    expect(screen.getByText('调用工具：get_portfolio')).toBeTruthy()
    expect(screen.getByText('{"market":"CN"}')).toBeTruthy()
    expect(screen.getByText('持仓查询完成')).toBeTruthy()
    expect(screen.queryByText(/思考过程|chain of thought/i)).toBeNull()
  })
})
