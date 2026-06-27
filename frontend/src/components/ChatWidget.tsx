import { useCallback, useEffect, useRef, useState } from 'react'
import { MessageCircle, X, Plus, Trash2, Send, ChevronLeft, XCircle } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import { chatApi, type ChatConversation, type ChatMessage, type ChatPendingAction } from '@panwatch/api'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import ChatActionCard from './ChatActionCard'

const LAST_CONV_STORAGE_KEY = 'panwatch_chat_last_conv_id'

interface StockContext {
  symbol: string
  market: string
  stockName: string
  pageContext?: string
  initialMessage?: string
}

interface CreateConversationParams {
  stock_symbol?: string
  stock_market?: string
  initial_context?: string
}

function readLastConvId(): number | null {
  try {
    const raw = localStorage.getItem(LAST_CONV_STORAGE_KEY)
    if (!raw) return null
    const id = Number(raw)
    return Number.isFinite(id) && id > 0 ? id : null
  } catch {
    return null
  }
}

function writeLastConvId(id: number | null) {
  try {
    if (id == null) {
      localStorage.removeItem(LAST_CONV_STORAGE_KEY)
    } else {
      localStorage.setItem(LAST_CONV_STORAGE_KEY, String(id))
    }
  } catch {
    // ignore
  }
}

export default function ChatWidget() {
  const { toast } = useToast()
  const [open, setOpen] = useState(false)
  const [conversations, setConversations] = useState<ChatConversation[]>([])
  const [activeConvId, setActiveConvId] = useState<number | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [view, setView] = useState<'list' | 'chat'>('list')
  const [stockContext, setStockContext] = useState<StockContext | null>(null)
  const [suggestedQuestions, setSuggestedQuestions] = useState<string[]>([])
  const [actionLoadingId, setActionLoadingId] = useState<string | null>(null)
  const [opening, setOpening] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)

  const loadConversations = useCallback(async () => {
    try {
      const list = await chatApi.listConversations(30)
      setConversations(list)
    } catch {
      // ignore
    }
  }, [])

  const loadMessages = useCallback(async (convId: number) => {
    try {
      const detail = await chatApi.getConversation(convId)
      setMessages(detail.messages)
      return detail
    } catch {
      setMessages([])
      return null
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

  const removeConversationsFromState = useCallback((ids: number[]) => {
    if (ids.length === 0) return
    const idSet = new Set(ids)
    setConversations((prev) => prev.filter((c) => !idSet.has(c.id)))
  }, [])

  const deleteConversations = useCallback(async (ids: number[]) => {
    const uniqueIds = [...new Set(ids.filter((id) => id > 0))]
    if (uniqueIds.length === 0) return

    await Promise.all(uniqueIds.map((id) => chatApi.deleteConversation(id).catch(() => undefined)))
    removeConversationsFromState(uniqueIds)

    const lastId = readLastConvId()
    if (lastId != null && uniqueIds.includes(lastId)) {
      writeLastConvId(null)
    }
    if (activeConvId != null && uniqueIds.includes(activeConvId)) {
      setActiveConvId(null)
      setMessages([])
    }
  }, [activeConvId, removeConversationsFromState])

  const collectStockConversationIds = useCallback(async (symbol: string, market: string) => {
    try {
      const rows = await chatApi.findRecentConversations(symbol, market, 10)
      return rows.map((c) => c.id)
    } catch {
      return conversations
        .filter((c) => c.stock_symbol === symbol && c.stock_market === market)
        .map((c) => c.id)
    }
  }, [conversations])

  const openConversation = useCallback(async (conv: ChatConversation, stockName = '') => {
    setActiveConvId(conv.id)
    setView('chat')
    setSuggestedQuestions([])
    writeLastConvId(conv.id)

    if (conv.stock_symbol && conv.stock_market) {
      setStockContext({
        symbol: conv.stock_symbol,
        market: conv.stock_market,
        stockName,
      })
      loadSuggestedQuestions(conv.stock_symbol, conv.stock_market)
    } else {
      setStockContext(null)
    }

    await loadMessages(conv.id)
  }, [loadMessages, loadSuggestedQuestions])

  const createNewConversation = useCallback(async (
    params?: CreateConversationParams,
    options?: { deleteActive?: boolean; deleteSameStock?: boolean },
  ) => {
    const deleteActive = options?.deleteActive ?? true
    const deleteSameStock = options?.deleteSameStock ?? Boolean(params?.stock_symbol && params?.stock_market)
    const idsToDelete: number[] = []

    if (deleteActive && activeConvId) {
      idsToDelete.push(activeConvId)
    }
    if (deleteSameStock && params?.stock_symbol && params?.stock_market) {
      const stockIds = await collectStockConversationIds(params.stock_symbol, params.stock_market)
      idsToDelete.push(...stockIds)
    }

    await deleteConversations(idsToDelete)

    try {
      const conv = await chatApi.createConversation(params)
      setActiveConvId(conv.id)
      setMessages([])
      setView('chat')
      writeLastConvId(conv.id)
      setConversations((prev) => [conv, ...prev.filter((c) => c.id !== conv.id)])
      setSuggestedQuestions([])

      if (params?.stock_symbol && params?.stock_market) {
        setStockContext({
          symbol: params.stock_symbol,
          market: params.stock_market,
          stockName: stockContext?.symbol === params.stock_symbol && stockContext?.market === params.stock_market
            ? (stockContext.stockName || '')
            : '',
          pageContext: params.initial_context,
        })
        loadSuggestedQuestions(params.stock_symbol, params.stock_market)
      } else {
        setStockContext(null)
      }

      return conv
    } catch {
      toast('创建对话失败', 'error')
      return null
    }
  }, [
    activeConvId,
    collectStockConversationIds,
    deleteConversations,
    loadSuggestedQuestions,
    stockContext,
    toast,
  ])

  const resumeLastConversation = useCallback(async () => {
    const lastId = readLastConvId()
    if (!lastId) {
      setView('list')
      await loadConversations()
      return
    }

    const detail = await loadMessages(lastId)
    if (!detail) {
      writeLastConvId(null)
      setActiveConvId(null)
      setView('list')
      await loadConversations()
      return
    }

    setActiveConvId(lastId)
    setView('chat')
    const conv = detail.conversation
    if (conv.stock_symbol && conv.stock_market) {
      setStockContext({
        symbol: conv.stock_symbol,
        market: conv.stock_market,
        stockName: '',
      })
      loadSuggestedQuestions(conv.stock_symbol, conv.stock_market)
    } else {
      setStockContext(null)
    }
  }, [loadConversations, loadMessages, loadSuggestedQuestions])

  const openWidget = useCallback(async () => {
    setOpen(true)
    setOpening(true)
    try {
      await resumeLastConversation()
    } finally {
      setOpening(false)
    }
  }, [resumeLastConversation])

  const sendMessageToConversation = useCallback(async (convId: number, content: string) => {
    const trimmed = content.trim()
    if (!trimmed || sending) return

    setInput('')
    setSending(true)
    setSuggestedQuestions([])
    writeLastConvId(convId)
    setActiveConvId(convId)
    setView('chat')

    const tempUserMsg: ChatMessage = {
      id: Date.now(),
      role: 'user',
      content: trimmed,
      created_at: new Date().toISOString(),
    }
    setMessages((prev) => [...prev, tempUserMsg])

    try {
      const reply = await chatApi.sendMessage(convId, trimmed)
      setMessages((prev) => [...prev, reply])
      setConversations((prev) =>
        prev.map((c) => (c.id === convId ? { ...c, title: c.title || trimmed.slice(0, 20) } : c)),
      )
    } catch (e) {
      const errMsg: ChatMessage = {
        id: Date.now() + 1,
        role: 'assistant',
        content: `请求失败：${e instanceof Error ? e.message : '未知错误'}`,
        created_at: new Date().toISOString(),
      }
      setMessages((prev) => [...prev, errMsg])
    } finally {
      setSending(false)
    }
  }, [sending])

  const openChatFromStock = useCallback(async (detail: StockContext) => {
    const initialMessage = detail.initialMessage?.trim() || ''
    setOpen(true)
    setStockContext(detail)
    setOpening(true)

    try {
      let convId: number | null = null
      const recent = await chatApi.findRecentConversations(detail.symbol, detail.market, 1)
      if (recent.length > 0) {
        const conv = recent[0]
        await openConversation(conv, detail.stockName)
        setStockContext({
          symbol: detail.symbol,
          market: detail.market,
          stockName: detail.stockName,
          pageContext: detail.pageContext,
        })
        convId = conv.id
      } else {
        const conv = await createNewConversation(
          {
            stock_symbol: detail.symbol,
            stock_market: detail.market,
            initial_context: detail.pageContext,
          },
          { deleteActive: false, deleteSameStock: false },
        )
        if (conv) {
          setStockContext({
            symbol: detail.symbol,
            market: detail.market,
            stockName: detail.stockName,
            pageContext: detail.pageContext,
          })
          convId = conv.id
        }
      }

      if (initialMessage && convId) {
        await sendMessageToConversation(convId, initialMessage)
      }
    } catch {
      setView('chat')
      toast('打开 AI 助手失败', 'error')
    } finally {
      setOpening(false)
    }
  }, [createNewConversation, openConversation, sendMessageToConversation, toast])

  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as StockContext
      if (!detail?.symbol) return
      void openChatFromStock(detail)
    }
    window.addEventListener('panwatch-open-chat', handler)
    return () => window.removeEventListener('panwatch-open-chat', handler)
  }, [openChatFromStock])

  useEffect(() => {
    if (open && view === 'list') {
      loadConversations()
    }
  }, [open, view, loadConversations])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const deleteConversation = useCallback(async (convId: number, e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await deleteConversations([convId])
      if (activeConvId === convId) {
        setView('list')
        setStockContext(null)
        setSuggestedQuestions([])
      }
    } catch {
      toast('删除失败', 'error')
    }
  }, [activeConvId, deleteConversations, toast])

  const handleSend = useCallback(async (overrideContent?: string) => {
    const content = (overrideContent || input).trim()
    if (!content || sending) return

    let convId = activeConvId
    if (!convId) {
      const conv = await createNewConversation(
        stockContext
          ? {
              stock_symbol: stockContext.symbol,
              stock_market: stockContext.market,
              initial_context: stockContext.pageContext,
            }
          : undefined,
        { deleteActive: true, deleteSameStock: Boolean(stockContext) },
      )
      if (!conv) return
      convId = conv.id
    }

    await sendMessageToConversation(convId, content)
  }, [input, sending, activeConvId, stockContext, createNewConversation, sendMessageToConversation])

  const updateMessageAction = useCallback((actionId: string, nextAction: ChatPendingAction) => {
    setMessages((prev) =>
      prev.map((msg) => {
        if (!msg.pending_actions?.some((a) => a.id === actionId)) return msg
        return {
          ...msg,
          pending_actions: msg.pending_actions.map((a) => (a.id === actionId ? nextAction : a)),
        }
      }),
    )
  }, [])

  const handleConfirmAction = useCallback(async (actionId: string) => {
    setActionLoadingId(actionId)
    try {
      const res = await chatApi.confirmAction(actionId)
      updateMessageAction(actionId, res.action)
      window.dispatchEvent(new CustomEvent('panwatch-portfolio-changed'))
      toast('操作已执行', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '操作失败', 'error')
    } finally {
      setActionLoadingId(null)
    }
  }, [updateMessageAction, toast])

  const handleCancelAction = useCallback(async (actionId: string) => {
    setActionLoadingId(actionId)
    try {
      const res = await chatApi.cancelAction(actionId)
      updateMessageAction(actionId, res.action)
    } catch (e) {
      toast(e instanceof Error ? e.message : '取消失败', 'error')
    } finally {
      setActionLoadingId(null)
    }
  }, [updateMessageAction, toast])

  const handleStartNewConversation = useCallback(async () => {
    if (stockContext) {
      await createNewConversation(
        {
          stock_symbol: stockContext.symbol,
          stock_market: stockContext.market,
          initial_context: stockContext.pageContext,
        },
        { deleteActive: true, deleteSameStock: true },
      )
      return
    }
    await createNewConversation(undefined, { deleteActive: true, deleteSameStock: false })
  }, [createNewConversation, stockContext])

  if (!open) {
    return (
      <button
        onClick={() => void openWidget()}
        className="fixed bottom-20 right-4 md:bottom-5 md:right-5 z-40 w-12 h-12 rounded-full bg-primary text-primary-foreground shadow-lg flex items-center justify-center hover:bg-primary/90 transition-all hover:scale-105"
      >
        <MessageCircle className="w-5 h-5" />
      </button>
    )
  }

  return (
    <div className="fixed bottom-0 right-0 z-50 w-full h-full md:w-[420px] md:h-[600px] md:bottom-5 md:right-5 md:rounded-xl bg-background border border-border/60 shadow-2xl flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/40 bg-accent/20">
        <div className="flex items-center gap-2 min-w-0">
          {view === 'chat' && (
            <button
              onClick={() => {
                setView('list')
                setSuggestedQuestions([])
                void loadConversations()
              }}
              className="text-muted-foreground hover:text-foreground transition-colors shrink-0"
              title="历史对话"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
          )}
          <span className="text-[14px] font-semibold text-foreground shrink-0">AI 助手</span>
          {view === 'chat' && stockContext && (
            <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-primary/10 text-primary min-w-0">
              <span className="truncate">
                {stockContext.market}:{stockContext.symbol}
                {stockContext.stockName && ` ${stockContext.stockName}`}
              </span>
              <button
                onClick={() => {
                  setStockContext(null)
                  setSuggestedQuestions([])
                }}
                className="hover:text-primary/70 transition-colors shrink-0"
              >
                <XCircle className="w-3 h-3" />
              </button>
            </span>
          )}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {(view === 'list' || view === 'chat') && (
            <button
              onClick={() => void handleStartNewConversation()}
              className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
              title="新对话（将删除当前会话记忆）"
            >
              <Plus className="w-4 h-4" />
            </button>
          )}
          <button
            onClick={() => setOpen(false)}
            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {opening && (
        <div className="px-4 py-2 text-[12px] text-muted-foreground border-b border-border/20">
          加载中...
        </div>
      )}

      {/* List view */}
      {view === 'list' && (
        <div className="flex-1 overflow-y-auto scrollbar">
          {conversations.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground text-[13px] gap-3">
              <MessageCircle className="w-8 h-8 opacity-30" />
              <p>暂无对话</p>
              <button
                onClick={() => void createNewConversation()}
                className="text-[12px] px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
              >
                开始新对话
              </button>
            </div>
          ) : (
            conversations.map((conv) => (
              <button
                key={conv.id}
                onClick={() => void openConversation(conv)}
                className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-accent/30 transition-colors border-b border-border/20"
              >
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] text-foreground truncate">
                    {conv.title || '新对话'}
                  </div>
                  <div className="text-[11px] text-muted-foreground mt-0.5">
                    {conv.stock_symbol ? `${conv.stock_market}:${conv.stock_symbol} · ` : ''}
                    {new Date(conv.updated_at || conv.created_at).toLocaleString()}
                  </div>
                </div>
                <button
                  onClick={(e) => void deleteConversation(conv.id, e)}
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
          <div className="flex-1 overflow-y-auto scrollbar px-4 py-3 space-y-3">
            {messages.length === 0 && suggestedQuestions.length > 0 && (
              <div className="flex flex-col gap-2">
                <span className="text-[11px] text-muted-foreground">推荐问题</span>
                <div className="flex flex-wrap gap-2">
                  {suggestedQuestions.map((q) => (
                    <button
                      key={q}
                      className="text-[11px] px-3 py-1.5 rounded-full bg-primary/10 text-primary hover:bg-primary/20 transition-colors text-left"
                      onClick={() => void handleSend(q)}
                      disabled={sending}
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.length === 0 && suggestedQuestions.length === 0 && !sending && !opening && (
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
                    <>
                      <div className="prose prose-sm dark:prose-invert max-w-none [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0.5 [&_h1]:text-[15px] [&_h2]:text-[14px] [&_h3]:text-[13px]">
                        <ReactMarkdown>{msg.content}</ReactMarkdown>
                      </div>
                      {(msg.pending_actions || []).map((action) => (
                        <ChatActionCard
                          key={action.id}
                          action={action}
                          loading={actionLoadingId === action.id}
                          onConfirm={handleConfirmAction}
                          onCancel={handleCancelAction}
                        />
                      ))}
                    </>
                  ) : (
                    msg.content
                  )}
                </div>
              </div>
            ))}
            {sending && (
              <div className="flex justify-start">
                <div className="bg-accent/60 rounded-xl px-3 py-2 text-[13px] text-muted-foreground flex items-center gap-2">
                  <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                  思考中...
                </div>
              </div>
            )}
            <div ref={endRef} />
          </div>

          <div className="flex items-center gap-2 px-4 py-3 border-t border-border/40">
            <input
              type="text"
              className="flex-1 h-9 px-3 rounded-lg bg-accent/40 text-[13px] text-foreground placeholder:text-muted-foreground outline-none focus:ring-1 focus:ring-primary/30"
              placeholder="输入问题..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  void handleSend()
                }
              }}
              disabled={sending}
            />
            <button
              className="h-9 w-9 rounded-lg bg-primary text-primary-foreground flex items-center justify-center hover:bg-primary/90 transition-colors disabled:opacity-50"
              onClick={() => void handleSend()}
              disabled={sending || !input.trim()}
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </>
      )}
    </div>
  )
}
