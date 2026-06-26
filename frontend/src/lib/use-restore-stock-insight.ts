import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import type { InsightTab } from '@panwatch/biz-ui/components/stock-insight-modal'

export interface StockInsightReturnState {
  symbol: string
  market: string
  name?: string
  tab?: InsightTab
  hasPosition?: boolean
}

export function useRestoreStockInsight(onRestore: (payload: StockInsightReturnState) => void) {
  const location = useLocation()
  const navigate = useNavigate()

  useEffect(() => {
    const payload = (location.state as { restoreStockInsight?: StockInsightReturnState } | null)?.restoreStockInsight
    if (!payload?.symbol) return
    onRestore(payload)
    navigate({ pathname: location.pathname, search: location.search }, { replace: true, state: null })
  }, [location.pathname, location.search, location.state, navigate, onRestore])
}
