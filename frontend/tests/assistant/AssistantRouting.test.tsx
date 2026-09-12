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

function renderAssistant(initialEntry: string) {
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
})
