import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'

import { chatApi } from '@panwatch/api'
import AssistantPage from '@/pages/Assistant'

vi.mock('@panwatch/api', () => ({
  chatApi: {
    listConversations: vi.fn(),
    getConversation: vi.fn(),
    getAssistantTask: vi.fn().mockResolvedValue({
      conversation_id: 2,
      status: 'completed',
      pending_approvals: [],
    }),
    getSuggestedQuestions: vi.fn().mockResolvedValue({ questions: [] }),
    createConversation: vi.fn(),
    sendAssistantMessageStream: vi.fn(),
    sendMessageStream: vi.fn(),
    sendMessage: vi.fn(),
    deleteConversation: vi.fn(),
    renameConversation: vi.fn(),
    getAgentPermissions: vi.fn().mockResolvedValue({ defaults: [], tools: [] }),
    updateAgentPermission: vi.fn(),
    decideAssistantApprovalStream: vi.fn(),
  },
}))

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

function BackButton() {
  const navigate = useNavigate()
  return <button onClick={() => navigate(-1)}>后退</button>
}

function renderAssistant(initialEntry: string | { pathname: string; state?: unknown }) {
  return render(
    (
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/assistant" element={<AssistantPage />} />
        <Route path="/assistant/:conversationId" element={<AssistantPage />} />
      </Routes>
      <LocationProbe />
      <BackButton />
    </MemoryRouter>
    ),
  )
}

const conversations = [
  {
    id: 1,
    title: '第一会话',
    stock_symbol: null,
    stock_market: null,
    created_at: '2026-09-12T00:00:00Z',
  },
  {
    id: 2,
    title: '第二会话',
    stock_symbol: null,
    stock_market: null,
    created_at: '2026-09-12T00:00:00Z',
  },
]

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(chatApi.listConversations).mockResolvedValue(conversations)
  vi.mocked(chatApi.getConversation).mockImplementation(async (id) => ({
    conversation: conversations.find((item) => item.id === id) || conversations[0],
    messages: [{
      id: id * 10,
      role: 'assistant',
      content: `会话 ${id} 的消息`,
      created_at: '2026-09-12T00:00:00Z',
    }],
  }))
})

describe('assistant conversation routing', () => {
  it('updates a renamed title immediately and retains it on a list refresh', async () => {
    const user = userEvent.setup()
    vi.mocked(chatApi.renameConversation).mockResolvedValue({ ...conversations[0], title: '我的持仓计划', title_source: 'manual' })
    renderAssistant('/assistant/1')
    await user.click(await screen.findByRole('button', { name: '会话操作：第一会话' }))
    await user.click(screen.getByRole('button', { name: '重命名' }))
    const input = await screen.findByRole('textbox', { name: '会话标题' })
    await user.clear(input); await user.type(input, '我的持仓计划')
    vi.mocked(chatApi.listConversations).mockResolvedValue([{ ...conversations[0], title: '我的持仓计划', title_source: 'manual' }, conversations[1]])
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByRole('button', { name: '我的持仓计划' })).toBeTruthy()
    expect(chatApi.renameConversation).toHaveBeenCalledWith(1, '我的持仓计划')
  })

  it('refreshes a generated title when background assistant activity changes', async () => {
    renderAssistant('/assistant/1')
    await screen.findByRole('button', { name: '第一会话' })
    vi.mocked(chatApi.listConversations).mockResolvedValue([{ ...conversations[0], title: '自动总结的主题', title_source: 'automatic' }, conversations[1]])
    window.dispatchEvent(new Event('panwatch:assistant-activity-changed'))
    expect(await screen.findByRole('button', { name: '自动总结的主题' })).toBeTruthy()
  })
  it('pushes an existing conversation into the URL so browser back returns to the home route', async () => {
    const user = userEvent.setup()
    renderAssistant('/assistant')

    await user.click(await screen.findByRole('button', { name: '第一会话', exact: true }))
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/assistant/1'))

    await user.click(screen.getByRole('button', { name: '后退' }))
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/assistant'))
  })

  it('rehydrates a conversation when the assistant page is opened with its URL', async () => {
    renderAssistant('/assistant/2')

    expect(await screen.findByText('会话 2 的消息')).toBeTruthy()
    expect(screen.getByTestId('location').textContent).toBe('/assistant/2')
  })

  it('opens a stock context handed off by the app shell into a new assistant conversation', async () => {
    vi.mocked(chatApi.createConversation).mockResolvedValue({
      id: 3,
      title: '',
      stock_symbol: '600519',
      stock_market: 'CN',
      created_at: '2026-09-12T00:00:00Z',
    })

    renderAssistant({
      pathname: '/assistant',
      state: {
        assistantContext: {
          symbol: '600519',
          market: 'CN',
          stockName: '贵州茅台',
          pageContext: '行情上下文',
        },
      },
    })

    await waitFor(() => expect(chatApi.createConversation).toHaveBeenCalledWith({
      stock_symbol: '600519',
      stock_market: 'CN',
      initial_context: '行情上下文',
    }))
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/assistant/3'))
  })
})
