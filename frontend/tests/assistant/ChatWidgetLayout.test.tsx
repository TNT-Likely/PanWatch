import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { chatApi } from '@panwatch/api'
import ChatWidget from '@/components/ChatWidget'

vi.mock('@panwatch/api', () => ({
  chatApi: {
    listConversations: vi.fn().mockResolvedValue([]),
    createConversation: vi.fn().mockResolvedValue({
      id: 1,
      title: '',
      stock_symbol: null,
      stock_market: null,
      created_at: '2026-09-12T00:00:00Z',
    }),
    getAssistantTask: vi.fn().mockResolvedValue({
      conversation_id: 1,
      status: 'completed',
      pending_approvals: [],
    }),
    sendMessage: vi.fn(),
    sendAssistantMessageStream: vi.fn().mockResolvedValue(undefined),
    decideAssistantApprovalStream: vi.fn().mockResolvedValue(undefined),
  },
}))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ChatWidget layout', () => {
  it('uses the sidebar new research entry instead of a duplicate header plus', async () => {
    render(<ChatWidget embedded />)

    await waitFor(() => expect(screen.getByRole('button', { name: '新研究' })).toBeTruthy())
    expect(screen.queryByRole('button', { name: '新建对话' })).toBeNull()
  })

  it('keeps the composer at the bottom while only the message list scrolls', async () => {
    const user = userEvent.setup()

    render(<ChatWidget embedded />)
    await user.click(screen.getByRole('button', { name: '诊断我的持仓' }))

    await waitFor(() => expect(screen.getByPlaceholderText('输入问题...')).toBeTruthy())

    const shell = screen.getByTestId('assistant-shell')
    const messageList = screen.getByTestId('assistant-message-list')
    const composer = screen.getByTestId('assistant-composer')

    expect(shell.className).toContain('min-h-0')
    expect(shell.className).toContain('h-[calc(100dvh-8rem)]')
    expect(messageList.className).toContain('min-h-0')
    expect(messageList.className).toContain('overflow-y-auto')
    expect(composer.className).toContain('shrink-0')
  })

  it('ignores a second send fired before the first request updates React state', async () => {
    render(<ChatWidget embedded />)
    const quickQuestion = await screen.findByRole('button', { name: '诊断我的持仓' })

    fireEvent.click(quickQuestion)
    fireEvent.click(quickQuestion)

    await waitFor(() => expect(chatApi.createConversation).toHaveBeenCalledTimes(1))
    expect(chatApi.sendAssistantMessageStream).toHaveBeenCalledTimes(1)
  })

  it('shows a prominent centered control when the reader scrolls away from the latest message', async () => {
    const user = userEvent.setup()

    render(<ChatWidget embedded />)
    await user.click(screen.getByRole('button', { name: '诊断我的持仓' }))
    const messageList = await screen.findByTestId('assistant-message-list')

    Object.defineProperties(messageList, {
      scrollHeight: { configurable: true, value: 1000, writable: true },
      scrollTop: { configurable: true, value: 0, writable: true },
      clientHeight: { configurable: true, value: 500, writable: true },
    })
    fireEvent.scroll(messageList)

    const scrollButton = await screen.findByRole('button', { name: '回到底部' })
    expect(scrollButton.textContent).toContain('回到底部')
    expect(scrollButton.className).toContain('left-1/2')
    expect(scrollButton.className).toContain('h-10')

    await user.click(scrollButton)
    expect(screen.queryByRole('button', { name: '回到底部' })).toBeNull()
  })

  it('renders GFM table syntax as a semantic table in assistant answers', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.sendAssistantMessageStream).mockImplementation(async (_conversationId, _content, callbacks) => {
      callbacks.onRunStarted?.({ taskId: 43 })
      callbacks.onDone?.({
        message_id: 44,
        content: '| 标的 | 涨跌幅 |\n| --- | ---: |\n| 贵州茅台 | +1.2% |',
        created_at: '2026-09-12T00:00:00Z',
      })
    })

    render(<ChatWidget embedded />)
    await user.click(screen.getByRole('button', { name: '诊断我的持仓' }))

    const table = await screen.findByRole('table')
    expect(table).toBeTruthy()
    expect(screen.getByRole('columnheader', { name: '标的' })).toBeTruthy()
    expect(screen.getByRole('cell', { name: '贵州茅台' })).toBeTruthy()
    expect(screen.getByRole('cell', { name: '+1.2%' })).toBeTruthy()
  })

  it('keeps a write failure recoverable without adding a generic error bubble', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.sendAssistantMessageStream).mockImplementation(async (_conversationId, _content, callbacks) => {
      callbacks.onRunStarted?.({ taskId: 45 })
      callbacks.onActionStatus?.({
        status: 'needs_retry',
        message: '我还没有执行这次修改，请确认目标后重试。',
        retryable: true,
      })
      callbacks.onError?.('我还没有执行这次修改，请确认目标后重试。')
      throw new Error('我还没有执行这次修改，请确认目标后重试。')
    })

    render(<ChatWidget embedded />)
    await user.click(screen.getByRole('button', { name: '诊断我的持仓' }))

    expect(await screen.findByRole('status', { name: '尚未执行' })).toBeTruthy()
    expect(screen.queryByText('请求未完成：我还没有执行这次修改，请确认目标后重试。')).toBeNull()

    await user.click(screen.getByRole('button', { name: '重试执行' }))
    expect(screen.getByDisplayValue('诊断我的持仓风险和关键关注点')).toBeTruthy()
  })

  it('does not downgrade the embedded assistant to the legacy non-streaming endpoint', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.sendAssistantMessageStream).mockRejectedValueOnce(new Error('SSE unavailable'))

    render(<ChatWidget embedded />)
    await user.click(screen.getByRole('button', { name: '诊断我的持仓' }))

    await waitFor(() => expect((screen.getByPlaceholderText('输入问题...') as HTMLInputElement).disabled).toBe(false))
    expect(screen.queryByText(/请求未完成/)).toBeNull()
    expect(chatApi.sendMessage).not.toHaveBeenCalled()
  })

  it('keeps the first card visible as completed while the next approval remains pending', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.sendAssistantMessageStream).mockImplementation(async (_conversationId, _content, callbacks) => {
      callbacks.onRunStarted?.({ taskId: 42 })
      callbacks.onApprovalRequired?.({
        id: 'approval-1',
        tool_title: '创建提醒',
        risk: 'write',
        summary: '创建第一个提醒',
        expires_at: '',
        status: 'pending',
      })
      callbacks.onApprovalRequired?.({
        id: 'approval-2',
        tool_title: '创建提醒',
        risk: 'write',
        summary: '创建第二个提醒',
        expires_at: '',
        status: 'pending',
      })
      callbacks.onPaused?.({ taskId: 42, reason: 'approval_required' })
    })
    vi.mocked(chatApi.decideAssistantApprovalStream).mockImplementation(async (_approvalId, _decision, callbacks) => {
      callbacks.onToolResult?.({ name: 'create_price_alert', ok: true, preview: '已创建第一个提醒' })
      callbacks.onPaused?.({
        taskId: 42,
        reason: 'approval_required',
        resolvedApprovalId: 'approval-1',
        resolvedStatus: 'approved',
      })
    })

    render(<ChatWidget embedded />)
    await user.click(screen.getByRole('button', { name: '诊断我的持仓' }))
    await screen.findByText('创建第一个提醒')
    await screen.findByText('创建第二个提醒')

    await user.click(screen.getAllByRole('button', { name: '本次允许' })[0])

    await screen.findByText('已允许，已执行')
    expect(screen.getAllByRole('button', { name: '本次允许' })).toHaveLength(1)
    expect(chatApi.decideAssistantApprovalStream).toHaveBeenCalledWith(
      'approval-1',
      'approved',
      expect.any(Object),
    )
  })
})
