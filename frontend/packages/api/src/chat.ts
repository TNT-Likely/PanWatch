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

export interface AssistantApproval {
  id: string
  tool_title: string
  risk: 'read' | 'write' | 'external' | 'destructive'
  summary: string
  expires_at: string
  status: 'pending' | 'approved' | 'rejected'
}

export interface AssistantTaskSnapshot {
  id: number
  conversation_id: number
  status: string
  pending_approvals: Array<{
    id: string
    call_id: string
    tool_name: string
    risk: AssistantApproval['risk']
    arguments: Record<string, unknown>
    presentation: { tool_title?: string; summary?: string }
    expires_at: string
  }>
}

export interface ContextSectionUsage {
  name: string
  tokens: number
  estimated: boolean
}

export interface ContextUsage {
  total_tokens: number
  budget_tokens: number
  soft_limit_tokens: number
  hard_limit_tokens: number
  estimated: boolean
  state: 'normal' | 'warning' | 'needs_compression'
  sections: ContextSectionUsage[]
}

export interface AssistantContextSnapshot {
  version: number
  mode: 'balanced' | 'preserve_details' | 'handoff'
  summary: {
    goal: string[]
    constraints: string[]
    decisions: string[]
    facts: string[]
    current_state: string
    open_items: string[]
    tool_findings: string[]
  }
  covered_until_message_id?: number | null
  source_message_count: number
  usage_before: ContextUsage
  usage_after: ContextUsage
  created_at?: string | null
}

export interface AssistantContextDetail {
  conversation_id: number
  usage: ContextUsage
  snapshot?: AssistantContextSnapshot | null
  compression_available: boolean
  status: ContextUsage['state']
}

export interface AgentPermissions {
  defaults: Array<{ risk: AssistantApproval['risk']; mode: 'allow' | 'ask' | 'deny' }>
  tools: Array<{
    name: string
    title: string
    risk: AssistantApproval['risk']
    mode: 'allow' | 'ask' | 'deny'
    confirmation_required: boolean
  }>
}

export interface AssistantConfigModel {
  id: number
  name: string
  model: string
  service_name: string
}

export interface AssistantConfig {
  compression_model_id: number | null
  compression_temperature: number
  max_tokens: number
  soft_limit_tokens: number
  hard_limit_tokens: number
  keep_recent_messages: number
  models: AssistantConfigModel[]
}

export type AssistantConfigUpdate = Omit<AssistantConfig, 'models'>

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

  getAssistantTask: (taskId: number) =>
    fetchAPI<AssistantTaskSnapshot>('/assistant/tasks/' + taskId),

  getAssistantContext: (conversationId: number) =>
    fetchAPI<AssistantContextDetail>(`/assistant/conversations/${conversationId}/context`),

  compressAssistantContext: (conversationId: number, mode: AssistantContextSnapshot['mode']) =>
    fetchAPI<AssistantContextDetail>(`/assistant/conversations/${conversationId}/context/compress`, {
      method: 'POST',
      body: JSON.stringify({ mode }),
    }),

  getAgentPermissions: () =>
    fetchAPI<AgentPermissions>('/assistant/tool-permissions'),

  updateAgentPermission: (change: {
    selector_kind: 'tool' | 'risk'
    selector_value: string
    mode: 'allow' | 'ask' | 'deny'
    risk?: AssistantApproval['risk']
  }) => fetchAPI<AgentPermissions>('/assistant/tool-permissions', {
    method: 'PUT',
    body: JSON.stringify(change),
  }),

  getAssistantConfig: () => fetchAPI<AssistantConfig>('/assistant/config'),

  updateAssistantConfig: (config: AssistantConfigUpdate) =>
    fetchAPI<AssistantConfig>('/assistant/config', {
      method: 'PUT',
      body: JSON.stringify(config),
    }),

  sendMessageStream,
  sendAssistantMessageStream: (conversationId: number, content: string, callbacks: ChatStreamCallbacks, signal?: AbortSignal) =>
    sendMessageStream(conversationId, content, callbacks, signal, '/assistant/conversations/' + conversationId + '/messages/stream'),
  decideAssistantApprovalStream,
}

export interface ChatStreamCallbacks {
  /** 已接受请求或正在执行的阶段提示 */
  onStatus?: (message: string) => void
  /** Server accepted a durable assistant task. */
  onRunStarted?: (info: { taskId: number; contextUsage?: ContextUsage }) => void
  /** Context was measured and, when needed, compacted before the agent loop. */
  onContextPrepared?: (info: {
    compressed: boolean
    mode: AssistantContextSnapshot['mode']
    usageBefore: ContextUsage
    usageAfter: ContextUsage
    compressedMessageCount: number
  }) => void
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
  /** A host-persisted tool approval is now waiting for a human decision. */
  onApprovalRequired?: (approval: AssistantApproval) => void
  /** The current stream ended normally because its task awaits approval. */
  onPaused?: (info: {
    taskId: number
    reason: string
    resolvedApprovalId?: string
    resolvedStatus?: AssistantApproval['status']
  }) => void
  /** 最终回答（已落库） */
  onDone?: (msg: { message_id: number; content: string; created_at: string }) => void
  /** AI 服务异常（服务端已把错误文案落库） */
  onError?: (message: string) => void
  /** Factual runtime events for the user-facing trace panel. */
  onTrace?: (event: AssistantTraceEvent) => void
}

export interface AssistantTraceEvent {
  event: string
  data: Record<string, any>
  id?: number
}

const TRACE_EVENTS = new Set([
  'run_started',
  'context_prepared',
  'step_updated',
  'tool_call_start',
  'tool_result',
  'approval_required',
  'paused',
  'done',
  'error',
])

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
  signal?: AbortSignal,
  streamPath = `/chat/conversations/${conversationId}/messages/stream`
): Promise<void> {
  let streamId = ''
  let lastEventId = 0
  let finished = false
  let paused = false
  let terminalError = ''

  const handleEvent = (ev: SSEEvent) => {
    if (ev.id > 0) lastEventId = ev.id
    const d = ev.data || {}
    if (TRACE_EVENTS.has(ev.event)) {
      callbacks.onTrace?.({ event: ev.event, data: d, id: ev.id })
    }
    switch (ev.event) {
      case 'meta':
        streamId = d.stream_id || ''
        break
      case 'status':
        callbacks.onStatus?.(d.message || '思考中…')
        break
      case 'run_started':
        callbacks.onRunStarted?.({
          taskId: Number(d.task_id) || 0,
          contextUsage: d.context_usage as ContextUsage | undefined,
        })
        break
      case 'context_prepared':
        callbacks.onContextPrepared?.({
          compressed: !!d.compressed,
          mode: d.mode || 'balanced',
          usageBefore: d.usage_before as ContextUsage,
          usageAfter: d.usage_after as ContextUsage,
          compressedMessageCount: Number(d.compressed_message_count) || 0,
        })
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
      case 'approval_required': {
        const call = d.calls?.[0] || {}
        callbacks.onApprovalRequired?.({
          id: d.approval_id || '',
          tool_title: d.presentation?.tool_title || call.name || '需要确认的工具操作',
          risk: call.risk || 'write',
          summary: d.presentation?.summary || ('请求执行 ' + (call.name || '工具操作')),
          expires_at: d.expires_at || '',
          status: 'pending',
        })
        break
      }
      case 'paused':
        paused = true
        callbacks.onPaused?.({
          taskId: Number(d.task_id) || 0,
          reason: d.reason || '',
          resolvedApprovalId: d.resolved_approval_id || undefined,
          resolvedStatus: d.resolved_status === 'approved' || d.resolved_status === 'rejected'
            ? d.resolved_status
            : undefined,
        })
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
        terminalError = d.message || '未知错误'
        callbacks.onError?.(terminalError)
        break
    }
  }

  await readSSE(streamPath, {
    method: 'POST',
    body: { content },
    signal,
    onEvent: handleEvent,
  })

  // The legacy /api/chat endpoint intentionally emits `error` then persists a
  // fallback response as `done`.  Only a stream that ends without `done` is a
  // terminal failure for the caller.
  if (terminalError && !finished) throw new Error(terminalError)

  // 连接被中断但生成未结束 → 经续推端点接回（服务端缓冲全量事件）
  let reconnects = 0
  while (!finished && !paused && streamId && reconnects < CHAT_STREAM_MAX_RECONNECTS) {
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

  if (!finished && !paused) throw new Error('流式回复未完成')
}

async function decideAssistantApprovalStream(
  approvalId: string,
  decision: 'approved' | 'rejected',
  callbacks: ChatStreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  let finished = false
  let paused = false
  let terminalError = ''

  await readSSE('/assistant/approvals/' + encodeURIComponent(approvalId) + '/decision/stream', {
    method: 'POST',
    body: { decision },
    signal,
    onEvent: (ev) => {
      const d = ev.data || {}
      if (TRACE_EVENTS.has(ev.event)) {
        callbacks.onTrace?.({ event: ev.event, data: d, id: ev.id })
      }
      switch (ev.event) {
        case 'token':
          callbacks.onToken?.(d.text || '')
          break
        case 'tool_call_start':
          callbacks.onToolCallStart?.({ name: d.name || '', arguments: d.arguments || {} })
          break
        case 'tool_result':
          callbacks.onToolResult?.({ name: d.name || '', ok: !!d.ok, preview: d.preview || '' })
          break
        case 'approval_required': {
          const call = d.calls?.[0] || {}
          callbacks.onApprovalRequired?.({
            id: d.approval_id || '',
            tool_title: d.presentation?.tool_title || call.name || '需要确认的工具操作',
            risk: call.risk || 'write',
            summary: d.presentation?.summary || ('请求执行 ' + (call.name || '工具操作')),
            expires_at: d.expires_at || '',
            status: 'pending',
          })
          break
        }
        case 'paused':
          paused = true
          callbacks.onPaused?.({
            taskId: Number(d.task_id) || 0,
            reason: d.reason || '',
            resolvedApprovalId: d.resolved_approval_id || undefined,
            resolvedStatus: d.resolved_status === 'approved' || d.resolved_status === 'rejected'
              ? d.resolved_status
              : undefined,
          })
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
          terminalError = d.message || '未知错误'
          callbacks.onError?.(terminalError)
          break
      }
    },
  })

  if (terminalError && !finished) throw new Error(terminalError)
  if (!finished && !paused) throw new Error('流式回复未完成')
}
