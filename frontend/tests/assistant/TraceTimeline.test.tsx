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
          { event: 'model_usage', data: { input_tokens: 120, output_tokens: 30 } },
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

  it('shows provider token usage as a factual runtime event', async () => {
    const user = userEvent.setup()
    render(
      <TraceTimeline
        events={[
          { event: 'model_usage', data: { input_tokens: 120, output_tokens: 30 } },
          { event: 'done', data: {} },
        ]}
      />,
    )

    await user.click(screen.getByRole('button', { name: /执行记录/ }))
    expect(screen.getByText('模型用量：输入 120，输出 30')).toBeTruthy()
  })

  it('shows task duration and token totals in the trace summary', async () => {
    const user = userEvent.setup()
    render(
      <TraceTimeline
        events={[
          { event: 'model_usage', data: { input_tokens: 120, output_tokens: 30, duration_ms: 800 } },
          { event: 'tool_call_start', data: { name: 'get_portfolio' } },
          { event: 'tool_result', data: { name: 'get_portfolio', ok: true, duration_ms: 420 } },
          { event: 'done', data: { duration_ms: 1240 } },
        ]}
      />,
    )

    expect(screen.getByText('已完成 · 1.2s · 1 次工具调用 · 150 tokens')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: /执行记录/ }))
    expect(screen.getByText('模型用量：输入 120，输出 30 · 800ms')).toBeTruthy()
    expect(screen.getByText('工具完成：get_portfolio · 420ms')).toBeTruthy()
  })

  it('expands the factual steps from the compact summary', async () => {
    const user = userEvent.setup()
    render(
      <TraceTimeline
        events={[
          { event: 'tool_call_start', data: { name: 'get_portfolio', arguments: { market: 'CN' } } },
          { event: 'tool_result', data: { name: 'get_portfolio', ok: true, preview: '持仓查询完成' } },
          {
            event: 'extension_event',
            data: {
              extension: 'tool_research',
              event: 'completed',
              data: { selected_tools: ['get_portfolio'] },
            },
          },
          { event: 'done', data: {} },
        ]}
      />,
    )

    await user.click(screen.getByRole('button', { name: /执行记录/ }))

    expect(screen.getByText('调用工具：get_portfolio')).toBeTruthy()
    expect(screen.getByText('{"market":"CN"}')).toBeTruthy()
    expect(screen.getByText('持仓查询完成')).toBeTruthy()
    expect(screen.getByText('工具研究完成：选出 1 个')).toBeTruthy()
  })

  it('distinguishes tool exposure and model-side search from execution', async () => {
    const user = userEvent.setup()
    render(
      <TraceTimeline
        events={[
          {
            event: 'extension_event',
            data: {
              extension: 'tool_research',
              event: 'exposure',
              data: { direct_tools: ['get_quote'], loaded_tools: [] },
            },
          },
          {
            event: 'extension_event',
            data: {
              extension: 'tool_research',
              event: 'searched',
              data: { selected_tools: ['get_fundamentals'] },
            },
          },
          { event: 'tool_call_start', data: { name: 'get_fundamentals', arguments: {} } },
          { event: 'done', data: {} },
        ]}
      />,
    )

    await user.click(screen.getByRole('button', { name: /执行记录/ }))

    expect(screen.getByText('工具目录已准备：1 个直达，0 个已加载')).toBeTruthy()
    expect(screen.getByText('工具搜索完成：加载 1 个')).toBeTruthy()
    expect(screen.getByText('调用工具：get_fundamentals')).toBeTruthy()
  })
})
