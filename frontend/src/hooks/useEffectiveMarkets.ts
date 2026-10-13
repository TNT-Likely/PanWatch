import { useEffect, useMemo, useState } from 'react'
import { marketsApi, type EffectiveMarket } from '@panwatch/api'

// 后端与前端共用的唯一有效市场来源:GET /api/stocks/markets/enabled。
// 模块级缓存让多个选择器共享一次请求;后端不可用时回退核心市场,
// 保证选择器永不空白(旧后端/网络故障时维持 CN/HK/US 现状)。
const CORE_MARKETS: EffectiveMarket[] = [
  { code: 'CN', name: 'A股', optional: false, capabilities: ['quotes', 'klines', 'watchlist', 'holdings', 'search', 'news', 'alerts', 'analysis', 'discovery', 'strategy', 'paper'], currency: 'CNY' },
  { code: 'HK', name: '港股', optional: false, capabilities: ['quotes', 'klines', 'watchlist', 'holdings', 'search', 'news', 'alerts', 'analysis', 'discovery', 'strategy', 'paper'], currency: 'HKD' },
  { code: 'US', name: '美股', optional: false, capabilities: ['quotes', 'klines', 'watchlist', 'holdings', 'search', 'news', 'alerts', 'analysis', 'discovery', 'strategy', 'paper'], currency: 'USD' },
]

let cache: { at: number; markets: EffectiveMarket[] } | null = null
const TTL_MS = 5 * 60 * 1000

export function resetEffectiveMarketsCacheForTests() {
  cache = null
}

export function useEffectiveMarkets() {
  const [markets, setMarkets] = useState<EffectiveMarket[]>(cache?.markets ?? CORE_MARKETS)
  const [loading, setLoading] = useState(!cache)

  useEffect(() => {
    if (cache && Date.now() - cache.at < TTL_MS) return
    let alive = true
    marketsApi.getEffectiveMarkets()
      .then((res) => {
        const next = res.markets ?? []
        cache = { at: Date.now(), markets: next }
        if (alive) {
          setMarkets(next)
          setLoading(false)
        }
      })
      .catch(() => {
        if (alive) {
          setMarkets(CORE_MARKETS)
          setLoading(false)
        }
      })
    return () => {
      alive = false
    }
  }, [])

  const codes = useMemo(() => markets.map((m) => m.code), [markets])
  const capabilityCodes = useMemo(
    () => (cap: string) => markets.filter((m) => m.capabilities.includes(cap)).map((m) => m.code),
    [markets],
  )
  return { markets, codes, capabilityCodes, loading }
}
