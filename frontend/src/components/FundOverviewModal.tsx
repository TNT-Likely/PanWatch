import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { TrendingUp, TrendingDown, RefreshCw, Share2, Copy, Download, ExternalLink, Bell } from 'lucide-react'
import { fetchAPI, insightApi, stocksApi } from '@panwatch/api'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Skeleton } from '@panwatch/base-ui/components/ui/skeleton'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { Switch } from '@panwatch/base-ui/components/ui/switch'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { getMarketBadge, getFundType } from '@panwatch/biz-ui'
import { SuggestionBadge, type SuggestionInfo } from '@panwatch/biz-ui/components/suggestion-badge'
import StockPriceAlertPanel from '@panwatch/biz-ui/components/stock-price-alert-panel'
import InteractiveFundChart from '@panwatch/biz-ui/components/InteractiveFundChart'

interface HoldingItem {
  code: string
  name: string
  weight: number | null
  weight_text?: string
  change_pct: number | null
}

interface PerfPoint {
  ts: number
  value: number
  return_pct: number | null
}

interface FundOverview {
  fund_code: string
  top_holdings: HoldingItem[]
  performance: {
    points: PerfPoint[]
    since_return_pct: number | null
  }
  updated_at: number
}

interface NewsItem {
  source: string
  source_label: string
  external_id?: string
  title: string
  content?: string
  publish_time: string
  symbols?: string[]
  importance?: number
  url: string
}

interface PortfolioPosition {
  symbol: string
  market: string
  quantity: number
  cost_price: number
  market_value_cny: number | null
  pnl: number | null
}

interface PortfolioSummaryResponse {
  accounts: Array<{
    positions: PortfolioPosition[]
  }>
}

interface HistoryRecord {
  id: number
  agent_name: string
  stock_symbol: string
  analysis_date: string
  title: string
  content: string
  suggestions?: Record<string, any> | null
  news?: Array<{ source?: string; title?: string; publish_time?: string; url?: string }> | null
  prompt_stats?: Record<string, any> | null
  created_at: string
  updated_at?: string
}

interface StockItem {
  id: number
  symbol: string
  name: string
  market: string
}

interface FundQuote {
  symbol: string
  market: string
  current_price: number | null  // 估值净值
  change_pct: number | null      // 估值涨幅
  prev_close: number | null      // 单位净值（昨日）
  gztime: string | null          // 估值时间
  jzrq: string | null            // 净值日期
}

type TimeRange = '1m' | '3m' | '6m' | '1y' | '3y' | 'all'
type FundTab = 'overview' | 'performance' | 'suggestions' | 'reports' | 'announcements' | 'news'
type ReportTab = 'fund_holding_analyst'

const AGENT_LABELS: Record<string, string> = {
  fund_holding_analyst: '分析',
}

function parseToMs(input?: string): number | null {
  if (!input) return null
  const d = new Date(input)
  if (!isNaN(d.getTime())) return d.getTime()
  return null
}

function normalizeSuggestionAction(action?: string, label?: string): string {
  const a = String(action || '').toLowerCase().trim()
  if (['buy', 'add', '买入', '加仓', '建仓'].some(k => a.includes(k) || (label || '').includes(k))) return 'buy'
  if (['sell', 'reduce', '卖出', '减仓', '清仓'].some(k => a.includes(k) || (label || '').includes(k))) return 'sell'
  if (['hold', '持有', '观望'].some(k => a.includes(k) || (label || '').includes(k))) return 'hold'
  return 'hold'
}

function pickSuggestionText(raw: unknown, key: string): string {
  if (typeof raw === 'string') return raw
  if (typeof raw === 'object' && raw !== null) return String((raw as any)[key] || '') || JSON.stringify(raw)
  return ''
}

const RANGE_TO_DAYS: Record<TimeRange, number | null> = {
  '1m': 30,
  '3m': 90,
  '6m': 180,
  '1y': 365,
  '3y': 365 * 3,
  all: null,
}

function formatTime(isoTime?: string): string {
  if (!isoTime) return ''
  const d = new Date(isoTime)
  if (isNaN(d.getTime())) return ''
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function formatClockTime(value?: string | number | Date | null): string {
  if (value == null || value === '') return '--:--:--'
  const d = value instanceof Date ? value : new Date(value)
  if (isNaN(d.getTime())) return '--:--:--'
  return d.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

function formatCompactNumber(value: number | null | undefined): string {
  if (value == null) return '--'
  const n = Number(value)
  if (!isFinite(n)) return '--'
  const abs = Math.abs(n)
  if (abs >= 1e8) return `${(n / 1e8).toFixed(2)}亿`
  if (abs >= 1e4) return `${(n / 1e4).toFixed(2)}万`
  return n.toFixed(2)
}

function copyText(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text)
  }
  const area = document.createElement('textarea')
  area.value = text
  area.style.position = 'fixed'
  area.style.opacity = '0'
  area.style.left = '-9999px'
  document.body.appendChild(area)
  area.select()
  document.execCommand('copy')
  document.body.removeChild(area)
  return Promise.resolve()
}

function buildPath(points: PerfPoint[], width: number, height: number, padding: number): string {
  if (!points.length) return ''
  const values = points.map(p => Number(p.return_pct ?? 0))
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1

  return points
    .map((p, idx) => {
      const x = padding + (idx / Math.max(points.length - 1, 1)) * (width - padding * 2)
      const y = height - padding - ((Number(p.return_pct ?? 0) - min) / span) * (height - padding * 2)
      return `${idx === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`
    })
    .join(' ')
}

function buildAreaPath(linePath: string, points: PerfPoint[], width: number, height: number, padding: number): string {
  if (!linePath || !points.length) return ''
  const lastX = padding + (width - padding * 2)
  const firstX = padding
  const baselineY = height - padding
  return `${linePath} L ${lastX.toFixed(2)} ${baselineY.toFixed(2)} L ${firstX.toFixed(2)} ${baselineY.toFixed(2)} Z`
}

export default function FundOverviewModal({
  open,
  onOpenChange,
  fundCode,
  fundName,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  fundCode: string
  fundName?: string
}) {
  const { toast } = useToast()
  const [loading, setLoading] = useState(false)
  const [tab, setTab] = useState<FundTab>('overview')
  const [range, setRange] = useState<TimeRange>('all')
  const [data, setData] = useState<FundOverview | null>(null)
  const [error, setError] = useState<string>('')
  const [newsHours, setNewsHours] = useState<string>('168')
  const [announcementHours, setAnnouncementHours] = useState<string>('168')
  const [newsLoading, setNewsLoading] = useState(false)
  const [news, setNews] = useState<NewsItem[]>([])
  const [announcements, setAnnouncements] = useState<NewsItem[]>([])
  const [suggestions, setSuggestions] = useState<SuggestionInfo[]>([])
  const [reports, setReports] = useState<HistoryRecord[]>([])
  const [reportTab, setReportTab] = useState<ReportTab>('fund_holding_analyst')
  const [includeExpiredSuggestions, setIncludeExpiredSuggestions] = useState(false)
  const [watchingStock, setWatchingStock] = useState<StockItem | null>(null)
  const [watchToggleLoading, setWatchToggleLoading] = useState(false)
  const [alerting, setAlerting] = useState(false)
  const stockCacheRef = useRef<Record<string, StockItem>>({})
  const [autoSuggesting] = useState(false)
  const [autoRefreshEnabled, setAutoRefreshEnabled] = useState(true)
  const [autoRefreshSec, setAutoRefreshSec] = useState<number>(60)
  const [autoRefreshProgress, setAutoRefreshProgress] = useState(0)
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null)
  const [holdingAgg, setHoldingAgg] = useState<{
    quantity: number
    cost: number
    unitCost: number
    marketValue: number
    pnl: number
    currentPrice: number | null
  } | null>(null)
  const [holdingLoaded, setHoldingLoaded] = useState(false)
  const [imageExporting, setImageExporting] = useState(false)
  const [sharePreviewOpen, setSharePreviewOpen] = useState(false)
  const [sharePreviewUrl, setSharePreviewUrl] = useState('')
  const [includeHoldingPnlRate, setIncludeHoldingPnlRate] = useState(true)
  const [includeHoldingPnlAmount, setIncludeHoldingPnlAmount] = useState(true)
  const [fundQuote, setFundQuote] = useState<FundQuote | null>(null)

  const miniChartWidth = 320
  const miniChartHeight = 140
  const miniChartPadding = 20

  const badge = getMarketBadge('FUND')
  const resolvedName = fundName || fundCode

  const load = useCallback(async () => {
    if (!fundCode) return
    setLoading(true)
    setError('')
    try {
      const resp = await fetchAPI<FundOverview>(`/stocks/funds/${encodeURIComponent(fundCode)}/overview`)
      setData(resp)
      setLastUpdatedAt(Date.now())
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载基金详情失败')
    } finally {
      setLoading(false)
    }
  }, [fundCode])

  const loadQuote = useCallback(async () => {
    if (!fundCode) return
    try {
      const resp = await fetchAPI<FundQuote>(`/quotes/${encodeURIComponent(fundCode)}?market=FUND`)
      setFundQuote(resp)
      setLastUpdatedAt(Date.now())
    } catch {
      setFundQuote(null)
    }
  }, [fundCode])

  const loadHolding = useCallback(async () => {
    if (!fundCode) return
    setHoldingLoaded(false)
    try {
      const resp = await fetchAPI<PortfolioSummaryResponse>('/portfolio/summary?include_quotes=true')
      let quantity = 0
      let cost = 0
      let marketValue = 0
      let pnl = 0
      let currentPrice: number | null = null
      for (const acc of resp?.accounts || []) {
        for (const p of acc.positions || []) {
          if (p.symbol !== fundCode || p.market !== 'FUND') continue
          quantity += Number(p.quantity || 0)
          cost += Number(p.cost_price || 0) * Number(p.quantity || 0)
          marketValue += Number(p.market_value_cny || 0)
          pnl += Number(p.pnl || 0)
        }
      }
      if (quantity > 0) {
        currentPrice = marketValue / quantity
        setHoldingAgg({ quantity, cost, unitCost: cost / quantity, marketValue, pnl, currentPrice })
      } else {
        setHoldingAgg(null)
      }
      setLastUpdatedAt(Date.now())
    } catch {
      setHoldingAgg(null)
    } finally {
      setHoldingLoaded(true)
    }
  }, [fundCode])

  useEffect(() => {
    if (!open || !fundCode) return
    setTab('overview')
    setRange('all')
    load()
    loadHolding()
    loadQuote()
  }, [open, fundCode, load, loadHolding, loadQuote])

  const loadNews = useCallback(async () => {
    if (!fundName && !fundCode) return
    setNewsLoading(true)
    try {
      const params = new URLSearchParams({ hours: newsHours, limit: '20' })
      params.set('names', fundName || fundCode)
      const rows = await fetchAPI<NewsItem[]>(`/news?${params.toString()}`)
      setNews(rows || [])
      setLastUpdatedAt(Date.now())
    } catch {
      setNews([])
    } finally {
      setNewsLoading(false)
    }
  }, [fundCode, fundName, newsHours])

  const loadAnnouncements = useCallback(async () => {
    if (!fundName && !fundCode) return
    try {
      const params = new URLSearchParams({ hours: announcementHours, limit: '50', source: 'eastmoney' })
      params.set('names', fundName || fundCode)
      const rows = await fetchAPI<NewsItem[]>(`/news?${params.toString()}`)
      setAnnouncements(rows || [])
      setLastUpdatedAt(Date.now())
    } catch {
      setAnnouncements([])
    }
  }, [fundCode, fundName, announcementHours])

  const loadSuggestions = useCallback(async () => {
    if (!fundCode) return
    try {
      const data = await insightApi.suggestions<any[]>(fundCode, {
        market: 'FUND',
        limit: 20,
        include_expired: includeExpiredSuggestions,
      })
      const list = (data || []).map(item => ({
        id: item.id,
        action: normalizeSuggestionAction(item.action, item.action_label),
        action_label: item.action_label || '',
        signal: pickSuggestionText(item.signal, 'signal'),
        reason: pickSuggestionText(item.reason, 'reason'),
        should_alert: !!item.should_alert,
        agent_name: item.agent_name,
        agent_label: item.agent_label,
        created_at: item.created_at,
        is_expired: item.is_expired,
        prompt_context: item.prompt_context,
        ai_response: item.ai_response,
        raw: item.raw || '',
        meta: item.meta,
      })) as SuggestionInfo[]
      setSuggestions(list)
      setLastUpdatedAt(Date.now())
    } catch {
      setSuggestions([])
    }
  }, [fundCode, includeExpiredSuggestions])

  const loadReports = useCallback(async () => {
    if (!fundCode) return
    try {
      const agents = ['fund_holding_analyst']
      const bySymbolResults = await Promise.all(
        agents.map(agent =>
          insightApi.history<HistoryRecord[]>({
            agent_name: agent,
            stock_symbol: fundCode,
            limit: 1,
          }).catch(() => [])
        )
      )
      let merged = bySymbolResults.flatMap(items => items || []).filter(Boolean)
      if (merged.length === 0 && fundName) {
        const globalResults = await Promise.all(
          agents.map(agent =>
            insightApi.history<HistoryRecord[]>({
              agent_name: agent,
              stock_symbol: '*',
              limit: 20,
            }).catch(() => [])
          )
        )
        merged = globalResults
          .map(items => {
            const rows = (items || []).filter(Boolean)
            const hit = rows.find((r) => {
              const text = `${r?.title || ''}\n${r?.content || ''}`
              if (text.includes(fundCode) || text.includes(fundName || '')) return true
              return false
            })
            return hit || null
          })
          .filter(Boolean) as HistoryRecord[]
      }
      merged = merged.sort((a, b) => {
        const am = parseToMs(a.updated_at || a.created_at || a.analysis_date) || 0
        const bm = parseToMs(b.updated_at || b.created_at || b.analysis_date) || 0
        return bm - am
      })
      setReports(merged)
      setLastUpdatedAt(Date.now())
    } catch {
      setReports([])
    }
  }, [fundCode, fundName])

  useEffect(() => {
    if (!open || tab !== 'news') return
    loadNews()
  }, [open, tab, loadNews])

  useEffect(() => {
    if (!open || tab !== 'announcements') return
    loadAnnouncements()
  }, [open, tab, loadAnnouncements])

  useEffect(() => {
    if (!open || !fundCode) return
    loadSuggestions().catch(() => setSuggestions([]))
  }, [open, fundCode, includeExpiredSuggestions, loadSuggestions])

  useEffect(() => {
    if (!open || !fundCode) return
    loadReports().catch(() => setReports([]))
  }, [open, fundCode, loadReports])

  useEffect(() => {
    if (!open || !fundCode) return
    let cancelled = false
    ;(async () => {
      try {
        const key = `FUND:${fundCode}`
        const stocks = await stocksApi.list()
        if (cancelled) return
        const found = (stocks || []).find(s => s.symbol === fundCode && s.market === 'FUND') || null
        if (found) {
          stockCacheRef.current[key] = found
        } else {
          delete stockCacheRef.current[key]
        }
        setWatchingStock(found)
      } catch {
        if (!cancelled) setWatchingStock(null)
      }
    })()
    return () => { cancelled = true }
  }, [open, fundCode])

  useEffect(() => {
    if (!open || !fundCode || !autoRefreshEnabled) {
      setAutoRefreshProgress(0)
      return
    }
    const sec = Number(autoRefreshSec) > 0 ? Number(autoRefreshSec) : 60
    const ms = Math.max(10, sec) * 1000
    const startTime = Date.now()
    const refreshTimer = setInterval(() => {
      load().catch(() => undefined)
      loadHolding().catch(() => undefined)
      loadQuote().catch(() => undefined)
      if (tab === 'suggestions') loadSuggestions().catch(() => undefined)
    }, ms)
    const tick = () => {
      const elapsed = Date.now() - startTime
      const cycleElapsed = elapsed % ms
      const progress = 1 - cycleElapsed / ms
      setAutoRefreshProgress(progress)
    }
    tick()
    const progressTimer = setInterval(tick, 100)
    return () => {
      clearInterval(refreshTimer)
      clearInterval(progressTimer)
      setAutoRefreshProgress(0)
    }
  }, [open, fundCode, autoRefreshEnabled, autoRefreshSec, load, loadHolding, loadQuote, loadSuggestions, tab])

  const allPoints = useMemo(() => data?.performance?.points || [], [data])

  const filteredPoints = useMemo(() => {
    const points = allPoints
    if (!points.length) return []
    const days = RANGE_TO_DAYS[range]
    if (!days) return points
    const endTs = points[points.length - 1].ts
    const startTs = endTs - days * 24 * 60 * 60 * 1000
    const selected = points.filter(p => p.ts >= startTs)
    return selected.length >= 2 ? selected : points
  }, [allPoints, range])

  const normalizedPoints = useMemo(() => {
    if (!filteredPoints.length) return []
    const base = Number(filteredPoints[0].value || 0)
    if (!base) return filteredPoints.map(p => ({ ...p, return_pct: 0 }))
    return filteredPoints.map(p => ({
      ...p,
      return_pct: ((Number(p.value) / base) - 1) * 100,
    }))
  }, [filteredPoints])

  const miniNormalizedPoints = useMemo(() => {
    if (!allPoints.length) return []
    const base = Number(allPoints[0].value || 0)
    if (!base) return allPoints.map(p => ({ ...p, return_pct: 0 }))
    return allPoints.map(p => ({
      ...p,
      return_pct: ((Number(p.value) / base) - 1) * 100,
    }))
  }, [allPoints])

  const miniChartPath = useMemo(() => buildPath(miniNormalizedPoints, miniChartWidth, miniChartHeight, miniChartPadding), [miniNormalizedPoints])
  const miniAreaPath = useMemo(() => buildAreaPath(miniChartPath, miniNormalizedPoints, miniChartWidth, miniChartHeight, miniChartPadding), [miniChartPath, miniNormalizedPoints])

  const rangeReturn = useMemo(() => {
    if (normalizedPoints.length < 2) return 0
    return Number(normalizedPoints[normalizedPoints.length - 1].return_pct || 0)
  }, [normalizedPoints])

  const sinceReturn = data?.performance?.since_return_pct ?? 0
  const positive = sinceReturn >= 0

  const latestNav = useMemo(() => {
    if (!allPoints.length) return null
    return Number(allPoints[allPoints.length - 1].value || 0)
  }, [allPoints])

  const hasHolding = !!holdingAgg

  const reportMap = useMemo(() => {
    const out: Record<string, HistoryRecord | null> = {
      premarket_outlook: null,
      daily_report: null,
      fund_holding_analyst: null,
    }
    for (const r of reports) {
      if (!out[r.agent_name]) out[r.agent_name] = r
    }
    return out
  }, [reports])
  const activeReport = reportMap[reportTab]

  const handleRefreshAll = async () => {
    await Promise.allSettled([
      load(),
      loadHolding(),
      loadSuggestions(),
      loadReports(),
      tab === 'news' ? loadNews() : Promise.resolve(),
      tab === 'announcements' ? loadAnnouncements() : Promise.resolve(),
    ])
  }

  const toggleWatch = useCallback(async () => {
    if (!fundCode) return
    if (watchingStock && hasHolding) {
      toast('该基金存在持仓，请先删除持仓后再取消关注', 'error')
      return
    }
    setWatchToggleLoading(true)
    try {
      if (watchingStock) {
        await stocksApi.remove(watchingStock.id)
        setWatchingStock(null)
        delete stockCacheRef.current[`FUND:${fundCode}`]
        toast('已取消关注', 'success')
      } else {
        const created = await stocksApi.create({ symbol: fundCode, name: resolvedName || fundCode, market: 'FUND' })
        setWatchingStock(created)
        stockCacheRef.current[`FUND:${fundCode}`] = created
        toast('已添加关注', 'success')
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : '操作失败', 'error')
    } finally {
      setWatchToggleLoading(false)
    }
  }, [hasHolding, fundCode, resolvedName, toast, watchingStock])

  const handleSetAlert = async () => {
    if (!fundCode || !watchingStock) {
      toast('请先关注该基金再设置提醒', 'error')
      return
    }
    setAlerting(true)
    try {
      await stocksApi.triggerAgent(watchingStock.id, 'intraday_monitor', {
        allow_unbound: true,
        symbol: fundCode,
        market: 'FUND',
        name: resolvedName || fundCode,
        bypass_throttle: true,
        bypass_market_hours: true,
      })
      toast('已触发建议生成，稍后刷新查看', 'success')
      const before = Date.now()
      const poll = setInterval(async () => {
        if (Date.now() - before > 120_000) { clearInterval(poll); setAlerting(false); return }
        await loadSuggestions()
      }, 5_000)
      await loadSuggestions()
      setTimeout(() => clearInterval(poll), 125_000)
      return
    } catch (e) {
      toast(e instanceof Error ? e.message : '设置提醒失败', 'error')
    } finally {
      setAlerting(false)
    }
  }

  const handleShare = async () => {
    const shareText = `${resolvedName}（${fundCode}）基金详情\n成立来涨跌 ${sinceReturn >= 0 ? '+' : ''}${sinceReturn.toFixed(2)}%\n区间涨跌 ${rangeReturn >= 0 ? '+' : ''}${rangeReturn.toFixed(2)}%`
    try {
      if (navigator.share) {
        await navigator.share({ title: `${resolvedName} 基金详情`, text: shareText })
        return
      }
      await copyText(shareText)
      toast('已复制分享内容', 'success')
    } catch {
      toast('分享失败', 'error')
    }
  }

  const handleCopySummary = async () => {
    try {
      const text = `${resolvedName}（${fundCode}）\n成立来涨跌: ${sinceReturn >= 0 ? '+' : ''}${sinceReturn.toFixed(2)}%\n最新净值: ${latestNav?.toFixed(4) || '--'}`
      await copyText(text)
      toast('已复制摘要', 'success')
    } catch {
      toast('复制失败', 'error')
    }
  }

  const holdingPnlRate = useMemo(() => {
    if (!holdingAgg || holdingAgg.cost <= 0) return null
    return (holdingAgg.pnl / holdingAgg.cost) * 100
  }, [holdingAgg])

  const shareImageWidth = 1200
  const shareImageHeight = 1680

  const toTwoCharActionLabel = (actionRaw: unknown, labelRaw: unknown, shouldAlert: boolean): string => {
    const action = String(actionRaw || '').toLowerCase().trim()
    const label = String(labelRaw || '').trim()

    if (action === 'dca' || label.includes('定投')) return '定投'
    if (action === 'add' || action === 'buy' || /加仓|买入|建仓/.test(label)) return '加仓'
    if (action === 'reduce' || /减仓/.test(label)) return '减仓'
    if (action === 'sell' || /清仓|卖出/.test(label)) return '清仓'
    if (action === 'watch' || action === 'hold' || /观望|持有/.test(label)) return '观望'
    if (action === 'avoid' || /回避/.test(label)) return '回避'
    if (action === 'alert' || /提示|提醒/.test(label)) return '提示'
    return shouldAlert ? '提示' : '观望'
  }

  const buildFundShareSvg = useCallback((opts: { includePnlRate: boolean; includePnlAmount: boolean }) => {
    const esc = (s: string) => String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&apos;')
    const trim = (s: string, n = 42) => {
      const x = String(s || '')
      return x.length > n ? `${x.slice(0, n - 1)}…` : x
    }

    const splitLines = (s: string, maxUnits: number, maxLines: number) => {
      const text = String(s || '').replace(/\s+/g, ' ').trim()
      if (!text) return [] as string[]

      const unit = (ch: string) => {
        if (/\s/.test(ch)) return 0.3
        if (/[a-zA-Z0-9.%+\-]/.test(ch)) return 0.56
        if (/[，。；：、！？,.!?:;]/.test(ch)) return 0.45
        return 1
      }

      const lines: string[] = []
      let i = 0
      while (i < text.length && lines.length < maxLines) {
        let width = 0
        let j = i
        let lastBreak = -1
        while (j < text.length) {
          const ch = text[j]
          if (ch === '\n') {
            break
          }
          width += unit(ch)
          if (/[，。；：、！？,.!?:;\s]/.test(ch)) lastBreak = j
          if (width > maxUnits) {
            if (lastBreak >= i) j = lastBreak + 1
            break
          }
          j += 1
        }

        if (j <= i) j = Math.min(i + 1, text.length)
        let line = text.slice(i, j).trim()
        if (!line && i < text.length) {
          line = text.slice(i, Math.min(i + 1, text.length))
          j = i + 1
        }
        lines.push(line)

        i = j
        while (i < text.length && /\s/.test(text[i])) i += 1
        if (i < text.length && text[i] === '\n') i += 1
      }

      if (i < text.length && lines.length > 0) {
        const last = lines.length - 1
        lines[last] = `${lines[last].slice(0, Math.max(1, lines[last].length - 1))}…`
      }
      return lines
    }

    const suggestion = suggestions[0] || null
    const action = String(suggestion?.action || '').toLowerCase()
    const actionLabel = toTwoCharActionLabel(
      suggestion?.action,
      suggestion?.action_label,
      !!suggestion?.should_alert,
    )
    const actionColor = action === 'avoid' || action === 'sell'
      ? '#8f6b2f'
      : action === 'reduce'
        ? '#9b7e42'
        : action === 'buy' || action === 'add'
          ? '#2f855a'
          : '#6b7280'
    const actionBg = action === 'avoid' || action === 'sell'
      ? '#f6ead1'
      : action === 'reduce'
        ? '#f6edd9'
        : action === 'buy' || action === 'add'
          ? '#e7f6ea'
          : '#eceff3'

    const topLeftIsHolding = opts.includePnlRate && holdingPnlRate != null
    const topRightIsHolding = opts.includePnlAmount && !!holdingAgg
    const bothHoldingTogglesOff = !opts.includePnlRate && !opts.includePnlAmount
    const anyHoldingToggleOn = opts.includePnlRate || opts.includePnlAmount
    const showTechCard = opts.includePnlRate || opts.includePnlAmount
    const gainRate = topLeftIsHolding ? holdingPnlRate : sinceReturn
    const gainAmount = topRightIsHolding ? (holdingAgg?.pnl ?? null) : (bothHoldingTogglesOff ? latestNav : sinceReturn)
    const gainRateColor = gainRate >= 0 ? '#2f855a' : '#b45309'
    const gainAmountColor = bothHoldingTogglesOff
      ? '#2f2a23'
      : (gainAmount != null ? (gainAmount >= 0 ? '#2f855a' : '#b45309') : '#6b7280')
    const leftTitle = topLeftIsHolding ? '当前收益率:' : '成立来涨跌:'
    const rightTitle = topRightIsHolding ? '累计收益金额:' : (bothHoldingTogglesOff ? '单位净值:' : '成立以来:')
    const rightValue = gainAmount == null
      ? '--'
      : bothHoldingTogglesOff
        ? gainAmount.toFixed(4)
        : `${gainAmount >= 0 ? '+' : ''}${gainAmount.toFixed(2)}${topRightIsHolding ? '' : '%'}`

    const signalLines = splitLines(
      suggestion?.signal || ((data?.top_holdings || []).slice(0, 4).map((h) => h.name).join('、') || '暂无明显信号，建议等待更清晰结构。'),
      10.4,
      4,
    )
    const reasonLines = splitLines(
      suggestion?.reason || `区间涨跌 ${rangeReturn >= 0 ? '+' : ''}${rangeReturn.toFixed(2)}%，最新净值 ${latestNav?.toFixed(4) || '--'}。`,
      10.4,
      4,
    )
    const riskSource = (
      (suggestion?.meta as Record<string, any> | undefined)?.risk
      || (suggestion?.meta as Record<string, any> | undefined)?.risk_tip
      || (suggestion?.meta as Record<string, any> | undefined)?.risk_warning
      || suggestion?.prompt_context
      || ''
    )
    const riskLines = splitLines(
      riskSource || (rangeReturn >= 0 ? '短线涨幅偏高，注意回撤与波动放大。' : '弱势阶段优先控制仓位，谨防继续走弱。'),
      10.4,
      4,
    )

    const sourceLabel = suggestion?.agent_label || '基金分析'
    const sourceTime = suggestion?.created_at
      ? formatClockTime(suggestion.created_at)
      : formatClockTime(fundQuote?.gztime || Date.now())

    const ts = new Date().toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
    const holdingMetrics: string[] = []
    if (holdingAgg && holdingAgg.quantity > 0) {
      if (opts.includePnlRate && holdingPnlRate != null) {
        holdingMetrics.push(`持仓收益率 ${holdingPnlRate >= 0 ? '+' : ''}${holdingPnlRate.toFixed(2)}%`)
      }
      if (opts.includePnlAmount) {
        holdingMetrics.push(`持仓收益额 ${holdingAgg.pnl >= 0 ? '+' : ''}${holdingAgg.pnl.toFixed(2)}`)
      }
    }
    const showBottomDetails = true
    const showHolding = false
    const holdingRefLines = showBottomDetails
      ? splitLines(
          `重仓参考: ${((data?.top_holdings || []).slice(0, 6).map((h) => h.name).join(' / ') || '--')}`,
          44,
          2,
        )
      : []
    const techLeftTitle = anyHoldingToggleOn ? '成立以来' : '区间涨跌'
    const techLeftValue = anyHoldingToggleOn ? sinceReturn : rangeReturn
    const techLeftColor = techLeftValue >= 0 ? '#2f855a' : '#b45309'
    const holdingsBaseY = showTechCard ? 1548 : 1454
    const metricsY = holdingsBaseY + holdingRefLines.length * 32 + 20
    const lastRefY = holdingRefLines.length > 0 ? (holdingsBaseY + (holdingRefLines.length - 1) * 32) : holdingsBaseY
    const desiredSourceY = showHolding ? (metricsY + 34) : (lastRefY + 30)
    const sourceY = Math.min(showTechCard ? 1578 : 1546, desiredSourceY)

    return `
<svg xmlns="http://www.w3.org/2000/svg" width="${shareImageWidth}" height="${shareImageHeight}" viewBox="0 0 ${shareImageWidth} ${shareImageHeight}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#f3efe7"/>
      <stop offset="100%" stop-color="#e9e3d9"/>
    </linearGradient>
    <linearGradient id="topPanel" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#f7faf7"/>
      <stop offset="100%" stop-color="#e9f6ed"/>
    </linearGradient>
    <linearGradient id="glass" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="0.94"/>
      <stop offset="100%" stop-color="#f7f4ee" stop-opacity="0.9"/>
    </linearGradient>
    <filter id="softShadow" x="-20%" y="-20%" width="140%" height="150%">
      <feDropShadow dx="0" dy="8" stdDeviation="10" flood-color="#5e5544" flood-opacity="0.16"/>
    </filter>
  </defs>

  <rect x="0" y="0" width="1200" height="1680" fill="url(#bg)"/>
  <rect x="64" y="58" width="1072" height="1560" rx="38" fill="url(#glass)" stroke="#d6ccbd" filter="url(#softShadow)"/>

  <text x="94" y="116" fill="#6e6252" font-size="36" font-weight="500" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(ts)}</text>
  <text x="1106" y="116" fill="#5f5649" font-size="56" text-anchor="end" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">PanWatch</text>

  <rect x="94" y="162" width="1012" height="264" rx="30" fill="url(#topPanel)" stroke="#cce4d0"/>
  <line x1="600" y1="198" x2="600" y2="390" stroke="#d7e7db"/>
  <text x="142" y="244" fill="#23201b" font-size="52" font-weight="600" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${leftTitle}</text>
  <text x="642" y="244" fill="#23201b" font-size="52" font-weight="600" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${rightTitle}</text>
  <text x="142" y="356" fill="${gainRateColor}" font-size="84" font-weight="800" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${gainRate >= 0 ? '+' : ''}${gainRate.toFixed(2)}%</text>
  <text x="642" y="356" fill="${gainAmountColor}" font-size="74" font-weight="800" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${rightValue}</text>

  <rect x="94" y="446" width="676" height="274" rx="30" fill="#fffdfa" stroke="#d9d0c2"/>
  <text x="130" y="520" fill="#1f1c17" font-size="62" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(resolvedName, 12))}</text>
  <text x="130" y="588" fill="#2a251f" font-size="42" font-weight="500" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">(${esc(fundCode)} · 基金)</text>
  <text x="130" y="660" fill="#2a251f" font-size="54" font-weight="500" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">估值 ${fundQuote?.current_price != null ? fundQuote.current_price.toFixed(4) : '--'} <tspan fill="${(fundQuote?.change_pct || 0) >= 0 ? '#2f855a' : '#b45309'}">(${(fundQuote?.change_pct || 0) >= 0 ? '+' : ''}${fundQuote?.change_pct != null ? fundQuote.change_pct.toFixed(2) : '--'}%)</tspan></text>

  <rect x="790" y="446" width="316" height="274" rx="30" fill="${actionBg}" stroke="#d8ccb3"/>
  <text x="832" y="530" fill="#5a4c37" font-size="54" font-weight="600" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">建议</text>
  <text x="850" y="640" fill="${actionColor}" font-size="86" font-weight="800" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(actionLabel)}</text>
  <text x="948" y="700" text-anchor="middle" fill="#6f6658" font-size="24" font-weight="500" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(sourceLabel)} · ${esc(sourceTime)}</text>

  <rect x="94" y="800" width="1012" height="560" rx="30" fill="#fffdf9" stroke="#d9d0c2"/>
  <line x1="430" y1="832" x2="430" y2="1324" stroke="#d8d0c4"/>
  <line x1="764" y1="832" x2="764" y2="1324" stroke="#d8d0c4"/>

  <text x="130" y="898" fill="#221f1a" font-size="64" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">信号</text>
  <text x="464" y="898" fill="#221f1a" font-size="64" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">理由</text>
  <text x="798" y="898" fill="#221f1a" font-size="64" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">风险</text>

  ${(signalLines.length ? signalLines : ['暂无']).map((line, idx) => `<text x="130" y="${986 + idx * 64}" fill="#2d2923" font-size="30" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(line)}</text>`).join('')}
  ${(reasonLines.length ? reasonLines : ['暂无']).map((line, idx) => `<text x="464" y="${986 + idx * 64}" fill="#2d2923" font-size="30" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(line)}</text>`).join('')}
  ${(riskLines.length ? riskLines : ['暂无']).map((line, idx) => `<text x="798" y="${986 + idx * 64}" fill="#2d2923" font-size="30" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(line)}</text>`).join('')}

  ${showTechCard ? `<rect x="94" y="1392" width="1012" height="116" rx="20" fill="#f3f7f4" stroke="#cfe0d4"/>
  <line x1="600" y1="1410" x2="600" y2="1488" stroke="#d7e5da"/>
  <text x="130" y="1436" fill="#5f5649" font-size="20" font-weight="600" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${techLeftTitle}</text>
  <text x="130" y="1478" fill="${techLeftColor}" font-size="32" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${techLeftValue >= 0 ? '+' : ''}${techLeftValue.toFixed(2)}%</text>
  <text x="640" y="1436" fill="#5f5649" font-size="20" font-weight="600" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">单位净值</text>
  <text x="640" y="1478" fill="#2f2a23" font-size="32" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${latestNav?.toFixed(4) || '--'}</text>` : ''}
  ${showBottomDetails ? (holdingRefLines.length ? holdingRefLines : ['重仓参考: --']).map((line, idx) => `<text x="110" y="${holdingsBaseY + idx * 32}" fill="#7b7163" font-size="20" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(line)}</text>`).join('') : ''}

  ${showHolding ? `<text x="110" y="${metricsY}" fill="#7b7163" font-size="20" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(holdingMetrics.join('  ·  '), 64))}</text>` : ''}
  ${showBottomDetails ? `<text x="110" y="${sourceY}" fill="#918573" font-size="18" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">数据来源：东方财富与 PanWatch · 仅供参考，不构成投资建议</text>` : ''}
</svg>`
  }, [data?.top_holdings, fundCode, fundQuote?.change_pct, fundQuote?.current_price, fundQuote?.gztime, holdingAgg, holdingPnlRate, latestNav, rangeReturn, resolvedName, sinceReturn, suggestions])

  const renderFundSharePreview = useCallback(async (opts?: { includePnlRate: boolean; includePnlAmount: boolean }) => {
    const svg = buildFundShareSvg(opts || {
      includePnlRate: includeHoldingPnlRate,
      includePnlAmount: includeHoldingPnlAmount,
    })

    const blob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    try {
      const img = await new Promise<HTMLImageElement>((resolve, reject) => {
        const el = new Image()
        el.onload = () => resolve(el)
        el.onerror = reject
        el.src = url
      })
      const canvas = document.createElement('canvas')
      canvas.width = shareImageWidth
      canvas.height = shareImageHeight
      const ctx = canvas.getContext('2d')
      if (!ctx) throw new Error('无法创建画布')
      ctx.drawImage(img, 0, 0)
      return canvas.toDataURL('image/png')
    } finally {
      URL.revokeObjectURL(url)
    }
  }, [buildFundShareSvg, includeHoldingPnlAmount, includeHoldingPnlRate, shareImageHeight, shareImageWidth])

  const handleOpenFundSharePreview = useCallback(async () => {
    setImageExporting(true)
    try {
      const png = await renderFundSharePreview()
      setSharePreviewUrl(png)
      setSharePreviewOpen(true)
    } catch {
      toast('图片生成失败，请稍后重试', 'error')
    } finally {
      setImageExporting(false)
    }
  }, [renderFundSharePreview, toast])

  const handleSaveFundShareImage = useCallback(() => {
    if (!sharePreviewUrl) return
    const a = document.createElement('a')
    a.href = sharePreviewUrl
    a.download = `PanWatch-FUND-${fundCode}-${Date.now()}.png`
    a.click()
    toast('分享图片已保存', 'success')
  }, [fundCode, sharePreviewUrl, toast])

  useEffect(() => {
    if (!sharePreviewOpen) return
    let cancelled = false
    setImageExporting(true)
    renderFundSharePreview({
      includePnlRate: includeHoldingPnlRate,
      includePnlAmount: includeHoldingPnlAmount,
    })
      .then((png) => {
        if (!cancelled) setSharePreviewUrl(png)
      })
      .catch(() => {
        if (!cancelled) toast('预览图更新失败', 'error')
      })
      .finally(() => {
        if (!cancelled) setImageExporting(false)
      })
    return () => { cancelled = true }
  }, [includeHoldingPnlAmount, includeHoldingPnlRate, renderFundSharePreview, sharePreviewOpen, toast])

  return (
    <>
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[92vw] max-w-6xl p-5 md:p-6 overflow-hidden">
        <DialogHeader className="mb-3">
          <div className="flex items-start justify-between gap-3 pr-10 md:pr-8">
            <div className="shrink-0">
              <DialogTitle className="flex items-center gap-2 flex-wrap">
                <span className={`text-[10px] px-2 py-0.5 rounded ${badge.style}`}>{badge.label}</span>
                <span className="break-all">{resolvedName}</span>
                <span className="font-mono text-[12px] text-muted-foreground">({fundCode})</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{getFundType(fundCode)}</span>
              </DialogTitle>
            </div>
            <div className="hidden md:flex items-center gap-2">
              <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => handleOpenFundSharePreview()} disabled={imageExporting}>
                <Download className={`w-3.5 h-3.5 ${imageExporting ? 'animate-pulse' : ''}`} />
                <span>{imageExporting ? '生成中' : '图片'}</span>
              </Button>
              <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={handleShare}>
                <Share2 className="w-3.5 h-3.5" />
                <span>分享</span>
              </Button>
              <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={handleCopySummary}>
                <Copy className="w-3.5 h-3.5" />
                <span>复制</span>
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className="h-8 px-2.5"
                onClick={toggleWatch}
                disabled={watchToggleLoading || (hasHolding && !!watchingStock)}
                title={hasHolding && watchingStock ? '持仓中的基金无法取消关注' : undefined}
              >
                {watchToggleLoading ? '处理中...' : (watchingStock ? (hasHolding ? '持仓中' : '取消关注') : '快速关注')}
              </Button>
              <StockPriceAlertPanel mode="inline" symbol={fundCode} market="FUND" stockName={resolvedName} />
              <Button variant="secondary" size="sm" className="h-8 px-2.5 hidden" onClick={handleSetAlert} disabled={alerting || !watchingStock}>
                <Bell className="w-3.5 h-3.5" />
                <span>{alerting ? '设置中...' : '一键设提醒'}</span>
              </Button>
            </div>
          </div>
          <div className="flex md:hidden items-center gap-2 mt-2 overflow-x-auto scrollbar-none pb-1 -mb-1">
            <Button variant="secondary" size="sm" className="h-8 px-2.5 shrink-0" onClick={() => handleOpenFundSharePreview()} disabled={imageExporting}>
              <Download className={`w-3.5 h-3.5 ${imageExporting ? 'animate-pulse' : ''}`} />
            </Button>
            <Button variant="secondary" size="sm" className="h-8 px-2.5 shrink-0" onClick={handleShare}>
              <Share2 className="w-3.5 h-3.5" />
            </Button>
            <Button variant="secondary" size="sm" className="h-8 px-2.5 shrink-0" onClick={handleCopySummary}>
              <Copy className="w-3.5 h-3.5" />
            </Button>
            <Button
              variant="secondary"
              size="sm"
              className="h-8 px-2.5 shrink-0"
              onClick={toggleWatch}
              disabled={watchToggleLoading || (hasHolding && !!watchingStock)}
            >
              {watchToggleLoading ? '处理中...' : (watchingStock ? (hasHolding ? '持仓中' : '取消关注') : '快速关注')}
            </Button>
            <StockPriceAlertPanel mode="inline" symbol={fundCode} market="FUND" stockName={resolvedName} />
            <Button variant="secondary" size="sm" className="h-8 px-2.5 shrink-0 hidden" onClick={handleSetAlert} disabled={alerting || !watchingStock}>
              <Bell className="w-3.5 h-3.5" />
              <span>{alerting ? '设置中...' : '一键设提醒'}</span>
            </Button>
          </div>
        </DialogHeader>

        <div className="flex items-center justify-between gap-2 flex-wrap mb-3">
          <div className="flex items-center gap-1 flex-wrap">
            {[
              { id: 'overview', label: '概览' },
              { id: 'suggestions', label: `建议 (${suggestions.length})` },
              { id: 'reports', label: `报告 (${reports.length})` },
              { id: 'performance', label: '业绩走势' },
              { id: 'announcements', label: `公告 (${announcements.length})` },
              { id: 'news', label: `新闻 (${news.length})` },
            ].map(item => (
              <button
                key={item.id}
                onClick={() => setTab(item.id as FundTab)}
                className={`text-[11px] px-2.5 py-1 rounded transition-colors ${
                  tab === item.id ? 'bg-primary text-primary-foreground' : 'bg-accent/50 text-muted-foreground hover:bg-accent'
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => handleRefreshAll()}
              disabled={loading}
              className="flex md:hidden w-8 h-8 rounded-xl items-center justify-center text-muted-foreground hover:text-foreground hover:bg-background/70 transition-all disabled:opacity-50 relative"
              title="刷新"
            >
              {autoRefreshEnabled && !loading && (
                <svg
                  className="absolute inset-0 -rotate-90 pointer-events-none"
                  width={28}
                  height={28}
                  style={{ margin: 'auto' }}
                >
                  <circle cx={14} cy={14} r={13} fill="none" stroke="currentColor" strokeOpacity={0.15} strokeWidth={2} />
                  <circle
                    cx={14} cy={14} r={13} fill="none" stroke="hsl(var(--primary))" strokeWidth={2} strokeLinecap="round"
                    strokeDasharray={2 * Math.PI * 13}
                    strokeDashoffset={2 * Math.PI * 13 * (1 - autoRefreshProgress)}
                    style={{ transition: 'stroke-dashoffset 0.1s linear' }}
                  />
                </svg>
              )}
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <Switch
              checked={autoRefreshEnabled}
              onCheckedChange={setAutoRefreshEnabled}
              aria-label="自动刷新"
              className="scale-90"
            />
            <span className="text-[11px] text-muted-foreground">自动刷新</span>
            {autoRefreshEnabled && (
              <Select value={String(autoRefreshSec)} onValueChange={(v) => setAutoRefreshSec(Number(v))}>
                <SelectTrigger className="h-6 w-14 text-[10px] px-1.5">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="10">10秒</SelectItem>
                  <SelectItem value="30">30秒</SelectItem>
                  <SelectItem value="60">60秒</SelectItem>
                  <SelectItem value="120">120秒</SelectItem>
                </SelectContent>
              </Select>
            )}
            <span className="hidden md:inline-flex h-7 items-center rounded-full border border-border/60 bg-accent/20 px-3 text-[11px] text-muted-foreground">
              更新 {formatClockTime(lastUpdatedAt)}
            </span>
            <button
              onClick={() => handleRefreshAll()}
              disabled={loading}
              className="hidden md:flex w-8 h-8 rounded-xl items-center justify-center text-muted-foreground hover:text-foreground hover:bg-background/70 transition-all disabled:opacity-50 relative"
              title="刷新"
            >
              {autoRefreshEnabled && !loading && (
                <svg
                  className="absolute inset-0 -rotate-90 pointer-events-none"
                  width={28}
                  height={28}
                  style={{ margin: 'auto' }}
                >
                  <circle cx={14} cy={14} r={13} fill="none" stroke="currentColor" strokeOpacity={0.15} strokeWidth={2} />
                  <circle
                    cx={14} cy={14} r={13} fill="none" stroke="hsl(var(--primary))" strokeWidth={2} strokeLinecap="round"
                    strokeDasharray={2 * Math.PI * 13}
                    strokeDashoffset={2 * Math.PI * 13 * (1 - autoRefreshProgress)}
                    style={{ transition: 'stroke-dashoffset 0.1s linear' }}
                  />
                </svg>
              )}
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        <div className="md:hidden mb-3">
          <span className="inline-flex h-7 items-center rounded-full border border-border/60 bg-accent/20 px-3 text-[11px] text-muted-foreground">
            更新 {formatClockTime(lastUpdatedAt)}
          </span>
        </div>

        <div className="max-h-[68vh] overflow-y-auto overflow-x-hidden pr-1 scrollbar">
          {loading && !data ? (
            <div className="space-y-3 py-2">
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-64 w-full" />
              <Skeleton className="h-36 w-full" />
            </div>
          ) : error ? (
            <div className="rounded-lg border border-destructive/30 bg-destructive/5 text-destructive text-sm p-3">{error}</div>
          ) : data && tab === 'overview' ? (
            <div className="space-y-3">
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 items-stretch">
                <div className="card p-4 h-full">
                  <div className="mt-1 flex items-end justify-between gap-3">
                    <div className={`text-[34px] leading-none font-bold font-mono ${positive ? 'text-rose-500' : 'text-emerald-500'}`}>
                      {latestNav?.toFixed(4) || '--'}
                    </div>
                    <div className={`text-[16px] font-mono ${positive ? 'text-rose-500' : 'text-emerald-500'}`}>
                      成立来 {sinceReturn >= 0 ? '+' : ''}{sinceReturn.toFixed(2)}%
                    </div>
                  </div>
                  <div className="mt-3 grid grid-cols-3 gap-2 text-[12px]">
                    <div className="rounded bg-accent/15 px-2 py-1.5">
                      <div className="text-[10px] text-muted-foreground">基金代码</div>
                      <div className="font-mono">{fundCode}</div>
                    </div>
                    <div className="rounded bg-accent/15 px-2 py-1.5">
                      <div className="text-[10px] text-muted-foreground">单位净值</div>
                      <div className="font-mono">{fundQuote?.prev_close?.toFixed(4) || latestNav?.toFixed(4) || '--'}</div>
                    </div>
                    <div className="rounded bg-accent/15 px-2 py-1.5">
                      <div className="text-[10px] text-muted-foreground">净值日期</div>
                      <div className="font-mono text-[11px]">{fundQuote?.jzrq || (data.updated_at ? new Date(data.updated_at).toLocaleDateString('zh-CN') : '--')}</div>
                    </div>
                  </div>

                  {fundQuote?.current_price != null && (
                    <div className="mt-2 grid grid-cols-3 gap-2 text-[12px]">
                      <div className="rounded bg-sky-500/10 px-2 py-1.5">
                        <div className="text-[10px] text-muted-foreground">估值净值</div>
                        <div className="font-mono text-sky-600 dark:text-sky-400">{fundQuote.current_price.toFixed(4)}</div>
                      </div>
                      <div className="rounded bg-sky-500/10 px-2 py-1.5">
                        <div className="text-[10px] text-muted-foreground">估值涨幅</div>
                        <div className={`font-mono ${(fundQuote.change_pct ?? 0) >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                          {fundQuote.change_pct != null ? `${fundQuote.change_pct >= 0 ? '+' : ''}${fundQuote.change_pct.toFixed(2)}%` : '--'}
                        </div>
                      </div>
                      <div className="rounded bg-sky-500/10 px-2 py-1.5">
                        <div className="text-[10px] text-muted-foreground">估值时间</div>
                        <div className="font-mono text-[11px]">{fundQuote.gztime || '--'}</div>
                      </div>
                    </div>
                  )}

                  <div className="mt-3 border-t border-border/50 pt-3">
                    <div className="text-[11px] text-muted-foreground mb-2">持仓信息</div>
                    {holdingAgg ? (
                      <div className="grid grid-cols-2 gap-2 text-[12px]">
                        <div className="rounded bg-emerald-500/10 px-2 py-1.5">
                          <div className="text-[10px] text-muted-foreground">持仓份额</div>
                          <div className="font-mono">{holdingAgg.quantity.toFixed(2)}</div>
                        </div>
                        <div className="rounded bg-emerald-500/10 px-2 py-1.5">
                          <div className="text-[10px] text-muted-foreground">持仓成本(单价)</div>
                          <div
                            className={`font-mono ${
                              latestNav != null
                                ? latestNav > holdingAgg.unitCost
                                  ? 'text-rose-500'
                                  : latestNav < holdingAgg.unitCost
                                    ? 'text-emerald-500'
                                    : 'text-foreground'
                                : 'text-foreground'
                            }`}
                          >
                            {holdingAgg.unitCost.toFixed(4)}
                          </div>
                        </div>
                        <div className="rounded bg-emerald-500/10 px-2 py-1.5">
                          <div className="text-[10px] text-muted-foreground">持仓市值</div>
                          <div className="font-mono">{formatCompactNumber(holdingAgg.marketValue)}</div>
                        </div>
                        <div className="rounded bg-emerald-500/10 px-2 py-1.5">
                          <div className="text-[10px] text-muted-foreground">总盈亏</div>
                          <div className={`font-mono ${holdingAgg.pnl >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                            {holdingAgg.pnl >= 0 ? '+' : ''}{formatCompactNumber(holdingAgg.pnl)}
                          </div>
                        </div>
                      </div>
                    ) : (
                      <div className="text-[11px] text-muted-foreground">{holdingLoaded ? '未在持仓中' : '加载中...'}</div>
                    )}
                  </div>
                </div>

                <div className="card p-4 h-full">
                  <div className="text-[12px] text-muted-foreground mb-2">自成立以来业绩走势</div>
                  {miniNormalizedPoints.length >= 2 ? (
                    <svg
                      viewBox={`0 0 ${miniChartWidth} ${miniChartHeight}`}
                      className="w-full h-32 cursor-pointer"
                      onClick={() => setTab('performance')}
                    >
                      <title>点击查看详细走势</title>
                      <defs>
                        <linearGradient id="miniArea" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor={positive ? '#fb7185' : '#34d399'} stopOpacity="0.3" />
                          <stop offset="100%" stopColor={positive ? '#fb7185' : '#34d399'} stopOpacity="0.02" />
                        </linearGradient>
                      </defs>
                      <path d={miniAreaPath} fill="url(#miniArea)" />
                      <path d={miniChartPath} fill="none" stroke={positive ? '#fb7185' : '#34d399'} strokeWidth="2.5" strokeLinecap="round" />
                    </svg>
                  ) : (
                    <div className="h-32 text-[11px] text-muted-foreground flex items-center justify-center">暂无走势数据</div>
                  )}

                  <div className="mt-3 rounded bg-accent/10 p-2.5">
                    <div className="text-[11px] text-muted-foreground mb-2">十大持仓股票涨跌</div>
                    <div className="grid grid-cols-2 gap-1.5">
                      {(data.top_holdings || []).slice(0, 6).map((item) => {
                        const rise = (item.change_pct || 0) >= 0
                        return (
                          <div key={`${item.code}-${item.name}`} className="flex items-center justify-between gap-1 text-[11px] px-1.5 py-1 rounded bg-accent/20">
                            <span className="truncate text-foreground">{item.name}</span>
                            <div className="flex items-center gap-1 shrink-0">
                              <span className={`font-mono ${rise ? 'text-rose-500' : 'text-emerald-500'}`}>
                                {item.change_pct != null ? `${item.change_pct >= 0 ? '+' : ''}${item.change_pct.toFixed(2)}%` : '--'}
                              </span>
                              <span className="text-sky-400 text-[10px]">{item.weight_text || `${Number(item.weight || 0).toFixed(1)}%`}</span>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                    {(data.top_holdings || []).length > 6 && (
                      <div className="text-[10px] text-muted-foreground mt-1.5 text-center">
                        查看全部 {data.top_holdings.length} 只持仓股票 ↓
                      </div>
                    )}
                  </div>
                </div>
              </div>

              <div className="card p-4">
                <div className="text-[12px] text-muted-foreground mb-3">重仓前10</div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                  {(data.top_holdings || []).map((item) => {
                    const rise = (item.change_pct || 0) >= 0
                    return (
                      <div key={`${item.code}-${item.name}`} className="rounded-lg border border-border bg-card p-2.5 flex items-center justify-between gap-2">
                        <div className="min-w-0">
                          <div className="text-sm font-medium truncate text-foreground">{item.name}</div>
                          <div className="text-xs text-muted-foreground font-mono">{item.code}</div>
                        </div>
                        <div className="text-right flex items-center gap-2">
                          <div className={`inline-flex items-center gap-1 px-2 py-1 rounded-full border text-xs ${rise ? 'text-rose-500 border-rose-500/30 bg-rose-500/10' : 'text-emerald-500 border-emerald-500/30 bg-emerald-500/10'}`}>
                            {rise ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                            {item.change_pct != null ? `${item.change_pct >= 0 ? '+' : ''}${item.change_pct.toFixed(2)}%` : '--'}
                          </div>
                          <div className="text-lg font-semibold text-sky-400">{item.weight_text || `${Number(item.weight || 0).toFixed(2)}%`}</div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>
          ) : tab === 'performance' ? (
            <InteractiveFundChart
              points={data?.performance?.points || []}
              loading={loading}
              onRefresh={load}
              initialRange="all"
            />
          ) : tab === 'news' ? (
            <div className="space-y-3">
              <div className="flex items-center justify-end">
                <Select value={newsHours} onValueChange={setNewsHours}>
                  <SelectTrigger className="h-8 w-[110px] text-[12px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="24">近24小时</SelectItem>
                    <SelectItem value="48">近48小时</SelectItem>
                    <SelectItem value="168">近7天</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {newsLoading ? (
                <div className="space-y-2">
                  <Skeleton className="h-16 w-full" />
                  <Skeleton className="h-16 w-full" />
                  <Skeleton className="h-16 w-full" />
                </div>
              ) : news.length === 0 ? (
                <div className="card p-6 text-[12px] text-muted-foreground text-center">暂无相关新闻</div>
              ) : (
                news.map((item, idx) => (
                  <a
                    key={`${item.external_id || item.publish_time}-${idx}`}
                    href={item.url}
                    target="_blank"
                    rel="noreferrer"
                    className="card block p-4 hover:bg-accent/20 transition-colors"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-[13px] font-medium text-foreground line-clamp-2">{item.title}</div>
                      <ExternalLink className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                    </div>
                    <div className="mt-2 text-[11px] text-muted-foreground">{item.source_label || item.source} · {formatTime(item.publish_time)}</div>
                  </a>
                ))
              )}
            </div>
          ) : tab === 'suggestions' ? (
            <div className="space-y-3">
              <div className="card p-3 flex items-center justify-between gap-3">
                <div className="text-[12px] text-muted-foreground">显示过期建议</div>
                <div className="flex items-center gap-2">
                  <span className="text-[11px] text-muted-foreground">{includeExpiredSuggestions ? '包含过期' : '仅有效'}</span>
                  <Switch
                    checked={includeExpiredSuggestions}
                    onCheckedChange={setIncludeExpiredSuggestions}
                    aria-label="显示过期建议"
                  />
                </div>
              </div>
              {suggestions.length === 0 ? (
                <div className="card p-6 text-[12px] text-muted-foreground text-center">
                  {autoSuggesting ? '正在自动生成 AI 建议（通常 5-15 秒）...' : '暂无建议'}
                </div>
              ) : (
                <div className="max-h-[56vh] overflow-y-auto pr-1 scrollbar space-y-3">
                  {suggestions.map((item, idx) => (
                    <div key={`${item.created_at || 's'}-${idx}`} className="card p-4">
                      <SuggestionBadge suggestion={item} stockName={resolvedName} stockSymbol={fundCode} hasPosition={hasHolding} />
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : tab === 'reports' ? (
            <div className="space-y-3">
              <div className="card p-3">
                <div className="flex items-center gap-1">
                  {([
                    { key: 'fund_holding_analyst', label: '分析' },
                  ] as const).map(item => (
                    <button
                      key={item.key}
                      onClick={() => setReportTab(item.key)}
                      className={`text-[11px] px-2.5 py-1 rounded ${
                        reportTab === item.key ? 'bg-primary text-primary-foreground' : 'bg-accent/60 text-muted-foreground hover:bg-accent'
                      }`}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>
              {!activeReport ? (
                <div className="card p-6 text-[12px] text-muted-foreground text-center">暂无报告</div>
              ) : (
                <div className="card p-4 space-y-3">
                  <div className="text-[11px] text-muted-foreground">
                    {AGENT_LABELS[activeReport.agent_name] || activeReport.agent_name} · {activeReport.analysis_date}
                  </div>
                  <div className="text-[15px] font-medium">{activeReport.title || '报告摘要'}</div>
                  {activeReport.suggestions && (activeReport.suggestions as any)?.[fundCode]?.action_label && (
                    <div className="text-[11px] inline-flex px-2 py-0.5 rounded bg-primary/10 text-primary">
                      {(activeReport.suggestions as any)[fundCode].action_label}
                    </div>
                  )}
                  <div className="rounded-lg bg-accent/10 p-3">
                    <div className="prose prose-sm dark:prose-invert max-w-none text-foreground/90 break-words">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{activeReport.content || '暂无报告内容'}</ReactMarkdown>
                    </div>
                  </div>
                </div>
              )}
            </div>
          ) : tab === 'announcements' ? (
            <div className="space-y-3">
              <div className="flex items-center justify-end">
                <Select value={announcementHours} onValueChange={setAnnouncementHours}>
                  <SelectTrigger className="h-8 w-[110px] text-[12px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="168">近7天</SelectItem>
                    <SelectItem value="336">近14天</SelectItem>
                    <SelectItem value="720">近30天</SelectItem>
                    <SelectItem value="2160">近90天</SelectItem>
                    <SelectItem value="24">近24小时</SelectItem>
                    <SelectItem value="48">近48小时</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {announcements.length === 0 ? (
                <div className="card p-6 text-[12px] text-muted-foreground text-center">暂无公告</div>
              ) : (
                announcements.map((item, idx) => (
                  <a
                    key={`${item.publish_time || 'a'}-${idx}`}
                    href={item.url}
                    target="_blank"
                    rel="noreferrer"
                    className="card block p-4 hover:bg-accent/20 transition-colors"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-[13px] font-medium text-foreground line-clamp-2">{item.title}</div>
                      <ExternalLink className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                    </div>
                    <div className="mt-2 text-[11px] text-muted-foreground">{item.source_label || item.source} · {formatTime(item.publish_time)}</div>
                  </a>
                ))
              )}
            </div>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>

    <Dialog open={sharePreviewOpen} onOpenChange={setSharePreviewOpen}>
      <DialogContent className="w-[94vw] max-w-5xl h-[94vh] p-4 md:p-5 overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="text-base">分享图片预览</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 flex-1 min-h-0 flex flex-col">
          {!!holdingAgg && (
            <div className="card p-3 flex items-center gap-4 flex-wrap">
              <div className="text-[12px] text-muted-foreground">持仓指标</div>
              <div className="flex items-center gap-2">
                <Switch checked={includeHoldingPnlRate} onCheckedChange={setIncludeHoldingPnlRate} />
                <span className="text-[12px]">收益率</span>
              </div>
              <div className="flex items-center gap-2">
                <Switch checked={includeHoldingPnlAmount} onCheckedChange={setIncludeHoldingPnlAmount} />
                <span className="text-[12px]">收益金额</span>
              </div>
            </div>
          )}

          <div className="rounded-xl border border-border/60 bg-muted/20 p-2 flex-1 min-h-0 overflow-hidden h-[52vh] md:h-[calc(94vh-220px)]">
            {sharePreviewUrl ? (
              <div className="w-full h-full flex items-center justify-center">
                <img src={sharePreviewUrl} alt="基金分享图预览" className="max-w-full max-h-full w-auto h-full rounded-lg object-contain" />
              </div>
            ) : (
              <div className="h-[220px] flex items-center justify-center text-[12px] text-muted-foreground">预览图生成中...</div>
            )}
          </div>

          <div className="flex items-center justify-end gap-2">
            <Button variant="secondary" onClick={() => setSharePreviewOpen(false)}>取消</Button>
            <Button onClick={handleSaveFundShareImage} disabled={!sharePreviewUrl || imageExporting}>
              {imageExporting ? '生成中...' : '保存图片'}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
    </>
  )
}
