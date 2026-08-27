import { useCallback, useEffect, useRef, useState } from 'react'
import { MessageCircle, X, Plus, Trash2, Send, ChevronLeft, XCircle } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import { chatApi, type ChatConversation, type ChatMessage } from '@panwatch/api'

interface StockContext {
  symbol: string
  market: string
  stockName: string
  pageContext?: string
}

// 工具名 → 过程可视化文案
const TOOL_LABELS: Record<string, string> = {
  get_portfolio: '正在查询持仓…',
  get_stock_quote: '正在查询行情…',
  get_technical_analysis: '正在分析技术面…',
  get_stock_suggestions: '正在查询 AI 建议…',
  get_watchlist: '正在查询自选股…',
}

/** 增量渲染容错：流式文本里未闭合的代码围栏先乐观闭合，避免 markdown 渲染爆版式 */
function safeStreamMarkdown(text: string): string {
  const fences = (text.match(/```/g) || []).length
  return fences % 2 === 1 ? `${text}\n\`\`\`` : text
}

export default function ChatWidget() {
  const [open, setOpen] = useState(false)
  const [conversations, setConversations] = useState<ChatConversation[]>([])
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
  const tokenBufRef = useRef('')
  const rafRef = useRef<number | null>(null)
  // 自动滚动：用户上滚即停，回到底部恢复
  const autoScrollRef = useRef(true)
  const scrollBoxRef = useRef<HTMLDivElement>(null)
  const endRef = useRef<HTMLDivElement>(null)

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

  const handleScroll = useCallback(() => {
    const box = scrollBoxRef.current
    if (!box) return
    // 距底部 40px 内视为"在底部"，恢复自动滚动；用户上滚则停
    autoScrollRef.current = box.scrollHeight - box.scrollTop - box.clientHeight < 40
  }, [])

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

      // Create a new conversation bound to this stock, with page context
      chatApi.createConversation({
        stock_symbol: detail.symbol,
        stock_market: detail.market,
        initial_context: detail.pageContext,
      }).then((conv) => {
        setActiveConvId(conv.id)
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
  }, [loadSuggestedQuestions])

  useEffect(() => {
    if (open) {
      loadConversations()
    }
  }, [open, loadConversations])

  useEffect(() => {
    if (autoScrollRef.current) {
      endRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages, streamText, streamTool])

  const openConversation = useCallback(async (conv: ChatConversation) => {
    setActiveConvId(conv.id)
    setView('chat')
    setSuggestedQuestions([])
    if (conv.stock_symbol && conv.stock_market) {
      setStockContext({ symbol: conv.stock_symbol, market: conv.stock_market, stockName: '' })
      loadSuggestedQuestions(conv.stock_symbol, conv.stock_market)
    } else {
      setStockContext(null)
    }
    await loadMessages(conv.id)
  }, [loadMessages, loadSuggestedQuestions])

  const createNewConversation = useCallback(async () => {
    try {
      const conv = await chatApi.createConversation()
      setActiveConvId(conv.id)
      setMessages([])
      setView('chat')
      setStockContext(null)
      setSuggestedQuestions([])
      setConversations((prev) => [conv, ...prev])
    } catch {
      // ignore
    }
  }, [])

  const deleteConversation = useCallback(async (convId: number, e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await chatApi.deleteConversation(convId)
      setConversations((prev) => prev.filter((c) => c.id !== convId))
      if (activeConvId === convId) {
        setActiveConvId(null)
        setMessages([])
        setView('list')
        setStockContext(null)
        setSuggestedQuestions([])
      }
    } catch {
      // ignore
    }
  }, [activeConvId])

  const handleSend = useCallback(async (overrideContent?: string) => {
    const content = (overrideContent || input).trim()
    if (!content || sending) return

    let convId = activeConvId
    if (!convId) {
      try {
        const conv = await chatApi.createConversation(
          stockContext ? { stock_symbol: stockContext.symbol, stock_market: stockContext.market } : undefined
        )
        convId = conv.id
        setActiveConvId(conv.id)
        setConversations((prev) => [conv, ...prev])
        setView('chat')
      } catch {
        return
      }
    }

    setInput('')
    setSending(true)
    setSuggestedQuestions([]) // hide after first send

    const tempUserMsg: ChatMessage = {
      id: Date.now(),
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    }
    setMessages((prev) => [...prev, tempUserMsg])

    resetStream()
    autoScrollRef.current = true
    let receivedAny = false

    try {
      // 优先走 SSE 流式（token 流 + 工具过程可视）
      await chatApi.sendMessageStream(convId, content, {
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
        onDone: (m) => {
          receivedAny = true
          setMessages((prev) => [...prev, {
            id: m.message_id || Date.now() + 1,
            role: 'assistant',
            content: m.content,
            created_at: m.created_at || new Date().toISOString(),
          }])
        },
      })
      setConversations((prev) =>
        prev.map((c) => c.id === convId ? { ...c, title: c.title || content.slice(0, 20) } : c)
      )
    } catch (e) {
      if (!receivedAny) {
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
      } else {
        // 已收到部分事件但流中断：生成在服务端继续并落库，稍后拉取最终消息
        await new Promise((r) => setTimeout(r, 1500))
        await loadMessages(convId)
      }
    } finally {
      resetStream()
      setSending(false)
    }
  }, [input, sending, activeConvId, stockContext, pushToken, resetStream, loadMessages])

  if (!open) {
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
    <div className="fixed bottom-0 right-0 z-50 w-full h-full md:w-[420px] md:h-[600px] md:bottom-5 md:right-5 md:rounded-xl bg-background border border-border/60 shadow-2xl flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/40 bg-accent/20">
        <div className="flex items-center gap-2">
          {view === 'chat' && (
            <button
              onClick={() => { setView('list'); setStockContext(null); setSuggestedQuestions([]); loadConversations() }}
              className="text-muted-foreground hover:text-foreground transition-colors"
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
          {view === 'list' && (
            <button
              onClick={createNewConversation}
              className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
              title="新建对话"
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

      {/* List view */}
      {view === 'list' && (
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
          <div ref={scrollBoxRef} onScroll={handleScroll} className="flex-1 overflow-y-auto scrollbar px-4 py-3 space-y-3">
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
                      disabled={sending}
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
                    <div className="prose prose-sm dark:prose-invert max-w-none [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0.5 [&_h1]:text-[15px] [&_h2]:text-[14px] [&_h3]:text-[13px]">
                      <ReactMarkdown>{msg.content}</ReactMarkdown>
                    </div>
                  ) : (
                    msg.content
                  )}
                </div>
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
                  <div className="prose prose-sm dark:prose-invert max-w-none [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0.5 [&_h1]:text-[15px] [&_h2]:text-[14px] [&_h3]:text-[13px]">
                    <ReactMarkdown>{safeStreamMarkdown(streamText)}</ReactMarkdown>
                  </div>
                </div>
              </div>
            )}
            {sending && !streamText && (
              <div className="flex justify-start">
                <div className="bg-accent/60 rounded-xl px-3 py-2 text-[13px] text-muted-foreground flex items-center gap-2">
                  <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                  {streamTool || '思考中...'}
                </div>
              </div>
            )}
            <div ref={endRef} />
          </div>

          {/* Input */}
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
                  handleSend()
                }
              }}
              disabled={sending}
            />
            <button
              className="h-9 w-9 rounded-lg bg-primary text-primary-foreground flex items-center justify-center hover:bg-primary/90 transition-colors disabled:opacity-50"
              onClick={() => handleSend()}
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
