import { useCallback, useEffect, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import ChatWidget from '@/components/ChatWidget'
import type { AssistantStockContext } from '@/components/AssistantOpenBridge'

function parseConversationId(rawId: string | undefined): number | null {
  if (!rawId || !/^\d+$/.test(rawId)) return null
  const id = Number(rawId)
  return Number.isSafeInteger(id) && id > 0 ? id : null
}

/** Navigation-level home for the PanAgent-powered interactive assistant. */
export default function AssistantPage() {
  const { conversationId: rawConversationId } = useParams<{ conversationId?: string }>()
  const conversationId = parseConversationId(rawConversationId)
  const navigate = useNavigate()
  const location = useLocation()
  // 消费即焚:首帧把 location.state 里的上下文固定成快照,随后立即用 replace 剥离
  // state——否则只要用户停留在本页,任何后续 remount 都会重新拿到旧上下文,
  // 触发 ChatWidget 再次创建会话(实测一次点击建出两个会话的成因之一)。
  const [launchContext] = useState(
    () => (location.state as { assistantContext?: AssistantStockContext } | null)?.assistantContext || null,
  )

  useEffect(() => {
    if ((location.state as { assistantContext?: unknown } | null)?.assistantContext) {
      navigate(location.pathname + location.search + location.hash, { replace: true })
    }
  }, [location.key, location.pathname, location.search, location.hash, navigate])

  const setConversationId = useCallback((nextId: number | null, options?: { replace?: boolean }) => {
    const nextPath = nextId == null ? '/assistant' : `/assistant/${nextId}`
    navigate(
      { pathname: nextPath, search: location.search, hash: location.hash },
      { replace: options?.replace === true },
    )
  }, [location.hash, location.search, navigate])

  // A malformed path should not leave the page in a state that can never be
  // rehydrated. Replace it so a browser Back action still returns to the prior
  // real page rather than visiting the malformed URL again.
  const malformedConversationPath = rawConversationId !== undefined && conversationId === null

  useEffect(() => {
    if (malformedConversationPath) setConversationId(null, { replace: true })
  }, [malformedConversationPath, setConversationId])

  if (malformedConversationPath) {
    return null
  }

  return (
    <ChatWidget
      embedded
      conversationIdFromUrl={conversationId}
      onConversationChange={setConversationId}
      initialStockContext={launchContext}
    />
  )
}
