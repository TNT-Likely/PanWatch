import { useEffect, useMemo, useState } from 'react'
import { marketsApi, type EffectiveMarket } from '@panwatch/api'

// 后端与前端共用的唯一有效市场来源:GET /api/stocks/markets/enabled。
// 模块级缓存让多个选择器共享一次请求(并发去重 + 结果广播给所有已挂载实例);
// 后端不可用时回退核心市场,保证选择器永不空白。
const CORE_MARKETS: EffectiveMarket[] = [
  { code: 'CN', name: 'A股', optional: false, capabilities: ['quotes', 'klines', 'watchlist', 'holdings', 'search', 'news', 'alerts', 'analysis', 'discovery', 'strategy', 'paper'], currency: 'CNY' },
  { code: 'HK', name: '港股', optional: false, capabilities: ['quotes', 'klines', 'watchlist', 'holdings', 'search', 'news', 'alerts', 'analysis', 'discovery', 'strategy', 'paper'], currency: 'HKD' },
  { code: 'US', name: '美股', optional: false, capabilities: ['quotes', 'klines', 'watchlist', 'holdings', 'search', 'news', 'alerts', 'analysis', 'discovery', 'strategy', 'paper'], currency: 'USD' },
]

let cache: { at: number; markets: EffectiveMarket[] } | null = null
let inflight: Promise<void> | null = null
const listeners = new Set<(markets: EffectiveMarket[]) => void>()
const TTL_MS = 5 * 60 * 1000

// 并发调用共享同一请求;完成后广播给所有订阅实例,后到实例不会互相覆盖
function fetchMarkets(): Promise<void> {
  if (!inflight) {
    inflight = marketsApi
      .getEffectiveMarkets()
      .then((res) => {
        cache = { at: Date.now(), markets: res.markets ?? [] }
        listeners.forEach((fn) => fn(cache!.markets))
      })
      .catch(() => {
        listeners.forEach((fn) => fn(CORE_MARKETS))
      })
      .finally(() => {
        inflight = null
      })
  }
  return inflight
}

export function resetEffectiveMarketsCacheForTests() {
  cache = null
  listeners.clear()
}

export function useEffectiveMarkets() {
  const [markets, setMarkets] = useState<EffectiveMarket[]>(cache?.markets ?? CORE_MARKETS)
  const [loading, setLoading] = useState(!cache)

  useEffect(() => {
    listeners.add(setMarkets)
    let alive = true
    if (!cache || Date.now() - cache.at >= TTL_MS) {
      fetchMarkets().then(() => {
        if (alive) setLoading(false)
      })
    } else {
      setLoading(false)
    }
    return () => {
      alive = false
      listeners.delete(setMarkets)
    }
  }, [])

  const codes = useMemo(() => markets.map((m) => m.code), [markets])
  const capabilityCodes = useMemo(
    () => (cap: string) => markets.filter((m) => m.capabilities.includes(cap)).map((m) => m.code),
    [markets],
  )
  return { markets, codes, capabilityCodes, loading }
}
