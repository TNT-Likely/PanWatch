import { useCallback, useEffect } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import ChatWidget from '@/components/ChatWidget'

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
    />
  )
}
