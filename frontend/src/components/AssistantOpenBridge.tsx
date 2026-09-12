import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

export interface AssistantStockContext {
  symbol: string
  market: string
  stockName: string
  pageContext?: string
}

/**
 * Keeps page-level “问 AI” actions working after the assistant moved to a
 * route.  The event is translated to router state while the source modal is
 * still mounted; AssistantPage consumes that state after it mounts.
 */
export default function AssistantOpenBridge() {
  const navigate = useNavigate()

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent).detail as Partial<AssistantStockContext> | undefined
      if (!detail?.symbol || !detail.market) return
      navigate('/assistant', {
        state: {
          assistantContext: {
            symbol: detail.symbol,
            market: detail.market,
            stockName: detail.stockName || '',
            pageContext: detail.pageContext,
          } satisfies AssistantStockContext,
        },
      })
    }
    window.addEventListener('panwatch-open-chat', handler)
    return () => window.removeEventListener('panwatch-open-chat', handler)
  }, [navigate])

  return null
}
