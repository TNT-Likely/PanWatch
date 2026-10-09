import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { chatApi, fetchAPI } from '@panwatch/api'
import ChatWidget from '@/components/ChatWidget'
import i18n from '@/i18n'

vi.mock('@panwatch/api', () => ({
  fetchAPI: vi.fn(),
  chatApi: {
    listConversations: vi.fn().mockResolvedValue([]),
    createConversation: vi.fn().mockResolvedValue({
      id: 1,
      title: '',
      stock_symbol: null,
      stock_market: null,
      created_at: '2026-09-12T00:00:00Z',
    }),
    getConversation: vi.fn().mockResolvedValue({
      conversation: { id: 1, title: '', stock_symbol: null, stock_market: null, created_at: '2026-09-12T00:00:00Z' },
      messages: [],
    }),
    getAssistantTask: vi.fn().mockResolvedValue({
      conversation_id: 1,
      status: 'completed',
      pending_approvals: [],
    }),
    subscribeAssistantTaskStream: vi.fn().mockResolvedValue(undefined),
    sendMessage: vi.fn(),
    sendAssistantMessageStream: vi.fn().mockResolvedValue(undefined),
    decideAssistantApprovalStream: vi.fn().mockResolvedValue(undefined),
    cancelAssistantTask: vi.fn(),
    retryAssistantTask: vi.fn(),
  },
}))

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(chatApi.listConversations).mockResolvedValue([])
  vi.mocked(chatApi.createConversation).mockResolvedValue({ id: 1, title: '', stock_symbol: null, stock_market: null, created_at: '2026-09-12T00:00:00Z' })
  vi.mocked(chatApi.getConversation).mockResolvedValue({
    conversation: { id: 1, title: '', stock_symbol: null, stock_market: null, created_at: '2026-09-12T00:00:00Z' }, messages: [],
  })
  vi.mocked(chatApi.getAssistantTask).mockRejectedValue(new Error('No task fixture'))
  vi.mocked(chatApi.subscribeAssistantTaskStream).mockResolvedValue(undefined)
  vi.mocked(chatApi.sendAssistantMessageStream).mockResolvedValue(undefined)
  vi.mocked(chatApi.decideAssistantApprovalStream).mockResolvedValue(undefined)
  sessionStorage.clear()
})

describe('ChatWidget layout', () => {
  it.each([
    ['zh-CN', 'tool_search', '正在查找可用工具…'],
    ['en-US', 'tool_search', 'Finding available tools…'],
    ['zh-CN', 'search_stocks', '正在调用 股票搜索…'],
    ['zh-CN', 'custom_research_tool', '正在调用 custom_research_tool…'],
    ['en-US', 'custom_research_tool', 'Calling custom_research_tool…'],
  ])('localizes live tool status in %s for %s', async (language, name, expected) => {
    await i18n.changeLanguage(language)
    vi.mocked(chatApi.sendAssistantMessageStream).mockImplementation(async (_conversationId, _content, callbacks, signal) => {
      callbacks.onRunStarted?.({ taskId: 43 })
      callbacks.onToolCallStart?.({ name, arguments: {} })
      await new Promise<void>(resolve => signal?.addEventListener('abort', () => resolve(), { once: true }))
    })
    const user = userEvent.setup()
    render(<ChatWidget embedded />)
    await user.click(screen.getByRole('button', { name: i18n.t('assistantPage.welcome.diagnosePortfolio', { ns: 'configuration' }) }))
    expect(await screen.findByText(expected)).toBeTruthy()
  })

  it('reconnects a running durable task after a refresh', async () => {
    sessionStorage.setItem('panwatch:assistant-task:1', '88')
    vi.mocked(chatApi.getAssistantTask)
      .mockResolvedValueOnce({
        id: 88,
        conversation_id: 1,
        status: 'running',
        pending_approvals: [],
      })
      .mockResolvedValueOnce({
        id: 88,
        conversation_id: 1,
        status: 'completed',
        pending_approvals: [],
      })
    vi.mocked(chatApi.getConversation).mockResolvedValue({
      conversation: { id: 1, title: '恢复任务', stock_symbol: null, stock_market: null, created_at: '2026-09-12T00:00:00Z' },
      messages: [{ id: 188, role: 'assistant', content: '后台任务已完成', created_at: '2026-09-12T00:00:00Z' }],
    })
    vi.mocked(chatApi.subscribeAssistantTaskStream).mockImplementation(async (_taskId, callbacks) => {
      callbacks.onToolCallStart?.({ name: 'create_price_alert', arguments: {} })
      callbacks.onDone?.({ message_id: 188, content: '后台任务已完成', created_at: '2026-09-12T00:00:00Z' })
    })

    render(<ChatWidget embedded conversationIdFromUrl={1} onConversationChange={vi.fn()} />)

    await waitFor(() => expect(chatApi.subscribeAssistantTaskStream).toHaveBeenCalledWith(
      88,
      expect.any(Object),
      expect.any(AbortSignal),
      0,
    ))
    await screen.findByText('后台任务已完成')
    await waitFor(() => expect(sessionStorage.getItem('panwatch:assistant-task:1')).toBeNull())
  })

  it('does not restore an approval from a conversation that was left before the response arrived', async () => {
    let resolveTask: ((value: unknown) => void) | undefined
    vi.mocked(chatApi.listConversations).mockResolvedValue([
      { id: 1, title: '旧会话', stock_symbol: null, stock_market: null, created_at: '2026-09-12T00:00:00Z' },
      { id: 2, title: '新会话', stock_symbol: null, stock_market: null, created_at: '2026-09-12T00:00:00Z' },
    ])
    vi.mocked(chatApi.getConversation).mockImplementation(async (id) => ({
      conversation: { id, title: id === 1 ? '旧会话' : '新会话', stock_symbol: null, stock_market: null, created_at: '2026-09-12T00:00:00Z' },
      messages: [{ id: id * 10, role: 'assistant', content: `会话 ${id}`, created_at: '2026-09-12T00:00:00Z' }],
    }))
    vi.mocked(chatApi.getAssistantTask).mockImplementationOnce(() => new Promise((resolve) => {
      resolveTask = resolve
    }))
    sessionStorage.setItem('panwatch:assistant-task:1', '99')

    const onConversationChange = vi.fn()
    const { rerender } = render(
      <ChatWidget embedded conversationIdFromUrl={1} onConversationChange={onConversationChange} />,
    )
    await screen.findByText('会话 1')

    rerender(<ChatWidget embedded conversationIdFromUrl={2} onConversationChange={onConversationChange} />)
    await screen.findByText('会话 2')

    resolveTask?.({
      id: 99,
      conversation_id: 1,
      status: 'awaiting_approval',
      pending_approvals: [{
        id: 'old-approval',
        tool_name: 'delete_price_alert',
        risk: 'write',
        presentation: { tool_title: '旧会话操作', summary: '不应显示' },
        expires_at: '2026-09-12T01:00:00Z',
      }],
    })

    await waitFor(() => expect(screen.queryByText('不应显示')).toBeNull())
    sessionStorage.removeItem('panwatch:assistant-task:1')
  })

  it('uses the sidebar new research entry instead of a duplicate header plus', async () => {
    render(<ChatWidget embedded />)

    await waitFor(() => expect(screen.getByRole('button', { name: '新研究' })).toBeTruthy())
    expect(screen.queryByRole('button', { name: '新建对话' })).toBeNull()
  })

  it('passes the selected stock into the new analysis conversation', async () => {
    const user = userEvent.setup()
    vi.mocked(fetchAPI).mockResolvedValue([
      { symbol: '600519', name: '贵州茅台', market: 'CN' },
    ])

    render(<ChatWidget embedded />)
    await user.click(screen.getByRole('button', { name: '分析一只股票' }))
    await user.type(screen.getByRole('searchbox', { name: '搜索股票' }), '茅台')
    await user.click(await screen.findByRole('button', { name: /贵州茅台/ }))

    await waitFor(() => expect(chatApi.createConversation).toHaveBeenCalledWith({
      stock_symbol: '600519',
      stock_market: 'CN',
      initial_context: undefined,
    }))
    expect(chatApi.sendAssistantMessageStream).toHaveBeenCalledWith(
      1,
      '分析 CN:600519 贵州茅台 的基本面、行情和近期新闻',
      expect.any(Object),
      expect.any(AbortSignal),
    )
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
    expect(shell.className).toContain('h-full')
    expect(messageList.className).toContain('min-h-0')
    expect(messageList.className).toContain('overflow-y-auto')
    expect(composer.className).toContain('shrink-0')
    expect(composer.className).not.toContain('env(safe-area-inset-bottom)')
    expect(composer.className).toContain('sm:px-4')
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
    fireEvent.wheel(messageList, { deltaY: -200 })
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

  it('attaches the completed trace to the assistant response and keeps it collapsed', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.sendAssistantMessageStream).mockImplementation(async (_conversationId, _content, callbacks) => {
      callbacks.onRunStarted?.({ taskId: 46 })
      callbacks.onTrace?.({ event: 'tool_call_start', data: { name: 'get_portfolio', arguments: { market: 'CN' } } })
      callbacks.onTrace?.({ event: 'tool_result', data: { name: 'get_portfolio', ok: true, preview: '持仓查询完成' } })
      callbacks.onDone?.({ message_id: 47, content: '已完成分析', created_at: '2026-09-12T00:00:00Z' })
    })

    render(<ChatWidget embedded />)
    await user.click(screen.getByRole('button', { name: '诊断我的持仓' }))

    await screen.findByText('已完成分析')
    expect(screen.getAllByTestId('assistant-trace')).toHaveLength(1)
    expect(screen.queryByText('正在查询：持仓')).toBeNull()

    await user.click(screen.getByRole('button', { name: /研究进度/ }))
    expect(screen.getByText('正在查询：持仓')).toBeTruthy()
  })

  it('does not render a generic retry card when a stream fails', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.sendAssistantMessageStream).mockImplementation(async (_conversationId, _content, callbacks) => {
      callbacks.onRunStarted?.({ taskId: 45 })
      callbacks.onError?.({
        code: 'required_tool_call_missing',
        message: '助手没有执行写入操作，因为本轮没有收到对应工具的成功结果。',
        retryable: true,
      })
      throw new Error('助手没有执行写入操作，因为本轮没有收到对应工具的成功结果。')
    })

    render(<ChatWidget embedded />)
    await user.click(screen.getByRole('button', { name: '诊断我的持仓' }))

    await waitFor(() => expect(screen.queryByRole('button', { name: '重试执行' })).toBeNull())
    expect(screen.queryByText(/尚未执行/)).toBeNull()
  })

  it('shows the actionable reason when the AI quota is exhausted', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.sendAssistantMessageStream).mockImplementation(async (_conversationId, _content, callbacks) => {
      callbacks.onRunStarted?.({ taskId: 49 })
      callbacks.onError?.({
        code: 'ai_quota_exhausted',
        message: 'provider fallback',
        retryable: false,
      })
      throw new Error('provider raw error')
    })

    render(<ChatWidget embedded />)
    await user.click(screen.getByRole('button', { name: '诊断我的持仓' }))

    expect(await screen.findByText('AI 服务额度已用尽，请充值或切换可用模型后重试。')).toBeTruthy()
    expect(screen.queryByText('provider raw error')).toBeNull()
  })

  it('restores the failure reason for a durable task after refresh', async () => {
    sessionStorage.setItem('panwatch:assistant-task:1', '91')
    vi.mocked(chatApi.getAssistantTask).mockResolvedValue({
      id: 91,
      conversation_id: 1,
      status: 'failed',
      error_code: 'ai_authentication_failed',
      pending_approvals: [],
    })

    render(<ChatWidget embedded conversationIdFromUrl={1} onConversationChange={vi.fn()} />)

    expect(await screen.findByText('AI 服务认证失败，请检查 API Key 是否正确且仍然有效。')).toBeTruthy()
    expect(sessionStorage.getItem('panwatch:assistant-task:1')).toBeNull()
  })

  it('opens AI settings from an authentication failure restored with the conversation', async () => {
    const onNavigate = vi.fn()
    vi.mocked(chatApi.getConversation).mockResolvedValue({
      conversation: { id: 1, title: '', stock_symbol: null, stock_market: null, created_at: '' }, messages: [],
      latest_task: { id: 91, conversation_id: 1, status: 'failed', error_code: 'ai_authentication_failed', can_retry: true, pending_approvals: [] },
    })
    render(<ChatWidget embedded conversationIdFromUrl={1} onNavigate={onNavigate} onConversationChange={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: '检查 AI 设置' }))
    expect(onNavigate).toHaveBeenCalledWith('/settings')
    expect(screen.queryByRole('button', { name: '重试任务' })).toBeNull()
  })

  it('shows operation evidence for a stopped task and prevents replay', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.getConversation).mockResolvedValue({
      conversation: { id: 1, title: '', stock_symbol: null, stock_market: null, created_at: '' }, messages: [],
      latest_task: {
        id: 91, conversation_id: 1, status: 'cancelled', can_retry: false, retry_blocked_reason: 'tools_already_started', pending_approvals: [],
        trace: [{ id: 5, event: 'tool_result', data: { name: 'create_price_alert', ok: true, preview: '创建了提醒 alert-1' } }, { id: 6, event: 'cancelled', data: {} }],
      },
    })
    render(<ChatWidget embedded conversationIdFromUrl={1} onConversationChange={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: '查看已执行操作' }))
    expect(await screen.findByText('创建了提醒 alert-1')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '重试任务' })).toBeNull()
    expect((screen.getByPlaceholderText('输入问题...') as HTMLInputElement).disabled).toBe(false)
  })

  it('prefills the failed question for revision without submitting it again', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.getConversation).mockResolvedValue({
      conversation: { id: 1, title: '', stock_symbol: null, stock_market: null, created_at: '' },
      messages: [{ id: 1, role: 'user', content: '请分析所有股票', created_at: '' }],
      latest_task: { id: 91, conversation_id: 1, status: 'failed', error_code: 'step_limit', can_retry: true, pending_approvals: [] },
    })
    render(<ChatWidget embedded conversationIdFromUrl={1} onConversationChange={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: '修改问题' }))
    expect((screen.getByPlaceholderText('输入问题...') as HTMLInputElement).value).toBe('请分析所有股票')
    expect(chatApi.sendAssistantMessageStream).not.toHaveBeenCalled()
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

    await screen.findByText('已允许')
    expect(screen.getAllByRole('button', { name: '本次允许' })).toHaveLength(1)
    expect(chatApi.decideAssistantApprovalStream).toHaveBeenCalledWith(
      'approval-1',
      'approved',
      expect.any(Object),
      42,
      expect.any(AbortSignal),
      0,
    )
  })
})
