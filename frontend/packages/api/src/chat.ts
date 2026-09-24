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
  /** Runtime facts captured while producing this assistant response. */
  trace?: AssistantTraceEvent[]
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
  model?: string | null
  duration_ms?: number
  usage?: {
    input_tokens: number
    output_tokens: number
    total_tokens: number
    cached_input_tokens: number
    reasoning_output_tokens: number
    source: 'provider' | 'tokenizer' | 'estimated' | 'unknown' | 'mixed'
    model?: string | null
  }
  tools?: Array<{
    call_id: string
    tool: string
    status: string
    summary: string
    duration_ms: number
    attempt_count: number
    error_code?: string | null
  }>
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
  measurement?: 'estimated' | 'tokenizer' | 'provider'
}

export interface ContextUsage {
  total_tokens: number
  budget_tokens: number
  soft_limit_tokens: number
  hard_limit_tokens: number
  estimated: boolean
  measurement?: 'estimated' | 'tokenizer' | 'provider'
  model?: string | null
  tokenizer?: string | null
  state: 'normal' | 'warning' | 'needs_compression'
  sections: ContextSectionUsage[]
}

export interface ContextCompressionReport {
  status: 'not_needed' | 'compressed' | 'no_gain'
  mode: AssistantContextSnapshot['mode']
  usage_before: ContextUsage
  usage_after: ContextUsage
  saved_tokens: number
  saved_percent: number
  compressed_message_count: number
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
  last_compression?: ContextCompressionReport | null
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
  summary_max_tokens: number
  max_tokens: number
  soft_limit_tokens: number
  hard_limit_tokens: number
  keep_recent_messages: number
  models: AssistantConfigModel[]
}

export type AssistantConfigUpdate = Omit<AssistantConfig, 'models'>

export const chatApi = {
  createConversation: (params?: { stock_symbol?: string; stock_market?: string; initial_context?: string }) =>
    fetchAPI<ChatConversation>('/assistant/conversations', {
      method: 'POST',
      body: JSON.stringify(params || {}),
    }),

  listConversations: (limit = 30) =>
    fetchAPI<ChatConversation[]>(`/assistant/conversations?limit=${limit}`),

  getConversation: (id: number) =>
    fetchAPI<ConversationDetail>(`/assistant/conversations/${id}`),

  deleteConversation: (id: number) =>
    fetchAPI<{ ok: boolean }>(`/assistant/conversations/${id}`, {
      method: 'DELETE',
    }),

  getSuggestedQuestions: (symbol: string, market: string) =>
    fetchAPI<{ questions: string[] }>(
      `/assistant/suggested-questions?symbol=${encodeURIComponent(symbol)}&market=${encodeURIComponent(market)}`
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
  sendAssistantMessageStream: sendMessageStream,
  subscribeAssistantTaskStream,
  decideAssistantApprovalStream,
}

export interface ChatStreamCallbacks {
  /** 已接受请求或正在执行的阶段提示 */
  onStatus?: (message: string) => void
  /** Server accepted a durable assistant task. */
  onRunStarted?: (info: { taskId: number; contextUsage?: ContextUsage }) => void
  /** A durable task id is known before execution starts. */
  onTaskCreated?: (taskId: number) => void
  /** Context was measured and, when needed, compacted before the agent loop. */
  onContextPrepared?: (info: {
    compressed: boolean
    compressionStatus: ContextCompressionReport['status']
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
  'extension_event',
  'model_usage',
  'tool_call_start',
  'tool_result',
  'approval_required',
  'paused',
  'done',
  'error',
])

const CHAT_STREAM_MAX_RECONNECTS = 3

interface AssistantStreamState {
  lastEventId: number
  finished: boolean
  paused: boolean
  terminalError: string
}

function dispatchAssistantEvent(
  ev: SSEEvent,
  callbacks: ChatStreamCallbacks,
  state: AssistantStreamState,
): void {
  if (ev.id > 0) state.lastEventId = ev.id
  const d = ev.data || {}
  if (TRACE_EVENTS.has(ev.event)) {
    callbacks.onTrace?.({ event: ev.event, data: d, id: ev.id })
  }
  switch (ev.event) {
    case 'task_created':
    case 'task_queued': {
      const taskId = Number(d.task_id) || 0
      if (taskId > 0) callbacks.onTaskCreated?.(taskId)
      break
    }
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
        compressionStatus: d.compression_status || (d.compressed ? 'compressed' : 'not_needed'),
        mode: d.mode || 'balanced',
        usageBefore: d.usage_before as ContextUsage,
        usageAfter: d.usage_after as ContextUsage,
        compressedMessageCount: Number(d.compressed_message_count) || 0,
      })
      break
    case 'extension_event': {
      const nestedEvent = d.event
      const nestedData = d.data || {}
      if (nestedEvent === 'plan') {
        callbacks.onPlan?.({
          status: nestedData.status || '',
          steps: nestedData.steps || [],
          current: nestedData.current,
        })
      }
      break
    }
    case 'token':
      callbacks.onToken?.(d.text || d.token || '')
      break
    case 'tool_call_start':
      callbacks.onToolCallStart?.({ name: d.name || d.tool || '', arguments: d.arguments || {} })
      break
    case 'tool_result':
      callbacks.onToolResult?.({ name: d.name || d.tool || '', ok: !!d.ok, preview: d.preview || d.summary || '' })
      break
    case 'plan':
      callbacks.onPlan?.({ status: d.status || '', steps: d.steps || [], current: d.current })
      break
    case 'approval_required': {
      const call = d.calls?.[0] || {}
      callbacks.onApprovalRequired?.({
        id: d.approval_id || '',
        tool_title: d.presentation?.tool_title || call.name || d.name || '需要确认的工具操作',
        risk: call.risk || d.risk || 'write',
        summary: d.presentation?.summary || call.summary || ('请求执行 ' + (call.name || d.name || '工具操作')),
        expires_at: d.expires_at || '',
        status: 'pending',
      })
      break
    }
    case 'paused':
      state.paused = true
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
      state.finished = true
      callbacks.onDone?.({
        message_id: d.message_id || 0,
        content: d.content || '',
        created_at: d.created_at || '',
      })
      break
    case 'error':
      state.terminalError = d.message || '未知错误'
      callbacks.onError?.(state.terminalError)
      break
  }
}

/**
 * 流式发送消息（SSE）。
 *
 * - 首次连接 POST /assistant/conversations/{id}/messages/stream；
 * - 连接中断后从持久化任务事件流 + Last-Event-ID 续推；
 * - 若首次连接直接失败，抛异常，由调用方展示失败状态。
 */
async function sendMessageStream(
  conversationId: number,
  content: string,
  callbacks: ChatStreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  let taskEventPath = ''
  const state: AssistantStreamState = {
    lastEventId: 0,
    finished: false,
    paused: false,
    terminalError: '',
  }
  let primaryError: unknown = null

  const handleEvent = (ev: SSEEvent) => {
    const d = ev.data || {}
    if (ev.event === 'task_created' || ev.event === 'task_queued') {
      const taskId = Number(d.task_id) || 0
      if (taskId > 0) taskEventPath = `/assistant/tasks/${taskId}/events`
    }
    dispatchAssistantEvent(ev, callbacks, state)
  }

  try {
    await readSSE(`/assistant/conversations/${conversationId}/messages/stream`, {
      method: 'POST',
      body: { content },
      signal,
      onEvent: handleEvent,
    })
  } catch (error) {
    primaryError = error
  }

  if (state.terminalError && !state.finished) throw new Error(state.terminalError)

  // 连接被中断但生成未结束，经持久化任务事件流接回。
  let reconnects = 0
  const reconnectPath = taskEventPath
  while (!state.finished && !state.paused && reconnectPath && reconnects < CHAT_STREAM_MAX_RECONNECTS) {
    if (signal?.aborted) return
    reconnects += 1
    try {
      await readSSE(reconnectPath, {
        signal,
        lastEventId: state.lastEventId,
        onEvent: handleEvent,
      })
    } catch {
      // 退避后再试
      await new Promise((r) => setTimeout(r, 1000 * reconnects))
    }
  }

  if (!state.finished && !state.paused) throw primaryError || new Error('流式回复未完成')
}

async function subscribeAssistantTaskStream(
  taskId: number,
  callbacks: ChatStreamCallbacks,
  signal?: AbortSignal,
  afterEventId = 0,
): Promise<void> {
  const state: AssistantStreamState = {
    lastEventId: Math.max(0, afterEventId),
    finished: false,
    paused: false,
    terminalError: '',
  }
  let primaryError: unknown = null
  let reconnects = 0
  const path = `/assistant/tasks/${taskId}/events`

  while (!state.finished && !state.paused && reconnects < CHAT_STREAM_MAX_RECONNECTS) {
    if (signal?.aborted) return
    try {
      await readSSE(path, {
        signal,
        lastEventId: state.lastEventId,
        onEvent: (ev) => dispatchAssistantEvent(ev, callbacks, state),
      })
      if (!state.finished && !state.paused) {
        reconnects += 1
        if (reconnects < CHAT_STREAM_MAX_RECONNECTS) {
          await new Promise((resolve) => setTimeout(resolve, 1000 * reconnects))
        }
      }
    } catch (error) {
      primaryError = error
      reconnects += 1
      if (reconnects >= CHAT_STREAM_MAX_RECONNECTS) break
      await new Promise((resolve) => setTimeout(resolve, 1000 * reconnects))
    }
  }

  if (state.terminalError && !state.finished) throw new Error(state.terminalError)
  if (!state.finished && !state.paused && !signal?.aborted) {
    throw primaryError || new Error('任务事件流未完成')
  }
}

async function decideAssistantApprovalStream(
  approvalId: string,
  decision: 'approved' | 'rejected',
  callbacks: ChatStreamCallbacks,
  taskId?: number,
  signal?: AbortSignal,
): Promise<void> {
  const state: AssistantStreamState = {
    lastEventId: 0,
    finished: false,
    paused: false,
    terminalError: '',
  }
  let primaryError: unknown = null
  try {
    await readSSE('/assistant/approvals/' + encodeURIComponent(approvalId) + '/decision/stream', {
      method: 'POST',
      body: { decision },
      signal,
      onEvent: (ev) => dispatchAssistantEvent(ev, callbacks, state),
    })
  } catch (error) {
    primaryError = error
  }

  // The POST is exactly-once. If its response is lost after the server has
  // accepted the decision, follow the durable task stream instead of posting
  // the decision again.
  let reconnects = 0
  while (!state.finished && !state.paused && taskId && reconnects < CHAT_STREAM_MAX_RECONNECTS) {
    if (signal?.aborted) return
    reconnects += 1
    try {
      await readSSE(`/assistant/tasks/${taskId}/events`, {
        signal,
        lastEventId: state.lastEventId,
        onEvent: (ev) => dispatchAssistantEvent(ev, callbacks, state),
      })
      if (!state.finished && !state.paused) {
        reconnects += 1
        if (reconnects < CHAT_STREAM_MAX_RECONNECTS) {
          await new Promise((resolve) => setTimeout(resolve, 1000 * reconnects))
        }
      }
    } catch {
      reconnects += 1
      if (reconnects >= CHAT_STREAM_MAX_RECONNECTS) break
      await new Promise((resolve) => setTimeout(resolve, 1000 * reconnects))
    }
  }

  if (state.terminalError && !state.finished) throw new Error(state.terminalError)
  if (!state.finished && !state.paused) throw primaryError || new Error('流式回复未完成')
}
