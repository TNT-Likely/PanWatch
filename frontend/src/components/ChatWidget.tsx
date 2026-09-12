import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowDown, ChevronLeft, MessageCircle, Menu, Send, Settings2, Trash2, X, XCircle } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { chatApi, type AssistantApproval, type ChatConversation, type ChatMessage } from '@panwatch/api'
import { ApprovalCard } from '@/components/assistant/ApprovalCard'
import { AssistantPermissionsDrawer } from '@/components/assistant/AssistantPermissionsDrawer'
import { AssistantSidebar } from '@/components/assistant/AssistantSidebar'
import { AssistantWelcome } from '@/components/assistant/AssistantWelcome'
import { useChatAutoScroll } from '@/hooks/useChatAutoScroll'

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
}

function taskStorageKey(conversationId: number): string {
  return 'panwatch:assistant-task:' + conversationId
}

function approvalFromSnapshot(approval: {
  id: string
  tool_name: string
  risk: AssistantApproval['risk']
  presentation: { tool_title?: string; summary?: string }
  expires_at: string
}): AssistantApproval {
  return {
    id: approval.id,
    tool_title: approval.presentation?.tool_title || approval.tool_name,
    risk: approval.risk,
    summary: approval.presentation?.summary || ('请求执行 ' + approval.tool_name),
    expires_at: approval.expires_at,
    status: 'pending',
  }
}

// 工具名 → 过程可视化文案
const TOOL_LABELS: Record<string, string> = {
  get_portfolio: '正在查询持仓…',
  get_stock_quote: '正在查询行情…',
  get_kline_summary: '正在分析 K 线…',
  get_stock_news: '正在检索相关新闻…',
  create_price_alert: '正在创建价格提醒…',
  get_technical_analysis: '正在分析技术面…',
  get_stock_suggestions: '正在查询 AI 建议…',
  get_watchlist: '正在查询自选股…',
}

/** 增量渲染容错：流式文本里未闭合的代码围栏先乐观闭合，避免 markdown 渲染爆版式 */
function safeStreamMarkdown(text: string): string {
  const fences = (text.match(/```/g) || []).length
  return fences % 2 === 1 ? `${text}\n\`\`\`` : text
}

export default function ChatWidget({
  embedded = false,
  conversationIdFromUrl = null,
  onConversationChange,
}: ChatWidgetProps) {
  const [open, setOpen] = useState(embedded)
  const [conversations, setConversations] = useState<ChatConversation[]>([])
  const [conversationsLoaded, setConversationsLoaded] = useState(false)
  const [activeConvId, setActiveConvId] = useState<number | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
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
  const [taskId, setTaskId] = useState<number | null>(null)
  const [pendingApprovals, setPendingApprovals] = useState<AssistantApproval[]>([])
  const [decidingApprovalId, setDecidingApprovalId] = useState<string | null>(null)
  const [permissionsOpen, setPermissionsOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const tokenBufRef = useRef('')
  const rafRef = useRef<number | null>(null)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const routeLoadRef = useRef<number | null>(null)
  // React state updates are batched; this synchronous guard closes the small
  // window where two clicks could otherwise create duplicate tasks/messages.
  const sendingRef = useRef(false)
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
        setStreamText(tokenBufRef.current)
      })
    }
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

  const loadConversations = useCallback(async () => {
    try {
      const list = await chatApi.listConversations(30)
      setConversations(list)
    } catch {
      // ignore
    } finally {
      setConversationsLoaded(true)
    }
  }, [])

  const loadMessages = useCallback(async (convId: number) => {
    try {
      const detail = await chatApi.getConversation(convId)
      setMessages(detail.messages)
    } catch {
      // ignore
    }
  }, [])

  const loadSuggestedQuestions = useCallback(async (symbol: string, market: string) => {
    try {
      const res = await chatApi.getSuggestedQuestions(symbol, market)
      setSuggestedQuestions(res.questions || [])
    } catch {
      setSuggestedQuestions([])
    }
  }, [])

  // Listen for stock context events from stock insight modal
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as StockContext
      if (!detail?.symbol) return
      setOpen(true)
      setStockContext(detail)
      setSuggestedQuestions([])
      setTaskId(null)
      setPendingApprovals([])
      resetFollowing()

      // Create a new conversation bound to this stock, with page context
      chatApi.createConversation({
        stock_symbol: detail.symbol,
        stock_market: detail.market,
        initial_context: detail.pageContext,
      }).then((conv) => {
        setActiveConvId(conv.id)
        onConversationChange?.(conv.id)
        setMessages([])
        setView('chat')
        setConversations((prev) => [conv, ...prev])
        loadSuggestedQuestions(detail.symbol, detail.market)
      }).catch(() => {
        // fallback: just open chat
        setView('chat')
      })
    }
    window.addEventListener('panwatch-open-chat', handler)
    return () => window.removeEventListener('panwatch-open-chat', handler)
  }, [loadSuggestedQuestions, onConversationChange, resetFollowing])

  useEffect(() => {
    if (open) {
      loadConversations()
    }
  }, [open, loadConversations])

  useEffect(() => {
    if (!activeConvId) return
    const storedTaskId = Number(sessionStorage.getItem(taskStorageKey(activeConvId)))
    if (!Number.isInteger(storedTaskId) || storedTaskId <= 0) return

    chatApi.getAssistantTask(storedTaskId).then((snapshot) => {
      if (snapshot.conversation_id !== activeConvId || snapshot.status !== 'awaiting_approval') return
      const approvals = snapshot.pending_approvals.map(approvalFromSnapshot)
      if (approvals.length === 0) return
      setTaskId(snapshot.id)
      setPendingApprovals(approvals)
    }).catch(() => {
      // A stale local task marker is harmless; a new message will create a new task.
      sessionStorage.removeItem(taskStorageKey(activeConvId))
    })
  }, [activeConvId])

  useEffect(() => {
    followNewContent()
  }, [messages, streamText, streamTool, pendingApprovals, followNewContent])

  const openConversation = useCallback(async (
    conv: ChatConversation,
    options: { updateUrl?: boolean } = {},
  ) => {
    resetFollowing()
    setTaskId(null)
    setPendingApprovals([])
    setActiveConvId(conv.id)
    setView('chat')
    if (options.updateUrl !== false) onConversationChange?.(conv.id)
    setSuggestedQuestions([])
    if (conv.stock_symbol && conv.stock_market) {
      setStockContext({ symbol: conv.stock_symbol, market: conv.stock_market, stockName: '' })
      loadSuggestedQuestions(conv.stock_symbol, conv.stock_market)
    } else {
      setStockContext(null)
    }
    await loadMessages(conv.id)
  }, [loadMessages, loadSuggestedQuestions, onConversationChange, resetFollowing])

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
        setActiveConvId(null)
        setTaskId(null)
        setPendingApprovals([])
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
        setActiveConvId(null)
        setMessages([])
        setView('list')
      })
  }, [activeConvId, conversationIdFromUrl, conversations, conversationsLoaded, embedded, onConversationChange, openConversation, view])

  const createNewConversation = useCallback(async () => {
    try {
      resetFollowing()
      setTaskId(null)
      setPendingApprovals([])
      const conv = await chatApi.createConversation()
      setActiveConvId(conv.id)
      onConversationChange?.(conv.id)
      setMessages([])
      setView('chat')
      setStockContext(null)
      setSuggestedQuestions([])
      setConversations((prev) => [conv, ...prev])
    } catch {
      // ignore
    }
  }, [onConversationChange, resetFollowing])

  const beginNewResearch = useCallback(() => {
    resetFollowing()
    setTaskId(null)
    setPendingApprovals([])
    setActiveConvId(null)
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
        setActiveConvId(null)
        onConversationChange?.(null, { replace: true })
        setTaskId(null)
        setPendingApprovals([])
        setMessages([])
        setView('list')
        setStockContext(null)
        setSuggestedQuestions([])
      }
    } catch {
      // ignore
    }
  }, [activeConvId, onConversationChange])

  const deleteConversation = useCallback(async (convId: number, e: React.MouseEvent) => {
    e.stopPropagation()
    await removeConversation(convId)
  }, [removeConversation])

  const handleSend = useCallback(async (overrideContent?: string) => {
    const content = (overrideContent || input).trim()
    if (!content || sending || sendingRef.current || pendingApprovals.length > 0) return

    sendingRef.current = true
    setSending(true)

    let convId = activeConvId
    if (!convId) {
      try {
        const conv = await chatApi.createConversation(
          stockContext ? { stock_symbol: stockContext.symbol, stock_market: stockContext.market } : undefined
        )
        convId = conv.id
        setActiveConvId(conv.id)
        onConversationChange?.(conv.id)
        setConversations((prev) => [conv, ...prev])
        setView('chat')
      } catch {
        sendingRef.current = false
        setSending(false)
        return
      }
    }

    setInput('')
    setSuggestedQuestions([]) // hide after first send
    setTaskId(null)
    setPendingApprovals([])
    sessionStorage.removeItem(taskStorageKey(convId))

    const tempUserMsg: ChatMessage = {
      id: Date.now(),
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    }
    setMessages((prev) => [...prev, tempUserMsg])

    resetStream()
    resetFollowing()
    let receivedAny = false
    let streamError = ''

    try {
      // 优先走 SSE 流式（token 流 + 工具过程可视）
      const stream = embedded ? chatApi.sendAssistantMessageStream : chatApi.sendMessageStream
      await stream(convId, content, {
        onRunStarted: ({ taskId: nextTaskId }) => {
          receivedAny = true
          if (nextTaskId > 0) {
            setTaskId(nextTaskId)
            sessionStorage.setItem(taskStorageKey(convId), String(nextTaskId))
          }
        },
        onToken: (t) => {
          receivedAny = true
          setStreamTool(null)
          pushToken(t)
        },
        onToolCallStart: ({ name }) => {
          receivedAny = true
          // 工具调用轮的过渡性文本不是最终回答，清空缓冲
          tokenBufRef.current = ''
          setStreamText('')
          setStreamTool(TOOL_LABELS[name] || `正在调用 ${name}…`)
        },
        onToolResult: () => {
          // 结果已就绪，等待模型基于数据继续回答
        },
        onPlan: (p) => {
          receivedAny = true
          setStreamTool(null)
          setPlan(p)
        },
        onApprovalRequired: (approval) => {
          receivedAny = true
          setPendingApprovals((previous) => (
            previous.some((item) => item.id === approval.id) ? previous : [...previous, approval]
          ))
        },
        onPaused: ({ taskId: pausedTaskId }) => {
          if (pausedTaskId > 0) {
            setTaskId(pausedTaskId)
            sessionStorage.setItem(taskStorageKey(convId), String(pausedTaskId))
          }
          setSending(false)
        },
        onDone: (m) => {
          receivedAny = true
          setMessages((prev) => [...prev, {
            id: m.message_id || Date.now() + 1,
            role: 'assistant',
            content: m.content,
            created_at: m.created_at || new Date().toISOString(),
          }])
          setTaskId(null)
          setPendingApprovals([])
          sessionStorage.removeItem(taskStorageKey(convId))
          requestAnimationFrame(() => inputRef.current?.focus())
        },
        onError: (message) => {
          receivedAny = true
          streamError = message
        },
      })
      setConversations((prev) =>
        prev.map((c) => c.id === convId ? { ...c, title: c.title || content.slice(0, 20) } : c)
      )
    } catch (e) {
      if (streamError) {
        if (!embedded) {
          setMessages((prev) => [...prev, {
            id: Date.now() + 1,
            role: 'assistant',
            content: streamError,
            created_at: new Date().toISOString(),
          }])
        }
      } else if (!receivedAny && !embedded) {
        // 流式完全不可用（旧后端/代理不支持等）→ 降级非流式端点
        try {
          const reply = await chatApi.sendMessage(convId, content)
          setMessages((prev) => [...prev, reply])
          setConversations((prev) =>
            prev.map((c) => c.id === convId ? { ...c, title: c.title || content.slice(0, 20) } : c)
          )
        } catch (e2) {
          const errMsg: ChatMessage = {
            id: Date.now() + 1,
            role: 'assistant',
            content: `请求失败：${e2 instanceof Error ? e2.message : '未知错误'}`,
            created_at: new Date().toISOString(),
          }
          setMessages((prev) => [...prev, errMsg])
        }
      } else if (embedded) {
        // 新助手不再追加“请求未完成”错误气泡；用户可直接重新提交。
      } else {
        // 已收到部分事件但流中断：生成在服务端继续并落库，稍后拉取最终消息
        await new Promise((r) => setTimeout(r, 1500))
        await loadMessages(convId)
      }
    } finally {
      resetStream()
      sendingRef.current = false
      setSending(false)
    }
  }, [input, sending, pendingApprovals.length, activeConvId, stockContext, pushToken, resetStream, loadMessages, resetFollowing, onConversationChange])

  const handleApprovalDecision = useCallback(async (
    approval: AssistantApproval,
    decision: 'approved' | 'rejected',
  ) => {
    const convId = activeConvId
    if (!convId || !taskId || decidingApprovalId) return

    setDecidingApprovalId(approval.id)
    setSending(true)
    resetStream()
    resetFollowing()
    let streamError = ''
    let resolvedApprovalId = ''

    try {
      await chatApi.decideAssistantApprovalStream(approval.id, decision, {
        onToken: (token) => {
          setStreamTool(null)
          pushToken(token)
        },
        onToolCallStart: ({ name }) => {
          tokenBufRef.current = ''
          setStreamText('')
          setStreamTool(TOOL_LABELS[name] || `正在调用 ${name}…`)
        },
        onToolResult: () => {
          // 工具结果到达后，等待模型继续输出最终回答。
        },
        onApprovalRequired: (nextApproval) => {
          setPendingApprovals((previous) => (
            previous.some((item) => item.id === nextApproval.id)
              ? previous
              : [...previous, nextApproval]
          ))
        },
        onPaused: ({ taskId: pausedTaskId, resolvedApprovalId: resolvedId, resolvedStatus }) => {
          if (pausedTaskId > 0) {
            setTaskId(pausedTaskId)
            sessionStorage.setItem(taskStorageKey(convId), String(pausedTaskId))
          }
          if (resolvedId && resolvedStatus) {
            resolvedApprovalId = resolvedId
            setPendingApprovals((previous) => previous.map((item) => (
              item.id === resolvedId ? { ...item, status: resolvedStatus } : item
            )))
          }
        },
        onDone: (message) => {
          setMessages((previous) => [...previous, {
            id: message.message_id || Date.now() + 1,
            role: 'assistant',
            content: message.content,
            created_at: message.created_at || new Date().toISOString(),
          }])
          setTaskId(null)
          setPendingApprovals([])
          sessionStorage.removeItem(taskStorageKey(convId))
          requestAnimationFrame(() => inputRef.current?.focus())
        },
        onError: (message) => {
          streamError = message
        },
      })

      if (streamError) throw new Error(streamError)
      // New hosts return the resolved card status in `paused`; old hosts did
      // not, so keep a compatibility fallback for their one-card behavior.
      if (!resolvedApprovalId) {
        setPendingApprovals((previous) => previous.filter((item) => item.id !== approval.id))
      }
    } catch (error) {
      // A decision is exactly-once on the server. If a browser loses the SSE
      // response after submitting it, rehydrate instead of inviting a blind
      // duplicate click that can only yield a conflict.
      let reconciled = false
      let terminalFailure = false
      try {
        const snapshot = await chatApi.getAssistantTask(taskId)
        if (snapshot.conversation_id === convId) {
          if (snapshot.status === 'awaiting_approval') {
            setPendingApprovals(snapshot.pending_approvals.map(approvalFromSnapshot))
            reconciled = true
          } else if (snapshot.status === 'completed') {
            await loadMessages(convId)
            setTaskId(null)
            setPendingApprovals([])
            sessionStorage.removeItem(taskStorageKey(convId))
            reconciled = true
          } else if (snapshot.status === 'failed' || snapshot.status === 'cancelled') {
            // The server cancels every unresolved card when a task fails. Do
            // the same in the current view so a stale card cannot be clicked
            // again after the checkpoint has been discarded.
            setTaskId(null)
            setPendingApprovals([])
            sessionStorage.removeItem(taskStorageKey(convId))
            terminalFailure = true
            reconciled = true
          }
        }
      } catch {
        // The original failure remains actionable when recovery is unavailable.
      }
      if (!reconciled || terminalFailure) {
        setMessages((previous) => [...previous, {
          id: Date.now() + 1,
          role: 'assistant',
          content: error instanceof Error ? error.message : '未知错误',
          created_at: new Date().toISOString(),
        }])
      }
      // The approval card owns its temporary disabled state. Re-throw so a
      // transport conflict or outage leaves the user a clear retry path.
      throw error
    } finally {
      resetStream()
      setSending(false)
      setDecidingApprovalId(null)
    }
  }, [activeConvId, taskId, decidingApprovalId, pushToken, resetStream, resetFollowing, loadMessages])

  const interactionLocked = sending || pendingApprovals.length > 0

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
        ? 'relative flex h-[calc(100dvh-12rem)] min-h-0 w-full overflow-hidden rounded-2xl border border-border/60 bg-card shadow-sm md:h-[calc(100dvh-8rem)]'
        : 'fixed bottom-0 right-0 z-50 flex h-full w-full flex-col overflow-hidden bg-background shadow-2xl md:bottom-5 md:right-5 md:h-[600px] md:w-[420px] md:rounded-xl md:border md:border-border/60'}>
        {embedded && (
          <div className="hidden w-64 shrink-0 md:flex">
            <AssistantSidebar
              conversations={conversations}
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
                activeConversationId={activeConvId}
                onOpen={(conversation) => { setHistoryOpen(false); void openConversation(conversation) }}
                onCreate={beginNewResearch}
                onDelete={(conversationId) => { void removeConversation(conversationId) }}
              />
            </div>
            <button
              type="button"
              aria-label="关闭历史会话"
              className="flex-1 bg-black/20"
              onClick={() => setHistoryOpen(false)}
            />
          </div>
        )}
        <div className={embedded
          ? 'relative flex min-w-0 min-h-0 flex-1 flex-col overflow-hidden'
          : 'relative flex h-full flex-col overflow-hidden'}>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/40 bg-accent/20">
        <div className="flex items-center gap-2">
          {embedded && (
            <button
              type="button"
              onClick={() => setHistoryOpen(true)}
              className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground md:hidden"
              aria-label="打开历史会话"
            >
              <Menu className="h-4 w-4" />
            </button>
          )}
          {view === 'chat' && (
            <button
              onClick={beginNewResearch}
              className="text-muted-foreground hover:text-foreground transition-colors"
              aria-label="返回助手首页"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
          )}
          <span className="text-[14px] font-semibold text-foreground">AI 助手</span>
          {view === 'chat' && stockContext && (
            <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-primary/10 text-primary">
              {stockContext.market}:{stockContext.symbol}
              {stockContext.stockName && ` ${stockContext.stockName}`}
              <button
                onClick={() => { setStockContext(null); setSuggestedQuestions([]) }}
                className="hover:text-primary/70 transition-colors"
              >
                <XCircle className="w-3 h-3" />
              </button>
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          {embedded && (
            <button
              type="button"
              onClick={() => setPermissionsOpen(true)}
              className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground"
              title="工具权限"
              aria-label="工具权限"
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

      {/* List view */}
      {view === 'list' && embedded && (
        <AssistantWelcome onSubmit={(question) => { void handleSend(question) }} disabled={interactionLocked} />
      )}
      {view === 'list' && !embedded && (
        <div className="flex-1 overflow-y-auto scrollbar">
          {conversations.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground text-[13px] gap-3">
              <MessageCircle className="w-8 h-8 opacity-30" />
              <p>暂无对话</p>
              <button
                onClick={createNewConversation}
                className="text-[12px] px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
              >
                开始新对话
              </button>
            </div>
          ) : (
            conversations.map((conv) => (
              <button
                key={conv.id}
                onClick={() => openConversation(conv)}
                className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-accent/30 transition-colors border-b border-border/20"
              >
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] text-foreground truncate">
                    {conv.title || '新对话'}
                  </div>
                  <div className="text-[11px] text-muted-foreground mt-0.5">
                    {conv.stock_symbol ? `${conv.stock_market}:${conv.stock_symbol} · ` : ''}
                    {new Date(conv.created_at).toLocaleDateString()}
                  </div>
                </div>
                <button
                  onClick={(e) => deleteConversation(conv.id, e)}
                  className="p-1 rounded text-muted-foreground/50 hover:text-rose-400 transition-colors shrink-0"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </button>
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
            className="min-h-0 flex-1 overflow-y-auto scrollbar px-4 py-3 space-y-3"
          >
            {/* Suggested questions */}
            {messages.length === 0 && suggestedQuestions.length > 0 && (
              <div className="flex flex-col gap-2">
                <span className="text-[11px] text-muted-foreground">推荐问题</span>
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
                <p>输入问题开始对话</p>
              </div>
            )}
            {messages.map((msg) => (
              <div
                key={msg.id}
                className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                <div
                  className={`max-w-[85%] rounded-xl px-3 py-2 text-[13px] leading-relaxed ${
                    msg.role === 'user'
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-accent/60 text-foreground'
                  }`}
                >
                  {msg.role === 'assistant' ? (
                    <div className="prose prose-sm dark:prose-invert max-w-none overflow-x-auto [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0.5 [&_h1]:text-[15px] [&_h2]:text-[14px] [&_h3]:text-[13px] [&_table]:my-2 [&_table]:w-full [&_table]:border-collapse [&_table]:text-[12px] [&_th]:border [&_th]:border-border/60 [&_th]:bg-background/30 [&_th]:px-2 [&_th]:py-1.5 [&_th]:font-semibold [&_td]:border [&_td]:border-border/60 [&_td]:px-2 [&_td]:py-1.5 [&_td]:align-top">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                    </div>
                  ) : (
                    msg.content
                  )}
                </div>
              </div>
            ))}
            {pendingApprovals.map((approval) => (
              <div key={approval.id} className="flex justify-start">
                <ApprovalCard
                  approval={approval}
                  onDecision={(decision) => handleApprovalDecision(approval, decision)}
                />
              </div>
            ))}
            {sending && plan && plan.steps.length > 0 && (
              // 计划驱动(全面诊断持仓)的计划卡片:步骤 + 状态
              <div className="flex justify-start">
                <div className="max-w-[85%] w-full rounded-xl px-3 py-2 text-[12px] bg-accent/40 border border-border/40">
                  <div className="font-medium text-foreground mb-1.5">
                    诊断计划{plan.status === 'done' ? '（已完成）' : plan.status === 'planning' ? '（生成中…）' : ''}
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
                <div className="max-w-[85%] rounded-xl px-3 py-2 text-[13px] leading-relaxed bg-accent/60 text-foreground">
                  <div className="prose prose-sm dark:prose-invert max-w-none overflow-x-auto [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0.5 [&_h1]:text-[15px] [&_h2]:text-[14px] [&_h3]:text-[13px] [&_table]:my-2 [&_table]:w-full [&_table]:border-collapse [&_table]:text-[12px] [&_th]:border [&_th]:border-border/60 [&_th]:bg-background/30 [&_th]:px-2 [&_th]:py-1.5 [&_th]:font-semibold [&_td]:border [&_td]:border-border/60 [&_td]:px-2 [&_td]:py-1.5 [&_td]:align-top">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{safeStreamMarkdown(streamText)}</ReactMarkdown>
                  </div>
                </div>
              </div>
            )}
            {sending && !streamText && pendingApprovals.length === 0 && (
              <div className="flex justify-start">
                <div
                  className="bg-accent/60 rounded-xl px-3 py-2 text-[13px] text-muted-foreground flex items-center gap-2"
                  role="status"
                  aria-label={streamTool || '正在请求助手回复'}
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
              aria-label="回到底部"
              title="滚动到最新消息"
            >
              <ArrowDown className="h-4 w-4 shrink-0" />
              <span className="hidden sm:inline">回到底部</span>
            </button>
          )}

          {/* Input */}
          <div data-testid="assistant-composer" className="flex shrink-0 items-center gap-2 px-4 py-3 border-t border-border/40">
            <input
              ref={inputRef}
              type="text"
              className="flex-1 h-9 px-3 rounded-lg bg-accent/40 text-[13px] text-foreground placeholder:text-muted-foreground outline-none focus:ring-1 focus:ring-primary/30"
              placeholder="输入问题..."
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
              className="h-9 w-9 rounded-lg bg-primary text-primary-foreground flex items-center justify-center hover:bg-primary/90 transition-colors disabled:opacity-50"
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
