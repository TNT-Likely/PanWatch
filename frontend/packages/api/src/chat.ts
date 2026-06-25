import { fetchAPI } from './client'

export interface ChatPendingAction {
  id: string
  type: 'create_price_alert' | 'add_position' | 'reduce_position'
  preview: {
    title?: string
    lines?: string[]
    warnings?: string[]
  }
  status: 'pending' | 'confirmed' | 'cancelled' | 'expired' | 'failed'
  result?: Record<string, unknown> | null
  expires_at?: string
  created_at?: string
}

export interface ChatConversation {
  id: number
  title: string
  stock_symbol?: string | null
  stock_market?: string | null
  created_at: string
  updated_at?: string
}

export interface ChatMessage {
  id: number
  role: 'user' | 'assistant' | 'system'
  content: string
  created_at: string
  pending_actions?: ChatPendingAction[]
}

export interface ConversationDetail {
  conversation: ChatConversation
  messages: ChatMessage[]
}

export interface ChatActionResult {
  ok: boolean
  action: ChatPendingAction
  result?: Record<string, unknown>
}

export const chatApi = {
  createConversation: (params?: { stock_symbol?: string; stock_market?: string; initial_context?: string }) =>
    fetchAPI<ChatConversation>('/chat/conversations', {
      method: 'POST',
      body: JSON.stringify(params || {}),
    }),

  listConversations: (limit = 30) =>
    fetchAPI<ChatConversation[]>(`/chat/conversations?limit=${limit}`),

  findRecentConversations: (symbol: string, market: string, limit = 1) =>
    fetchAPI<ChatConversation[]>(
      `/chat/conversations/recent?symbol=${encodeURIComponent(symbol)}&market=${encodeURIComponent(market)}&limit=${limit}`,
    ),

  getConversation: (id: number) =>
    fetchAPI<ConversationDetail>(`/chat/conversations/${id}`),

  deleteConversation: (id: number) =>
    fetchAPI<{ ok: boolean }>(`/chat/conversations/${id}`, {
      method: 'DELETE',
    }),

  sendMessage: (conversationId: number, content: string) =>
    fetchAPI<ChatMessage>(`/chat/conversations/${conversationId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content }),
      timeoutMs: 120000,
    }),

  confirmAction: (actionId: string) =>
    fetchAPI<ChatActionResult>(`/chat/actions/${actionId}/confirm`, {
      method: 'POST',
    }),

  cancelAction: (actionId: string) =>
    fetchAPI<{ ok: boolean; action: ChatPendingAction }>(`/chat/actions/${actionId}/cancel`, {
      method: 'POST',
    }),

  getSuggestedQuestions: (symbol: string, market: string) =>
    fetchAPI<{ questions: string[] }>(
      `/chat/suggested-questions?symbol=${encodeURIComponent(symbol)}&market=${encodeURIComponent(market)}`
    ),
}
