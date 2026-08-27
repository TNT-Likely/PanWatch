import { fetchAPI } from './client'
import { readSSE, type SSEEvent } from './sse'

export interface ChatConversation {
  id: number
  title: string
  stock_symbol?: string | null
  stock_market?: string | null
  created_at: string
}

export interface ChatMessage {
  id: number
  role: 'user' | 'assistant' | 'system'
  content: string
  created_at: string
}

export interface ConversationDetail {
  conversation: ChatConversation
  messages: ChatMessage[]
}

export const chatApi = {
  createConversation: (params?: { stock_symbol?: string; stock_market?: string; initial_context?: string }) =>
    fetchAPI<ChatConversation>('/chat/conversations', {
      method: 'POST',
      body: JSON.stringify(params || {}),
    }),

  listConversations: (limit = 30) =>
    fetchAPI<ChatConversation[]>(`/chat/conversations?limit=${limit}`),

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

  getSuggestedQuestions: (symbol: string, market: string) =>
    fetchAPI<{ questions: string[] }>(
      `/chat/suggested-questions?symbol=${encodeURIComponent(symbol)}&market=${encodeURIComponent(market)}`
    ),

  sendMessageStream,
}

export interface ChatStreamCallbacks {
  /** token 增量文本 */
  onToken?: (text: string) => void
  /** 模型开始调用工具（前端应清空当前 token 缓冲并展示"正在查询…"） */
  onToolCallStart?: (info: { name: string; arguments: Record<string, unknown> }) => void
  /** 工具执行完成 */
  onToolResult?: (info: { name: string; ok: boolean; preview: string }) => void
  /** 计划驱动(全面诊断持仓):计划生成/步骤推进/完成 */
  onPlan?: (info: {
    status: string
    steps: { id: number; title: string; status: string }[]
    current?: number
  }) => void
  /** 最终回答（已落库） */
  onDone?: (msg: { message_id: number; content: string; created_at: string }) => void
  /** AI 服务异常（服务端已把错误文案落库） */
  onError?: (message: string) => void
}

const CHAT_STREAM_MAX_RECONNECTS = 3

/**
 * 流式发送消息（SSE）。
 *
 * - 首次连接 POST /chat/conversations/{id}/messages/stream；
 * - meta 事件携带 stream_id，之后若连接中断（生成仍在服务端继续），
 *   自动经 GET /chat/streams/{stream_id} + Last-Event-ID 续推；
 * - 若首次连接直接失败（未收到任何事件），抛异常，调用方降级到非流式 sendMessage。
 */
async function sendMessageStream(
  conversationId: number,
  content: string,
  callbacks: ChatStreamCallbacks,
  signal?: AbortSignal
): Promise<void> {
  let streamId = ''
  let lastEventId = 0
  let finished = false

  const handleEvent = (ev: SSEEvent) => {
    if (ev.id > 0) lastEventId = ev.id
    const d = ev.data || {}
    switch (ev.event) {
      case 'meta':
        streamId = d.stream_id || ''
        break
      case 'token':
        callbacks.onToken?.(d.text || '')
        break
      case 'tool_call_start':
        callbacks.onToolCallStart?.({ name: d.name || '', arguments: d.arguments || {} })
        break
      case 'tool_result':
        callbacks.onToolResult?.({ name: d.name || '', ok: !!d.ok, preview: d.preview || '' })
        break
      case 'plan':
        callbacks.onPlan?.({ status: d.status || '', steps: d.steps || [], current: d.current })
        break
      case 'done':
        finished = true
        callbacks.onDone?.({
          message_id: d.message_id || 0,
          content: d.content || '',
          created_at: d.created_at || '',
        })
        break
      case 'error':
        callbacks.onError?.(d.message || '未知错误')
        break
    }
  }

  await readSSE(`/chat/conversations/${conversationId}/messages/stream`, {
    method: 'POST',
    body: { content },
    signal,
    onEvent: handleEvent,
  })

  // 连接被中断但生成未结束 → 经续推端点接回（服务端缓冲全量事件）
  let reconnects = 0
  while (!finished && streamId && reconnects < CHAT_STREAM_MAX_RECONNECTS) {
    if (signal?.aborted) return
    reconnects += 1
    try {
      await readSSE(`/chat/streams/${streamId}`, {
        signal,
        lastEventId,
        onEvent: handleEvent,
      })
    } catch {
      // 退避后再试
      await new Promise((r) => setTimeout(r, 1000 * reconnects))
    }
  }

  if (!finished) throw new Error('流式回复未完成')
}
