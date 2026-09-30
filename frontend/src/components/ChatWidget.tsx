import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowDown, ChevronLeft, MessageCircle, Menu, Send, Settings2, X, XCircle } from 'lucide-react'
import {
  chatApi,
  type AssistantContextDetail,
  type AssistantContextSnapshot,
  type AssistantTraceEvent,
  type AssistantTaskStatus,
  type ChatConversation,
  type ChatMessage,
} from '@panwatch/api'
import { ApprovalCard } from '@/components/assistant/ApprovalCard'
import { AssistantPermissionsDrawer } from '@/components/assistant/AssistantPermissionsDrawer'
import { AssistantSidebar } from '@/components/assistant/AssistantSidebar'
import { AssistantWelcome } from '@/components/assistant/AssistantWelcome'
import type { AssistantStockSearchResult } from '@/components/assistant/AssistantStockPicker'
import { ContextPanel } from '@/components/assistant/ContextPanel'
import { ContextUsageIndicator } from '@/components/assistant/ContextUsageIndicator'
import { TraceTimeline } from '@/components/assistant/TraceTimeline'
import { useChatAutoScroll } from '@/hooks/useChatAutoScroll'
import { useTranslation } from 'react-i18next'
import { AssistantResultCard } from '@/components/assistant/AssistantResultCard'
import { useAssistantTask, isActiveTask } from '@/hooks/useAssistantTask'
import { AssistantTaskBar } from '@/components/assistant/AssistantTaskBar'
import { AssistantMarkdown } from '@/components/assistant/AssistantMarkdown'
import { AssistantConversationActions } from '@/components/assistant/AssistantConversationActions'
import { AssistantTaskIndicator } from '@/components/assistant/AssistantTaskIndicator'
import { useActiveAssistantTasks } from '@/components/notifications/NotificationProvider'
import { ASSISTANT_ACTIVITY_CHANGED } from '@/lib/assistant-activity'

interface StockContext {
  symbol: string
  market: string
  stockName: string
  pageContext?: string
}

interface ConversationChangeOptions {
  replace?: boolean
}

interface ChatWidgetProps {
  embedded?: boolean
  /** Canonical conversation selected by the navigation-level route. */
  conversationIdFromUrl?: number | null
  /** Keep the route in sync when a user opens, creates, or leaves a session. */
  onConversationChange?: (conversationId: number | null, options?: ConversationChangeOptions) => void
  /** Stock context handed off by the application shell when a page opens “问 AI”. */
  initialStockContext?: StockContext | null
  /** Navigate to a trusted application route selected from a result action. */
  onNavigate?: (path: string) => void
}

function requestFailureText(
  assistantT: (key: string, options?: Record<string, unknown>) => string,
  error: unknown,
): string {
  return error instanceof Error && error.message
    ? error.message
    : assistantT('assistantPage.requestFailed')
}

export default function ChatWidget({
  embedded = false,
  conversationIdFromUrl = null,
  onConversationChange,
  initialStockContext = null,
  onNavigate,
}: ChatWidgetProps) {
  const { t } = useTranslation('configuration')
  const assistantT = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const [open, setOpen] = useState(embedded)
  const [conversations, setConversations] = useState<ChatConversation[]>([])
  const [conversationsLoaded, setConversationsLoaded] = useState(false)
  const conversationRequest = useRef(0)
  const backgroundTasks = useActiveAssistantTasks()
  const [activeConvId, setActiveConvId] = useState<number | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [creatingConversation, setCreatingConversation] = useState(false)
  const [reviewRequest, setReviewRequest] = useState(0)
  const [view, setView] = useState<'list' | 'chat'>('list')
  const [stockContext, setStockContext] = useState<StockContext | null>(null)
  const [suggestedQuestions, setSuggestedQuestions] = useState<string[]>([])
  // 流式回复的增量状态
  const [streamText, setStreamText] = useState('')
  const [streamTool, setStreamTool] = useState<string | null>(null)
  // 计划驱动(全面诊断持仓)的计划卡片状态
  const [plan, setPlan] = useState<{
    status: string
    steps: { id: number; title: string; status: string }[]
    current?: number
  } | null>(null)
  const [permissionsOpen, setPermissionsOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [contextDetail, setContextDetail] = useState<AssistantContextDetail | null>(null)
  const [contextPanelOpen, setContextPanelOpen] = useState(false)
  const [contextLoading, setContextLoading] = useState(false)
  const [contextCompressing, setContextCompressing] = useState(false)
  const [contextError, setContextError] = useState('')
  const [traceEvents, setTraceEvents] = useState<AssistantTraceEvent[]>([])
  const traceEventsRef = useRef<AssistantTraceEvent[]>([])
  const tokenBufRef = useRef('')
  const rafRef = useRef<number | null>(null)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const routeLoadRef = useRef<number | null>(null)
  // Async task/message requests may finish after the user has switched
  // conversations.  Keep the latest selection outside React's async closures
  // so stale responses cannot re-introduce an old task or message list.
  const activeConvIdRef = useRef<number | null>(null)
  // React state updates are batched; this synchronous guard closes the small
  // window where two clicks could otherwise create duplicate tasks/messages.
  const sendingRef = useRef(false)
  const interactionLockedRef = useRef(false)
  const {
    scrollBoxRef,
    followNewContent,
    handleScroll,
    scrollToBottom,
    showScrollToBottom,
    resetFollowing,
  } = useChatAutoScroll()

  // token 用 rAF 批量刷新，避免每个分片都触发渲染
  const pushToken = useCallback((t: string) => {
    tokenBufRef.current += t
    if (rafRef.current == null) {
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null
        setStreamText(tokenBufRef.current.trimEnd())
      })
    }
  }, [])

  useEffect(() => () => {
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current)
  }, [])

  const resetStream = useCallback(() => {
    tokenBufRef.current = ''
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
    setStreamText('')
    setStreamTool(null)
    setPlan(null)
  }, [])

  const appendTrace = useCallback((event: AssistantTraceEvent) => {
    const fingerprint = `${event.id ?? ''}:${event.event}:${JSON.stringify(event.data)}`
    if (traceEventsRef.current.some((item) => `${item.id ?? ''}:${item.event}:${JSON.stringify(item.data)}` === fingerprint)) return
    const next = [...traceEventsRef.current, event].slice(-40)
    traceEventsRef.current = next
    setTraceEvents(next)
  }, [])

  const loadConversations = useCallback(async () => {
    const request = ++conversationRequest.current
    try {
      const list = await chatApi.listConversations(30)
      if (conversationRequest.current === request) setConversations(list)
    } catch {
      // ignore
    } finally {
      if (conversationRequest.current === request) setConversationsLoaded(true)
    }
  }, [])

  useEffect(() => () => { conversationRequest.current++ }, [])

  const renameConversation = useCallback(async (conversationId: number, title: string) => {
    const updated = await chatApi.renameConversation(conversationId, title)
    conversationRequest.current++ // Discard older list requests after a rename.
    setConversations(previous => previous.map(item => item.id === conversationId ? updated : item))
  }, [])

  const loadMessages = useCallback(async (convId: number) => {
    try {
      const detail = await chatApi.getConversation(convId)
      if (activeConvIdRef.current !== convId) return
      setMessages(detail.messages)
      return detail
    } catch {
      // ignore
    }
  }, [])

  const task = useAssistantTask({
    onResetStream: resetStream,
    onReloadMessages: loadMessages,
    onTrace: appendTrace,
    onToken: (token) => { setStreamTool(null); pushToken(token) },
    onToolCallStart: ({ name }) => {
      tokenBufRef.current = ''
      setStreamText('')
      const toolName = assistantT(`p4.components.trace.tools.${name}`, { defaultValue: name })
      setStreamTool(assistantT(`assistantPage.tools.${name}`, {
        defaultValue: assistantT('assistantPage.callingTool', { name: toolName }),
      }))
    },
    onPlan: (next) => { setStreamTool(null); setPlan(next) },
    onRunStarted: ({ contextUsage }) => {
      if (contextUsage) setContextDetail((previous) => previous ? { ...previous, usage: contextUsage, status: contextUsage.state } : previous)
    },
    onContextPrepared: ({ compressedMessageCount, compressionStatus, mode, usageAfter, usageBefore }) => {
      setContextDetail((previous) => previous ? {
        ...previous, usage: usageAfter, status: usageAfter.state,
        last_compression: {
          status: compressionStatus, mode, usage_before: usageBefore, usage_after: usageAfter,
          saved_tokens: Math.max(0, usageBefore.total_tokens - usageAfter.total_tokens),
          saved_percent: Math.round(Math.max(0, usageBefore.total_tokens - usageAfter.total_tokens) / Math.max(usageBefore.total_tokens, 1) * 100),
          compressed_message_count: compressedMessageCount,
        },
      } : previous)
    },
    onDone: (message) => {
      const completedTrace = traceEventsRef.current
      setMessages((previous) => previous.some((item) => item.id === message.message_id) ? previous : [...previous, {
        id: message.message_id || Date.now() + 1,
        role: 'assistant', content: message.content,
        created_at: message.created_at || new Date().toISOString(),
        trace: completedTrace.length > 0 ? completedTrace : undefined,
        result: message.result,
      }])
      traceEventsRef.current = []
      setTraceEvents([])
      setReviewRequest(0)
      requestAnimationFrame(() => inputRef.current?.focus())
    },
  })
  const { pendingApprovals } = task
  const taskStatuses: Record<number, AssistantTaskStatus> = Object.fromEntries(backgroundTasks.map(item => [item.conversation_id, item.status]))
  const backgroundCurrentTask = backgroundTasks.find(item => item.conversation_id === activeConvId)
  if (task.snapshot && activeConvId && (!backgroundCurrentTask || backgroundCurrentTask.id <= task.snapshot.id)) {
    if (isActiveTask(task.snapshot.status)) taskStatuses[activeConvId] = task.snapshot.status
    else delete taskStatuses[activeConvId]
  }
  if (task.sending && activeConvId && !taskStatuses[activeConvId]) taskStatuses[activeConvId] = 'pending'
  const activitySignature = JSON.stringify([backgroundTasks.map(item => [item.id, item.status]), task.snapshot?.id, task.snapshot?.status])
  useEffect(() => {
    if (!open) return
    const refresh = () => { if (document.visibilityState !== 'hidden') void loadConversations() }
    refresh()
    // Metadata can arrive after the completed answer. Catch the bounded
    // title summary, then stop polling once the conversation is idle.
    const timers = [1000, 4000, 10_000].map(delay => setTimeout(refresh, delay))
    const interval = backgroundTasks.length || task.sending ? setInterval(refresh, 5000) : undefined
    window.addEventListener('focus', refresh)
    window.addEventListener(ASSISTANT_ACTIVITY_CHANGED, refresh)
    return () => {
      timers.forEach(clearTimeout); clearInterval(interval)
      window.removeEventListener('focus', refresh)
      window.removeEventListener(ASSISTANT_ACTIVITY_CHANGED, refresh)
    }
  }, [open, activitySignature, task.sending, loadConversations])
  const sending = creatingConversation || task.sending
  const setActiveConversationId = useCallback((conversationId: number | null) => {
    if (activeConvIdRef.current !== conversationId) {
      task.reset(conversationId)
      traceEventsRef.current = []
      setTraceEvents([])
      setReviewRequest(0)
      setCreatingConversation(false)
      sendingRef.current = false
    }
    activeConvIdRef.current = conversationId
    setActiveConvId(conversationId)
  }, [task.reset])

  useEffect(() => {
    const conversationId = activeConvId
    setContextPanelOpen(false)
    setContextDetail(null)
    setContextError('')
    if (!conversationId || typeof chatApi.getAssistantContext !== 'function') return
    let cancelled = false
    setContextLoading(true)
    chatApi.getAssistantContext(conversationId)
      .then((detail) => {
        if (!cancelled) setContextDetail(detail)
      })
      .catch(() => {
        if (!cancelled) setContextError(assistantT('assistantPage.contextError'))
      })
      .finally(() => {
        if (!cancelled) setContextLoading(false)
      })
    return () => { cancelled = true }
  }, [activeConvId])

  const handleCompressContext = useCallback(async (mode: AssistantContextSnapshot['mode']) => {
    if (!activeConvId || contextCompressing || typeof chatApi.compressAssistantContext !== 'function') return
    setContextCompressing(true)
    setContextError('')
    try {
      const next = await chatApi.compressAssistantContext(activeConvId, mode)
      setContextDetail(next)
      const compression = next.last_compression
      appendTrace({
        event: 'context_prepared',
        data: {
          compressed: compression?.status === 'compressed',
          compression_status: compression?.status || 'not_needed',
          mode,
          usage_before: compression?.usage_before,
          usage_after: compression?.usage_after,
        },
      })
    } catch {
      setContextError(assistantT('assistantPage.compressionFailed'))
    } finally {
      setContextCompressing(false)
    }
  }, [activeConvId, appendTrace, contextCompressing])

  const loadSuggestedQuestions = useCallback(async (symbol: string, market: string) => {
    try {
      const res = await chatApi.getSuggestedQuestions(symbol, market)
      setSuggestedQuestions(res.questions || [])
    } catch {
      setSuggestedQuestions([])
    }
  }, [])

  // The application shell owns cross-page “问 AI” routing.  Keeping the
  // handoff as a prop means it is not lost while this page is unmounted.
  useEffect(() => {
    if (!embedded || !initialStockContext?.symbol) return

    let cancelled = false
    const detail = initialStockContext
    setOpen(true)
    setStockContext(detail)
    setSuggestedQuestions([])
    resetFollowing()

    chatApi.createConversation({
      stock_symbol: detail.symbol,
      stock_market: detail.market,
      initial_context: detail.pageContext,
    }).then((conv) => {
      if (cancelled) return
      setActiveConversationId(conv.id)
      onConversationChange?.(conv.id)
      setMessages([])
      setView('chat')
      setConversations((prev) => [conv, ...prev.filter((item) => item.id !== conv.id)])
      loadSuggestedQuestions(detail.symbol, detail.market)
    }).catch((error) => {
      if (!cancelled) {
        setView('chat')
        setMessages([{
          id: Date.now(),
          role: 'assistant',
          content: requestFailureText(assistantT, error),
          created_at: new Date().toISOString(),
        }])
      }
    })

    return () => { cancelled = true }
  }, [assistantT, embedded, initialStockContext, loadSuggestedQuestions, onConversationChange, resetFollowing, setActiveConversationId])

  useEffect(() => {
    if (open) {
      loadConversations()
    }
  }, [open, loadConversations])

  useLayoutEffect(() => {
    followNewContent()
  }, [messages, streamText, streamTool, pendingApprovals, traceEvents, plan, sending, followNewContent])

  const openConversation = useCallback(async (
    conv: ChatConversation,
    options: { updateUrl?: boolean } = {},
  ) => {
    resetFollowing()
    setActiveConversationId(conv.id)
    setView('chat')
    if (options.updateUrl !== false) onConversationChange?.(conv.id)
    setSuggestedQuestions([])
    if (conv.stock_symbol && conv.stock_market) {
      setStockContext({ symbol: conv.stock_symbol, market: conv.stock_market, stockName: '' })
      loadSuggestedQuestions(conv.stock_symbol, conv.stock_market)
    } else {
      setStockContext(null)
    }
    const detail = await loadMessages(conv.id)
    if (activeConvIdRef.current === conv.id) void task.restore(conv.id, detail?.latest_task)
  }, [loadMessages, loadSuggestedQuestions, onConversationChange, resetFollowing, task.restore])

  // A route is the source of truth for the embedded assistant. The first
  // render may not have the conversation list yet, so wait until that request
  // settles before resolving an ID. If the session is older than the list
  // window, hydrate it directly by ID instead of losing a valid deep link.
  useEffect(() => {
    if (!embedded || !onConversationChange) return

    const requestedId = conversationIdFromUrl
    if (requestedId == null) {
      routeLoadRef.current = null
      if (activeConvId !== null || view === 'chat') {
        setActiveConversationId(null)
        setMessages([])
        setView('list')
        setStockContext(null)
        setSuggestedQuestions([])
      }
      return
    }

    if (!conversationsLoaded || (activeConvId === requestedId && view === 'chat')) return
    if (routeLoadRef.current === requestedId) return

    routeLoadRef.current = requestedId
    const listedConversation = conversations.find((item) => item.id === requestedId)
    const hydrate = listedConversation
      ? Promise.resolve(listedConversation)
      : chatApi.getConversation(requestedId).then((detail) => {
        setConversations((previous) => (
          previous.some((item) => item.id === detail.conversation.id)
            ? previous
            : [detail.conversation, ...previous]
        ))
        return detail.conversation
      })

    hydrate
      .then((conversation) => openConversation(conversation, { updateUrl: false }))
      .catch(() => {
        routeLoadRef.current = null
        onConversationChange?.(null, { replace: true })
        setActiveConversationId(null)
        setMessages([])
        setView('list')
      })
  }, [activeConvId, conversationIdFromUrl, conversations, conversationsLoaded, embedded, onConversationChange, openConversation, view])

  const createNewConversation = useCallback(async () => {
    try {
      resetFollowing()
      const conv = await chatApi.createConversation()
      setActiveConversationId(conv.id)
      onConversationChange?.(conv.id)
      setMessages([])
      setView('chat')
      setStockContext(null)
      setSuggestedQuestions([])
      setConversations((prev) => [conv, ...prev])
    } catch (error) {
      setView('chat')
      setMessages([{
        id: Date.now(),
        role: 'assistant',
        content: requestFailureText(assistantT, error),
        created_at: new Date().toISOString(),
      }])
    }
  }, [assistantT, onConversationChange, resetFollowing])

  const beginNewResearch = useCallback(() => {
    resetFollowing()
    setActiveConversationId(null)
    onConversationChange?.(null)
    setMessages([])
    setView('list')
    setStockContext(null)
    setSuggestedQuestions([])
    setHistoryOpen(false)
  }, [onConversationChange, resetFollowing])

  const removeConversation = useCallback(async (convId: number) => {
    try {
      await chatApi.deleteConversation(convId)
      setConversations((prev) => prev.filter((c) => c.id !== convId))
      if (activeConvId === convId) {
        setActiveConversationId(null)
        onConversationChange?.(null, { replace: true })
        setMessages([])
        setView('list')
        setStockContext(null)
        setSuggestedQuestions([])
      }
    } catch {
      // ignore
    }
  }, [activeConvId, onConversationChange])

  const handleSend = useCallback(async (
    overrideContent?: string,
    overrideStockContext?: StockContext,
  ) => {
    const content = (overrideContent || input).trim()
    if (!content || interactionLockedRef.current || sendingRef.current) return

    sendingRef.current = true
    setCreatingConversation(true)

    const messageStockContext = overrideStockContext || stockContext
    let convId = activeConvId
    if (!convId) {
      try {
        const conv = await chatApi.createConversation(
          messageStockContext
            ? {
                stock_symbol: messageStockContext.symbol,
                stock_market: messageStockContext.market,
                initial_context: messageStockContext.pageContext,
              }
            : undefined,
        )
        convId = conv.id
        setActiveConversationId(conv.id)
        sendingRef.current = true
        onConversationChange?.(conv.id)
        setConversations((prev) => [conv, ...prev.filter((item) => item.id !== conv.id)])
        setView('chat')
      } catch (error) {
        sendingRef.current = false
        setCreatingConversation(false)
        setView('chat')
        setMessages((previous) => [...previous, {
          id: Date.now() + 1,
          role: 'assistant',
          content: requestFailureText(assistantT, error),
          created_at: new Date().toISOString(),
        }])
        return
      }
    }

    setInput('')
    setSuggestedQuestions([])
    setReviewRequest(0)
    traceEventsRef.current = []
    setTraceEvents([])
    setMessages((previous) => [...previous, {
      id: Date.now(), role: 'user', content, created_at: new Date().toISOString(),
    }])
    resetFollowing()
    setCreatingConversation(false)
    setConversations(previous => previous.map(item => item.id === convId && !item.title
      ? { ...item, title: content.slice(0, 20) } : item))
    try {
      await task.send(convId, content)
    } finally {
      if (activeConvIdRef.current === convId) {
        sendingRef.current = false
        setCreatingConversation(false)
      }
    }
  }, [input, activeConvId, stockContext, task.send, resetFollowing, onConversationChange, assistantT, setActiveConversationId])

  const handleStockSelect = useCallback((stock: AssistantStockSearchResult) => {
    const nextContext: StockContext = {
      symbol: stock.symbol,
      market: stock.market,
      stockName: stock.name,
    }
    setStockContext(nextContext)
    void handleSend(
      assistantT('assistantPage.askStock', { market: stock.market, symbol: stock.symbol, name: stock.name }),
      nextContext,
    )
  }, [handleSend])

  const interactionLocked = sending || task.control !== null
    || pendingApprovals.some((approval) => approval.status === 'pending')
    || (task.snapshot != null && isActiveTask(task.snapshot.status))
  interactionLockedRef.current = interactionLocked

  if (!open && !embedded) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-20 right-4 md:bottom-5 md:right-5 z-40 w-12 h-12 rounded-full bg-primary text-primary-foreground shadow-lg flex items-center justify-center hover:bg-primary/90 transition-all hover:scale-105"
      >
        <MessageCircle className="w-5 h-5" />
      </button>
    )
  }

  return (
    <>
      {embedded && <AssistantPermissionsDrawer open={permissionsOpen} onOpenChange={setPermissionsOpen} />}
      <div
        data-testid={embedded ? 'assistant-shell' : undefined}
        className={embedded
        ? 'relative flex h-full min-h-0 w-full overflow-hidden rounded-none border border-border/60 bg-card shadow-sm sm:rounded-2xl'
        : 'fixed bottom-0 right-0 z-50 flex h-full w-full flex-col overflow-hidden bg-background shadow-2xl md:bottom-5 md:right-5 md:h-[600px] md:w-[420px] md:rounded-xl md:border md:border-border/60'}>
        {embedded && (
          <div className="hidden w-64 shrink-0 md:flex">
            <AssistantSidebar
              conversations={conversations}
              taskStatuses={taskStatuses}
              onRename={renameConversation}
              activeConversationId={activeConvId}
              onOpen={openConversation}
              onCreate={beginNewResearch}
              onDelete={(conversationId) => { void removeConversation(conversationId) }}
            />
          </div>
        )}
        {embedded && historyOpen && (
          <div className="absolute inset-0 z-30 flex md:hidden">
            <div className="w-[min(19rem,88vw)] shadow-2xl">
              <AssistantSidebar
                conversations={conversations}
                taskStatuses={taskStatuses}
                onRename={renameConversation}
                activeConversationId={activeConvId}
                onOpen={(conversation) => { setHistoryOpen(false); void openConversation(conversation) }}
                onCreate={beginNewResearch}
                onDelete={(conversationId) => { void removeConversation(conversationId) }}
              />
            </div>
            <button
              type="button"
              aria-label={assistantT('assistantPage.closeHistory')}
              className="flex-1 bg-black/20"
              onClick={() => setHistoryOpen(false)}
            />
          </div>
        )}
        <div className={embedded
          ? 'relative flex min-w-0 min-h-0 flex-1 flex-col overflow-hidden'
          : 'relative flex h-full flex-col overflow-hidden'}>
      {/* Header */}
      <div className="flex items-center justify-between gap-2 border-b border-border/40 bg-accent/20 px-3 py-2.5 sm:px-4 sm:py-3">
        <div className="flex min-w-0 items-center gap-1.5 sm:gap-2">
          {embedded && (
            <button
              type="button"
              onClick={() => setHistoryOpen(true)}
              className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground md:hidden"
              aria-label={assistantT('assistantPage.openHistory')}
            >
              <Menu className="h-4 w-4" />
            </button>
          )}
          {view === 'chat' && (
            <button
              onClick={beginNewResearch}
              className="text-muted-foreground hover:text-foreground transition-colors"
              aria-label={assistantT('assistantPage.backHome')}
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
          )}
          <span className="min-w-0 truncate text-[14px] font-semibold text-foreground">{assistantT('assistantPage.title')}</span>
          {view === 'chat' && stockContext && (
            <span className="inline-flex max-w-[42vw] items-center gap-1 truncate rounded-full bg-primary/10 px-2 py-0.5 text-[11px] text-primary sm:max-w-none">
              {stockContext.market}:{stockContext.symbol}
              <span className="truncate">{stockContext.stockName && ` ${stockContext.stockName}`}</span>
              <button
                onClick={() => { setStockContext(null); setSuggestedQuestions([]) }}
                className="hover:text-primary/70 transition-colors"
              >
                <XCircle className="w-3 h-3" />
              </button>
            </span>
          )}
          {view === 'chat' && (
            <ContextUsageIndicator
              usage={contextDetail?.usage || null}
              onClick={() => setContextPanelOpen((open) => !open)}
            />
          )}
        </div>
        <div className="flex items-center gap-1">
          {embedded && (
            <button
              type="button"
              onClick={() => setPermissionsOpen(true)}
              className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground"
              title={assistantT('assistantPage.permissions')}
              aria-label={assistantT('assistantPage.permissions')}
            >
              <Settings2 className="h-4 w-4" />
            </button>
          )}
          {!embedded && (
            <button
              onClick={() => setOpen(false)}
              className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      {view === 'chat' && contextPanelOpen && (
        <ContextPanel
          detail={contextDetail}
          loading={contextLoading}
          compressing={contextCompressing}
          error={contextError}
          onCompress={(mode) => { void handleCompressContext(mode) }}
          onClose={() => setContextPanelOpen(false)}
        />
      )}

      {/* List view */}
      {view === 'list' && embedded && (
        <AssistantWelcome
          onSubmit={(question) => { void handleSend(question) }}
          onSelectStock={handleStockSelect}
          disabled={interactionLocked}
        />
      )}
      {view === 'list' && !embedded && (
        <div className="flex-1 overflow-y-auto scrollbar">
          {conversations.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground text-[13px] gap-3">
              <MessageCircle className="w-8 h-8 opacity-30" />
              <p>{assistantT('assistantPage.noConversations')}</p>
              <button
                onClick={createNewConversation}
                className="text-[12px] px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
              >
                {assistantT('assistantPage.newConversation')}
              </button>
            </div>
          ) : (
            conversations.map((conv) => (
              <div
                key={conv.id}
                className="group flex w-full items-center justify-between border-b border-border/20 px-4 py-3 text-left hover:bg-accent/30"
              >
                <button type="button" onClick={() => void openConversation(conv)} className="min-w-0 flex-1 text-left">
                  <div className="flex items-center gap-2 text-[13px] text-foreground">
                    <span className="min-w-0 flex-1 truncate">{conv.title || assistantT('assistantPage.newConversation')}</span><AssistantTaskIndicator status={taskStatuses[conv.id]} />
                  </div>
                  <div className="text-[11px] text-muted-foreground mt-0.5">
                    {conv.stock_symbol ? `${conv.stock_market}:${conv.stock_symbol} · ` : ''}
                    {new Date(conv.created_at).toLocaleDateString()}
                  </div>
                </button>
                <AssistantConversationActions conversation={conv} onRename={renameConversation} onDelete={id => { void removeConversation(id) }} />
              </div>
            ))
          )}
        </div>
      )}

      {/* Chat view */}
      {view === 'chat' && (
        <>
          <div
            ref={scrollBoxRef}
            data-testid="assistant-message-list"
            onScroll={handleScroll}
            tabIndex={0}
            style={{ overflowAnchor: 'none' }}
            className="min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain px-3 py-3 scrollbar sm:px-4"
          >
            {/* Suggested questions */}
            {messages.length === 0 && suggestedQuestions.length > 0 && (
              <div className="flex flex-col gap-2">
                <span className="text-[11px] text-muted-foreground">{assistantT('assistantPage.recommended')}</span>
                <div className="flex flex-wrap gap-2">
                  {suggestedQuestions.map((q) => (
                    <button
                      key={q}
                      className="text-[11px] px-3 py-1.5 rounded-full bg-primary/10 text-primary hover:bg-primary/20 transition-colors text-left"
                      onClick={() => handleSend(q)}
                      disabled={interactionLocked}
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.length === 0 && suggestedQuestions.length === 0 && !sending && (
              <div className="flex flex-col items-center justify-center h-full text-muted-foreground text-[13px] gap-2">
                <MessageCircle className="w-6 h-6 opacity-30" />
                <p>{assistantT('assistantPage.startConversation')}</p>
              </div>
            )}
            {messages.map((msg) => (
              <div
                key={msg.id}
                className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                <div className="flex max-w-[92%] flex-col gap-2 sm:max-w-[85%]">
                  <div
                    className={`rounded-xl px-3 py-2 text-[13px] leading-relaxed ${
                      msg.role === 'user'
                        ? 'bg-primary text-primary-foreground'
                        : 'bg-accent/60 text-foreground'
                    }`}
                  >
                    {msg.role === 'assistant' ? (
                      <AssistantMarkdown content={msg.content} />
                    ) : (
                      msg.content
                    )}
                  </div>
                  {msg.role === 'assistant' && msg.result && (
                    <AssistantResultCard
                      result={msg.result}
                      disabled={interactionLocked}
                      onPrefill={(prompt) => {
                        setInput(prompt)
                        requestAnimationFrame(() => inputRef.current?.focus())
                      }}
                      onNavigate={(path) => onNavigate?.(path)}
                      onSubmitPrompt={(prompt) => { void handleSend(prompt) }}
                    />
                  )}
                  {msg.role === 'assistant' && msg.trace && msg.trace.length > 0 && (
                    <TraceTimeline events={msg.trace} />
                  )}
                </div>
              </div>
            ))}
            {pendingApprovals.map((approval) => (
              <div key={approval.id} className="flex justify-start">
                <ApprovalCard
                  approval={approval}
                  blocked={task.control !== null || task.decidingApprovalId !== null}
                  onDecision={(decision) => task.decide(approval, decision)}
                />
              </div>
            ))}
            {traceEvents.length > 0 && (
              <div className="flex justify-start">
                <div className="w-full max-w-[92%] sm:max-w-[85%]">
                  <TraceTimeline events={traceEvents} live={sending} reviewRequest={reviewRequest} />
                </div>
              </div>
            )}
            {sending && plan && plan.steps.length > 0 && (
              // 计划驱动(全面诊断持仓)的计划卡片:步骤 + 状态
              <div className="flex justify-start">
                <div className="w-full max-w-[92%] rounded-xl border border-border/40 bg-accent/40 px-3 py-2 text-[12px] sm:max-w-[85%]">
                  <div className="font-medium text-foreground mb-1.5">
                    {assistantT('assistantPage.diagnosisPlan')}{plan.status === 'done' ? ` (${assistantT('assistantPage.completed')})` : plan.status === 'planning' ? ` (${assistantT('assistantPage.planning')})` : ''}
                  </div>
                  <ol className="space-y-1">
                    {plan.steps.map((s) => (
                      <li key={s.id} className="flex items-center gap-2">
                        <span
                          className={
                            s.status === 'done'
                              ? 'text-emerald-600'
                              : s.status === 'failed'
                              ? 'text-rose-600'
                              : s.status === 'running'
                              ? 'text-primary'
                              : 'text-muted-foreground'
                          }
                        >
                          {s.status === 'done'
                            ? '✓'
                            : s.status === 'failed'
                            ? '✕'
                            : s.status === 'running'
                            ? '⟳'
                            : '○'}
                        </span>
                        <span className={s.status === 'done' ? 'text-muted-foreground' : 'text-foreground'}>
                          {s.title}
                        </span>
                      </li>
                    ))}
                  </ol>
                </div>
              </div>
            )}
            {sending && streamText && (
              // 流式增量渲染（未闭合代码块乐观闭合）
              <div className="flex justify-start">
                <div className="max-w-[92%] rounded-xl bg-accent/60 px-3 py-2 text-[13px] leading-relaxed text-foreground sm:max-w-[85%]">
                  <AssistantMarkdown content={streamText} streaming />
                </div>
              </div>
            )}
            {sending && !streamText && pendingApprovals.length === 0 && (
              <div className="flex justify-start">
                <div
                  className="bg-accent/60 rounded-xl px-3 py-2 text-[13px] text-muted-foreground flex items-center gap-2"
                  role="status"
                  aria-label={streamTool || assistantT('assistantPage.requestFailed')}
                >
                  <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                  {streamTool && <span>{streamTool}</span>}
                </div>
              </div>
            )}
          </div>

          {showScrollToBottom && (
            <button
              type="button"
              onClick={scrollToBottom}
              className="absolute left-1/2 bottom-16 z-10 flex h-10 -translate-x-1/2 items-center gap-2 rounded-full border border-primary/30 bg-background/95 px-4 text-sm font-medium text-foreground shadow-xl shadow-black/20 backdrop-blur transition-all hover:-translate-x-1/2 hover:scale-105 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
              aria-label={assistantT('assistantPage.scrollBottom')}
              title={assistantT('assistantPage.scrollLatest')}
            >
              <ArrowDown className="h-4 w-4 shrink-0" />
              <span className="hidden sm:inline">{assistantT('assistantPage.scrollBottom')}</span>
            </button>
          )}

          <AssistantTaskBar
            snapshot={task.snapshot}
            error={task.error}
            disconnected={task.disconnected}
            control={task.control}
            controlError={task.controlError}
            onStop={() => { void task.cancel() }}
            onBackground={onNavigate ? () => onNavigate('/') : undefined}
            onRetry={() => { traceEventsRef.current = []; setTraceEvents([]); void task.retry() }}
            onReconnect={() => { void task.reconnect() }}
            onConfigure={() => onNavigate?.('/settings')}
            onPermissions={() => setPermissionsOpen(true)}
            onContext={() => setContextPanelOpen(true)}
            onRevise={() => {
              const original = [...messages].reverse().find((message) => message.role === 'user')
              if (original) setInput(original.content)
              requestAnimationFrame(() => inputRef.current?.focus())
            }}
            onReview={() => { setReviewRequest((previous) => previous + 1); requestAnimationFrame(() => scrollToBottom()) }}
          />
          {/* Input */}
          <div data-testid="assistant-composer" className={`sticky bottom-0 flex shrink-0 items-center gap-2 border-t border-border/40 bg-background px-3 py-2.5 sm:px-4 sm:py-3 ${embedded ? '' : 'pb-[calc(0.625rem+env(safe-area-inset-bottom))] sm:pb-3'}`}>
            <input
              ref={inputRef}
              type="text"
              className="h-10 min-w-0 flex-1 rounded-lg bg-accent/40 px-3 text-[13px] text-foreground outline-none placeholder:text-muted-foreground focus:ring-1 focus:ring-primary/30 sm:h-9"
              placeholder={assistantT('assistantPage.askPlaceholder')}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  handleSend()
                }
              }}
              disabled={interactionLocked}
            />
            <button
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50 sm:h-9 sm:w-9"
              onClick={() => handleSend()}
              disabled={interactionLocked || !input.trim()}
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </>
      )}
        </div>
      </div>
    </>
  )
}
