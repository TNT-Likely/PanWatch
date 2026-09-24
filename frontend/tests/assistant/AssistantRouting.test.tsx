import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
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
    getAgentPermissions: vi.fn().mockResolvedValue({ defaults: [], tools: [] }),
    updateAgentPermission: vi.fn(),
    decideAssistantApprovalStream: vi.fn(),
  },
}))

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

function StateProbe() {
  const location = useLocation()
  return <output data-testid="location-state">{JSON.stringify(location.state)}</output>
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
      <StateProbe />
      <BackButton />
    </MemoryRouter>
    ),
  )
}

function renderAssistantStrict(initialEntry: string | { pathname: string; state?: unknown }) {
  return render(
    (
    <StrictMode>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/assistant" element={<AssistantPage />} />
          <Route path="/assistant/:conversationId" element={<AssistantPage />} />
        </Routes>
        <LocationProbe />
        <StateProbe />
      </MemoryRouter>
    </StrictMode>
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

  it('creates only one conversation under StrictMode double-invoked effects', async () => {
    // 回归:StrictMode 对挂载 effect 执行「跑→cleanup→再跑」,旧实现无防重入守卫,
    // 会一次点击建出两个会话(第一个因 cleanup 丢弃响应沦为孤儿)。
    vi.mocked(chatApi.createConversation).mockResolvedValue({
      id: 3,
      title: '',
      stock_symbol: '600519',
      stock_market: 'CN',
      created_at: '2026-09-12T00:00:00Z',
    })

    renderAssistantStrict({
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

    await waitFor(() => expect(chatApi.createConversation).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/assistant/3'))
    // 让所有微任务排空后,严格断言只创建了一次
    await new Promise((r) => setTimeout(r, 0))
    expect(chatApi.createConversation).toHaveBeenCalledTimes(1)
  })

  it('strips the handoff state after consumption so remounts cannot re-create', async () => {
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

    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/assistant/3'))
    await waitFor(() =>
      expect(screen.getByTestId('location-state').textContent).toBe('null'),
    )
  })
})
