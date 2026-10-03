import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Layers, RefreshCw } from 'lucide-react'
import {
  dashboardApi,
  discoveryApi,
  type DashboardMonitorStock,
  type DashboardPortfolioSummary,
  type DashboardWatchStock,
  type HotStockItem,
  type HotBoardItem,
} from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import { useLocalStorage } from '@/lib/utils'
import { useTranslation } from 'react-i18next'
import { marketSignTextClass } from '@/lib/market-colors'

interface Props {
  monitorStocks: DashboardMonitorStock[]
  portfolioSummary: DashboardPortfolioSummary | null
  onOpenStock: (symbol: string, market: string, name?: string, hasPosition?: boolean) => void
}

export default function DiscoveryPanel({ monitorStocks, portfolioSummary: portfolioRaw, onOpenStock }: Props) {
  const { t } = useTranslation('configuration')
  const discoveryT = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const tr = useCallback((key: string, options?: Record<string, unknown>) => discoveryT(`p4.components.discovery.${key}`, options), [discoveryT])
  const marketLabel = useCallback((market: string) => discoveryT(`stocksPage.markets.${({ CN: 'cn', HK: 'hk', US: 'us' } as Record<string, string>)[market] || market}`), [discoveryT])
  const navigate = useNavigate()
  const [watchlist, setWatchlist] = useState<DashboardWatchStock[] | null>(null)

  const [discoverTab, setDiscoverTab] = useLocalStorage<'boards' | 'stocks'>('panwatch_dashboard_discoverTab', 'boards')
  const [discoverMarket, setDiscoverMarket] = useLocalStorage<'CN' | 'HK' | 'US'>('panwatch_dashboard_discoverMarket', 'CN')
  const [stocksMode, setStocksMode] = useLocalStorage<'turnover' | 'gainers' | 'for_you'>('panwatch_dashboard_stocksMode', 'for_you')
  const [boardsMode, setBoardsMode] = useLocalStorage<'gainers' | 'turnover'>('panwatch_dashboard_boardsMode', 'gainers')
  const [hotStocks, setHotStocks] = useState<HotStockItem[]>([])
  const [hotBoards, setHotBoards] = useState<HotBoardItem[]>([])
  const [discoverLoading, setDiscoverLoading] = useState(false)
  const [discoverError, setDiscoverError] = useState('')
  const [boardDialogOpen, setBoardDialogOpen] = useState(false)
  const [activeBoard, setActiveBoard] = useState<HotBoardItem | null>(null)
  const [boardStocks, setBoardStocks] = useState<HotStockItem[]>([])
  const requestId = useRef(0)
  const discoveryCacheRef = useRef<{
    boards: Record<string, { ts: number; data: HotBoardItem[] }>
    stocks: Record<string, { ts: number; data: HotStockItem[] }>
  }>({ boards: {}, stocks: {} })

  useEffect(() => {
    if (discoverTab !== 'stocks' || stocksMode !== 'for_you' || watchlist !== null) return
    let alive = true
    dashboardApi.watchlist().then((value) => { if (alive) setWatchlist(value) }).catch(() => {})
    return () => { alive = false }
  }, [discoverTab, stocksMode, watchlist])

  const watchlistSet = useMemo(
    () => new Set((watchlist || []).map((s) => `${s.market}:${s.symbol}`)),
    [watchlist],
  )
  const holdingSet = useMemo(() => {
    const set = new Set<string>()
    for (const acc of portfolioRaw?.accounts || []) for (const p of acc.positions || []) set.add(`${p.market}:${p.symbol}`)
    return set
  }, [portfolioRaw])
  const stylePreference = useMemo(() => {
    const score: Record<string, number> = { short: 0, swing: 0, long: 0 }
    for (const acc of portfolioRaw?.accounts || [])
      for (const p of acc.positions || []) {
        if (p.trading_style && p.trading_style in score) score[p.trading_style] += 1
      }
    const ranked = Object.entries(score).sort((a, b) => b[1] - a[1])
    return ranked[0]?.[1] ? ranked[0][0] : null
  }, [portfolioRaw])

  const loadDiscovery = async (which?: 'boards' | 'stocks', opts?: { force?: boolean }) => {
    const id = ++requestId.current
    const tab = which || discoverTab
    const force = !!opts?.force
    const cacheKey = tab === 'boards' ? `${discoverMarket}:${boardsMode}` : `${discoverMarket}:${stocksMode}`
    const now = Date.now()
    const ttlMs = 60 * 1000
    const cache = tab === 'boards' ? discoveryCacheRef.current.boards[cacheKey] : discoveryCacheRef.current.stocks[cacheKey]
    if (!force && cache && now - cache.ts < ttlMs) {
      if (tab === 'boards') setHotBoards(cache.data as HotBoardItem[])
      else setHotStocks(cache.data as HotStockItem[])
      setDiscoverLoading(false)
      setDiscoverError('')
      return
    }
    setDiscoverLoading(true)
    setDiscoverError('')
    try {
      if (tab === 'boards') {
        const items = (await discoveryApi.listHotBoards({ market: discoverMarket, mode: boardsMode, limit: 12 })) || []
        if (id === requestId.current) setHotBoards(items)
        discoveryCacheRef.current.boards[cacheKey] = { ts: now, data: items }
      } else if (stocksMode === 'for_you') {
        const [turnoverItems, gainerItems] = await Promise.all([
          discoveryApi.listHotStocks({ market: discoverMarket, mode: 'turnover', limit: 20 }),
          discoveryApi.listHotStocks({ market: discoverMarket, mode: 'gainers', limit: 20 }),
        ])
        const map = new Map<string, HotStockItem>()
        for (const item of [...(turnoverItems || []), ...(gainerItems || [])]) map.set(item.symbol, item)
        const items = Array.from(map.values())
        if (id === requestId.current) setHotStocks(items)
        discoveryCacheRef.current.stocks[cacheKey] = { ts: now, data: items }
      } else {
        const items = (await discoveryApi.listHotStocks({ market: discoverMarket, mode: stocksMode, limit: 20 })) || []
        if (id === requestId.current) setHotStocks(items)
        discoveryCacheRef.current.stocks[cacheKey] = { ts: now, data: items }
      }
    } catch (e) {
      if (id === requestId.current) {
        setDiscoverError(e instanceof Error ? e.message : tr('loadFailed'))
        if (tab === 'boards') setHotBoards([])
        else setHotStocks([])
      }
    } finally {
      if (id === requestId.current) setDiscoverLoading(false)
    }
  }

  const openBoard = async (b: HotBoardItem) => {
    setActiveBoard(b)
    setBoardStocks([])
    setBoardDialogOpen(true)
    try {
      setBoardStocks((await discoveryApi.listBoardStocks(b.code, { mode: 'gainers', limit: 20 })) || [])
    } catch {
      setBoardStocks([])
    }
  }

  const personalizedHotStocks = useMemo(() => {
    const monitorMap = new Map<string, DashboardMonitorStock>()
    for (const s of monitorStocks || []) monitorMap.set(`${s.market}:${s.symbol}`, s)
    const scored = (hotStocks || []).map((stock) => {
      const market = stock.market || discoverMarket
      const key = `${market}:${stock.symbol}`
      const reasons: string[] = []
      let score = 0
      const pctAbs = Math.abs(stock.change_pct || 0)
      score += Math.min((stock.turnover || 0) / 1e8, 8) + pctAbs * 0.6
      if (holdingSet.has(key)) {
        score += 10
        reasons.push(tr('reasons.holding'))
      } else if (watchlistSet.has(key)) {
        score += 6
        reasons.push(tr('reasons.watchlist'))
      }
      const monitor = monitorMap.get(key)
      if (monitor?.alert_type) {
        score += 5
        reasons.push(tr('reasons.monitor'))
      }
      if (stylePreference === 'short' && pctAbs >= 3) {
        score += 3
        reasons.push(tr('reasons.short'))
      } else if (stylePreference === 'swing' && pctAbs >= 1.5 && pctAbs <= 6) {
        score += 2
        reasons.push(tr('reasons.swing'))
      } else if (stylePreference === 'long' && pctAbs <= 4) {
        score += 2
        reasons.push(tr('reasons.long'))
      }
      if (reasons.length === 0) reasons.push(tr('reasons.active'))
      return { ...stock, _score: score, _reasons: reasons.slice(0, 2) }
    })
    return scored.sort((a, b) => b._score - a._score)
  }, [hotStocks, holdingSet, watchlistSet, stylePreference, monitorStocks, discoverMarket, tr])

  const visibleHotStocks = useMemo(
    () => (stocksMode === 'for_you' ? personalizedHotStocks.slice(0, 8) : hotStocks.slice(0, 8)),
    [stocksMode, personalizedHotStocks, hotStocks],
  )

  useEffect(() => {
    // 只加载正在显示的标签；切换时复用各标签的短缓存。
    void loadDiscovery(discoverTab)
    return () => { requestId.current += 1 }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [discoverTab, discoverMarket, boardsMode, stocksMode])

  return (
    <>
      <div className="mt-3">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Layers className="h-4 w-4 text-primary" />
            {tr('title')}
          </h2>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => navigate('/opportunities')} className="h-7 text-[12px]">
              {tr('openOpportunities')}
            </Button>
            <Select value={discoverMarket} onValueChange={(v) => setDiscoverMarket(v as 'CN' | 'HK' | 'US')}>
              <SelectTrigger className="h-7 w-[90px] text-[12px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="CN">{marketLabel('CN')}</SelectItem>
                <SelectItem value="HK">{marketLabel('HK')}</SelectItem>
                <SelectItem value="US">{marketLabel('US')}</SelectItem>
              </SelectContent>
            </Select>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => loadDiscovery(undefined, { force: true })}
              disabled={discoverLoading}
              className="h-7 text-[12px]"
              title={tr('refresh')}
            >
              {discoverLoading ? (
                <span className="h-3 w-3 animate-spin rounded-full border-2 border-current/30 border-t-current" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
            </Button>
          </div>
        </div>

        <div className="card p-4">
          <div className="mb-3 flex items-center gap-1.5">
            <button
              onClick={() => setDiscoverTab('boards')}
              className={`rounded px-2.5 py-1 text-[11px] transition-colors ${discoverTab === 'boards' ? 'bg-primary text-primary-foreground' : 'bg-accent/50 text-muted-foreground hover:bg-accent'}`}
            >
              {tr('boards')}
            </button>
            <button
              onClick={() => setDiscoverTab('stocks')}
              className={`rounded px-2.5 py-1 text-[11px] transition-colors ${discoverTab === 'stocks' ? 'bg-primary text-primary-foreground' : 'bg-accent/50 text-muted-foreground hover:bg-accent'}`}
            >
              {tr('stocks')}
            </button>
            <div className="ml-auto flex items-center gap-2">
              {discoverTab === 'boards' ? (
                <Select value={boardsMode} onValueChange={(v) => setBoardsMode(v as 'gainers' | 'turnover')}>
                  <SelectTrigger className="h-7 w-[110px] text-[12px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="gainers">{tr('gainers')}</SelectItem>
                    <SelectItem value="turnover">{tr('turnover')}</SelectItem>
                  </SelectContent>
                </Select>
              ) : (
                <Select value={stocksMode} onValueChange={(v) => setStocksMode(v as 'turnover' | 'gainers' | 'for_you')}>
                  <SelectTrigger className="h-7 w-[110px] text-[12px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="for_you">{tr('forYou')}</SelectItem>
                    <SelectItem value="turnover">{tr('turnover')}</SelectItem>
                    <SelectItem value="gainers">{tr('gainers')}</SelectItem>
                  </SelectContent>
                </Select>
              )}
            </div>
          </div>

          {discoverLoading ? (
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="animate-pulse rounded-xl bg-accent/20 p-3">
                  <div className="mb-2 h-3 w-24 rounded bg-accent/60" />
                  <div className="h-3 w-16 rounded bg-accent/50" />
                </div>
              ))}
            </div>
          ) : discoverTab === 'boards' ? (
            hotBoards.length === 0 ? (
              <div className="py-6 text-center text-[12px] text-muted-foreground">
                {discoverError || (discoverMarket === 'CN' ? tr('noData') : tr('noBoardsMarket', { market: marketLabel(discoverMarket) }))}
                {discoverMarket !== 'CN' && (
                  <div className="mt-2">
                    <Button variant="ghost" size="sm" className="h-7 text-[11px]" onClick={() => setDiscoverTab('stocks')}>
                      {tr('switchToStocks')}
                    </Button>
                  </div>
                )}
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                {hotBoards.slice(0, 6).map((b) => {
                  const pct = b.change_pct ?? 0
                  const color = marketSignTextClass(pct)
                  return (
                    <button
                      key={b.code}
                      onClick={() => openBoard(b)}
                      className="flex items-center justify-between gap-3 rounded-xl bg-accent/20 p-3 text-left transition-colors hover:bg-accent/35"
                      title={tr('boardConstituentsTitle')}
                    >
                      <div className="min-w-0">
                        <div className="truncate text-[13px] font-medium text-foreground">{b.name}</div>
                        <div className="truncate font-mono text-[11px] text-muted-foreground">{b.code}</div>
                      </div>
                      <div className={`font-mono text-[12px] font-semibold ${color}`}>{pct >= 0 ? '+' : ''}{pct.toFixed(2)}%</div>
                    </button>
                  )
                })}
              </div>
            )
          ) : hotStocks.length === 0 ? (
            <div className="py-6 text-center text-[12px] text-muted-foreground">{discoverError || tr('noData')}</div>
          ) : (
            <div className="space-y-2">
              {stocksMode === 'for_you' && <div className="px-1 text-[11px] text-muted-foreground">{tr('personalizedHint')}</div>}
              <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                {visibleHotStocks.slice(0, 6).map((s) => {
                  const pct = s.change_pct ?? 0
                  const color = marketSignTextClass(pct)
                  const reasons = (s as HotStockItem & { _reasons?: string[] })._reasons
                  return (
                    <div
                      key={`${s.market || discoverMarket}:${s.symbol}`}
                      onClick={() => onOpenStock(s.symbol, s.market || discoverMarket, s.name, false)}
                      className="flex cursor-pointer items-center justify-between gap-3 rounded-xl bg-accent/20 p-3 text-left transition-colors hover:bg-accent/35"
                      title={tr('openStockTitle')}
                    >
                      <div className="min-w-0">
                        <div className="truncate text-[13px] font-medium text-foreground">{s.name}</div>
                        <div className="font-mono text-[11px] text-muted-foreground">{s.market || discoverMarket}:{s.symbol}</div>
                        {reasons && reasons.length > 0 && (
                          <div className="mt-0.5 truncate text-[10px] text-muted-foreground">{reasons.join(' · ')}</div>
                        )}
                      </div>
                      <div className="text-right">
                        <div className="font-mono text-[12px] text-foreground">{s.price != null ? s.price.toFixed(2) : '--'}</div>
                        <div className={`font-mono text-[11px] ${color}`}>{pct >= 0 ? '+' : ''}{pct.toFixed(2)}%</div>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      </div>

      <Dialog open={boardDialogOpen} onOpenChange={setBoardDialogOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{activeBoard ? tr('boardTitle', { name: activeBoard.name }) : tr('boardConstituents')}</DialogTitle>
            <DialogDescription>{tr('boardDescription')}</DialogDescription>
          </DialogHeader>
          {boardStocks.length === 0 ? (
            <div className="py-6 text-center text-[12px] text-muted-foreground">{tr('noData')}</div>
          ) : (
            <div className="scrollbar grid max-h-[60vh] grid-cols-1 gap-2 overflow-y-auto md:grid-cols-2">
              {boardStocks.map((s) => {
                const pct = s.change_pct ?? 0
                const color = marketSignTextClass(pct)
                return (
                  <div
                    key={s.symbol}
                    onClick={() => {
                      setBoardDialogOpen(false)
                      onOpenStock(s.symbol, s.market || 'CN', s.name, false)
                    }}
                    className="flex cursor-pointer items-center justify-between gap-3 rounded-xl bg-accent/20 p-3 text-left transition-colors hover:bg-accent/35"
                  >
                    <div className="min-w-0">
                      <div className="truncate text-[13px] font-medium text-foreground">{s.name}</div>
                      <div className="font-mono text-[11px] text-muted-foreground">{s.symbol}</div>
                    </div>
                    <div className="text-right">
                      <div className="font-mono text-[12px] text-foreground">{s.price != null ? s.price.toFixed(2) : '--'}</div>
                      <div className={`font-mono text-[11px] ${color}`}>{pct >= 0 ? '+' : ''}{pct.toFixed(2)}%</div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
