import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import { RefreshCw, AlertTriangle, Sparkles, Activity, ShieldAlert, Newspaper, Share2 } from 'lucide-react'
import {
  dashboardApi,
  portfolioApi,
  recommendationsApi,
  homeApi,
  type DashboardMarketIndex,
  type DashboardMonitorStock,
  type DashboardOverviewResponse,
  type PortfolioDiagnostics,
  type PortfolioBenchmark,
  type StrategySignalItem,
  type AlertHitToday,
  type PortfolioTodo,
  type CurateCandidate,
  type CuratedItem,
  type AttributionItem,
  type PortfolioAiReview,
  type DashboardBrief,
} from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Onboarding } from '@panwatch/biz-ui/components/onboarding'
import StockInsightModal from '@panwatch/biz-ui/components/stock-insight-modal'
import DiscoveryPanel from '@/components/DiscoveryPanel'
import BenchmarkShareCard from '@/components/BenchmarkShareCard'
import DiagnosticsShareCard from '@/components/DiagnosticsShareCard'
import DigestShareCard from '@/components/DigestShareCard'

function pct(v?: number | null, digits = 2): string {
  if (v == null || !isFinite(v)) return '--'
  return `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`
}
function moveColor(v?: number | null): string {
  if (v == null) return 'text-muted-foreground'
  return v > 0 ? 'text-rose-500' : v < 0 ? 'text-emerald-500' : 'text-muted-foreground'
}
const ALERT_LABEL: Record<string, string> = {
  surge: '快速拉升',
  plunge: '快速跳水',
  high_volume: '放量异动',
  breakout: '突破',
  breakdown: '破位',
  limit_up: '涨停',
  limit_down: '跌停',
}

const FEED_BADGE: Record<string, { label: string; cls: string }> = {
  alert: { label: '提醒命中', cls: 'bg-rose-500/15 text-rose-500' },
  holding: { label: '持仓', cls: 'bg-emerald-500/15 text-emerald-500' },
  watch: { label: '自选', cls: 'bg-accent text-muted-foreground' },
  risk: { label: '风险', cls: 'bg-amber-500/15 text-amber-600' },
  opportunity: { label: '机会', cls: 'bg-primary/10 text-primary' },
}

export default function DashboardPage() {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(true)
  const [indices, setIndices] = useState<DashboardMarketIndex[]>([])
  const [scan, setScan] = useState<DashboardMonitorStock[]>([])
  const [overview, setOverview] = useState<DashboardOverviewResponse | null>(null)
  const [diag, setDiag] = useState<PortfolioDiagnostics | null>(null)
  const [bench, setBench] = useState<PortfolioBenchmark | null>(null)
  const [oppFallback, setOppFallback] = useState<StrategySignalItem[]>([])
  const [alertHits, setAlertHits] = useState<AlertHitToday[]>([])
  const [todos, setTodos] = useState<PortfolioTodo[]>([])
  const [curated, setCurated] = useState<CuratedItem[]>([])
  const [attribution, setAttribution] = useState<AttributionItem[]>([])
  const [aiReview, setAiReview] = useState<PortfolioAiReview | null>(null)
  const [aiReviewLoading, setAiReviewLoading] = useState(false)
  const [brief, setBrief] = useState<DashboardBrief | null>(null)
  const [briefOpen, setBriefOpen] = useState(false)
  // 分享卡开关:成绩单(基准)/ 组合体检 / 每日 digest
  const [shareBench, setShareBench] = useState(false)
  const [shareDiag, setShareDiag] = useState(false)
  const [shareDigest, setShareDigest] = useState(false)
  const [showOnboarding, setShowOnboarding] = useState(false)
  const [modal, setModal] = useState<{ open: boolean; symbol: string; market: string; name: string; hasPosition: boolean }>({
    open: false,
    symbol: '',
    market: 'CN',
    name: '',
    hasPosition: false,
  })

  const load = useCallback(async () => {
    setLoading(true)
    // 快车道:DB/轻量查询,先让首屏(要紧事/指数/体检分布)尽快出来
    const [idx, sc, ov, dg, ht, td] = await Promise.allSettled([
      dashboardApi.indices(),
      dashboardApi.intradayScan(),
      dashboardApi.overview({ market: 'ALL', action_limit: 6, risk_limit: 6 }),
      portfolioApi.diagnostics(),
      homeApi.alertHitsToday(),
      homeApi.todos(),
    ])
    if (idx.status === 'fulfilled') setIndices(idx.value)
    if (sc.status === 'fulfilled') setScan(sc.value.stocks || [])
    if (ov.status === 'fulfilled') setOverview(ov.value)
    if (dg.status === 'fulfilled') setDiag(dg.value)
    if (ht.status === 'fulfilled') setAlertHits(ht.value)
    if (td.status === 'fulfilled') setTodos(td.value.todos || [])
    setLoading(false) // 首屏不再等基准/归因(要拉全持仓 K 线)

    // 机会兜底:overview 无机会时再取(不挡首屏)
    if (ov.status !== 'fulfilled' || !ov.value.action_center?.opportunities?.length) {
      recommendationsApi
        .listStrategySignals({ status: 'active', limit: 5 })
        .then((r) => setOppFallback(r.items || []))
        .catch(() => {})
    }

    // 慢车道:基准/归因需拉全持仓 K 线(40s 级),独立加载,就绪后回填超额/归因
    Promise.allSettled([portfolioApi.benchmark({ days: 60 }), portfolioApi.attribution(60)]).then(([bn, at]) => {
      if (bn.status === 'fulfilled') setBench(bn.value)
      if (at.status === 'fulfilled') setAttribution(at.value.items || [])
    })

    // 盘前/盘后简报:独立加载,取较新一条
    Promise.allSettled([dashboardApi.brief('premarket'), dashboardApi.brief('eod')]).then((res) => {
      const briefs = res
        .filter((b): b is PromiseFulfilledResult<DashboardBrief> => b.status === 'fulfilled' && !b.value.empty)
        .map((b) => b.value)
      briefs.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))
      setBrief(briefs[0] || null)
    })
  }, [])

  useEffect(() => {
    load()
    if (!localStorage.getItem('panwatch_onboarding_completed')) setShowOnboarding(true)
  }, [load])

  const handleOnboardingComplete = () => {
    localStorage.setItem('panwatch_onboarding_completed', 'true')
    setShowOnboarding(false)
  }

  const openStock = (symbol: string, market: string, name = '', hasPosition = false) =>
    setModal({ open: true, symbol, market: market || 'CN', name, hasPosition })

  const runAiReview = async () => {
    setAiReviewLoading(true)
    try {
      setAiReview(await portfolioApi.aiReview())
    } catch (e) {
      setAiReview({ content: e instanceof Error ? `AI 体检失败: ${e.message}` : 'AI 体检失败' })
    } finally {
      setAiReviewLoading(false)
    }
  }

  // 今日要紧事:持仓异动 + 触发的盯盘信号(有 AI 建议/告警优先)
  const urgent = useMemo(() => {
    const items = (scan || []).filter((s) => s.has_position || s.alert_type || s.suggestion?.should_alert)
    const weight = (s: DashboardMonitorStock) =>
      (s.suggestion?.should_alert ? 1000 : 0) + (s.has_position ? 500 : 0) + Math.abs(s.change_pct || 0)
    return items.sort((a, b) => weight(b) - weight(a)).slice(0, 8)
  }, [scan])

  const opportunities = useMemo(() => {
    const list = overview?.action_center?.opportunities?.length ? overview.action_center.opportunities : oppFallback
    return list.slice(0, 5)
  }, [overview, oppFallback])

  // 今日必读候选(多源)→ 交 AI 策展(失败兜底原序)
  const candidates = useMemo<CurateCandidate[]>(() => {
    const out: CurateCandidate[] = []
    for (const h of alertHits) {
      out.push({ type: 'alert', symbol: h.symbol, name: h.name || h.symbol, market: h.market, signal: `触发提醒 ${h.rule_name}` })
    }
    for (const s of urgent) {
      out.push({
        type: s.has_position ? 'holding' : 'watch',
        symbol: s.symbol,
        name: s.name,
        market: s.market,
        change_pct: s.change_pct,
        signal: s.suggestion?.signal || (s.alert_type ? ALERT_LABEL[s.alert_type] || s.alert_type : ''),
      })
    }
    for (const a of diag?.alerts || []) out.push({ type: 'risk', name: '组合风险', market: '', signal: a })
    for (const o of opportunities.slice(0, 3)) {
      out.push({ type: 'opportunity', symbol: o.stock_symbol, name: o.stock_name || o.stock_symbol, market: o.stock_market, signal: o.signal || o.reason || o.action_label || '' })
    }
    return out
  }, [alertHits, urgent, diag, opportunities])

  const candKey = useMemo(
    () => candidates.map((c) => `${c.type}:${c.symbol}:${c.change_pct ?? ''}`).join('|'),
    [candidates],
  )

  useEffect(() => {
    if (candidates.length === 0) {
      setCurated([])
      return
    }
    let alive = true
    dashboardApi
      .curate(candidates)
      .then((r) => alive && setCurated(r.items || []))
      .catch(() => alive && setCurated([]))
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candKey])

  const feed = useMemo(() => {
    const rows = curated.length
      ? curated.map((ci) => (candidates[ci.index] ? { ...candidates[ci.index], why: ci.why } : null))
      : candidates.map((c) => ({ ...c, why: c.signal }))
    return rows.filter((x): x is CurateCandidate & { why: string } => !!x)
  }, [curated, candidates])

  const today = useMemo(() => {
    const d = new Date()
    const mm = String(d.getMonth() + 1).padStart(2, '0')
    const dd = String(d.getDate()).padStart(2, '0')
    return `${d.getFullYear()}-${mm}-${dd}`
  }, [])
  const hasHoldings = (diag?.position_count ?? 0) > 0
  const benchReady = bench && !bench.empty && bench.excess_return != null
  const hasWatchlist = (overview?.kpis?.watchlist_count ?? 0) > 0
  const portfolioPnlPct =
    diag && diag.total_market_value - diag.total_unrealized_pnl > 0
      ? (diag.total_unrealized_pnl / (diag.total_market_value - diag.total_unrealized_pnl)) * 100
      : null

  return (
    <div className="page-container pb-10">
      {/* 顶部:标题 + 大盘指数细条(降级,不再占主位) */}
      <div className="mb-3 flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div className="flex items-center gap-2">
          <h1 className="text-[20px] font-bold tracking-tight text-foreground md:text-[22px]">今日该看什么</h1>
          <Button onClick={load} disabled={loading} size="sm" variant="ghost" className="h-7 px-2">
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
          {hasHoldings && portfolioPnlPct != null && (
            <span className="flex items-center gap-1">
              <span className="text-muted-foreground">组合浮盈</span>
              <span className={`font-mono ${moveColor(portfolioPnlPct)}`}>{pct(portfolioPnlPct)}</span>
            </span>
          )}
          {benchReady && (
            <span className="flex items-center gap-1">
              <span className="text-muted-foreground">超额</span>
              <span className={`font-mono ${moveColor(bench!.excess_return)}`}>{pct(bench!.excess_return)}</span>
            </span>
          )}
          {indices.slice(0, 5).map((ix) => (
            <span key={`${ix.market}:${ix.symbol}`} className="flex items-center gap-1">
              <span className="text-muted-foreground">{ix.name}</span>
              {ix.current_price != null && (
                <span className="font-mono text-foreground/80">{ix.current_price.toFixed(2)}</span>
              )}
              <span className={`font-mono ${moveColor(ix.change_pct)}`}>{pct(ix.change_pct)}</span>
            </span>
          ))}
        </div>
      </div>

      {/* 今日要紧事(主角) */}
      <div className="card mb-3 p-4">
        <div className="mb-2 flex items-center gap-2">
          <Activity className="h-4 w-4 text-primary" />
          <h2 className="text-sm font-semibold">今日要紧事</h2>
          <span className="text-[11px] text-muted-foreground">你的持仓/自选里今天该关注的</span>
          {feed.length > 0 && (
            <button
              type="button"
              onClick={() => setShareDigest(true)}
              className="ml-auto inline-flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-primary"
              title="生成今日盯盘分享图"
            >
              <Share2 className="h-3.5 w-3.5" />
              分享图
            </button>
          )}
        </div>
        {loading && candidates.length === 0 ? (
          <div className="py-6 text-center text-[12px] text-muted-foreground">扫描中…</div>
        ) : candidates.length === 0 ? (
          todos.length > 0 ? (
            <div className="space-y-1.5 py-1">
              <div className="text-[11px] text-muted-foreground">今日暂无异动/触发 ✓ · 待办:</div>
              {todos.map((t, i) => (
                <div
                  key={i}
                  className={`flex items-center gap-2 py-1 text-[12px] ${t.symbol ? 'cursor-pointer hover:bg-accent/30' : ''}`}
                  onClick={() => t.symbol && openStock(t.symbol, t.market || 'CN', '')}
                >
                  <span className="shrink-0 rounded bg-amber-500/15 px-1 text-[9px] text-amber-600">
                    {t.type === 'no_alert' ? '加提醒' : '将到期'}
                  </span>
                  <span className="truncate">{t.message}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="py-6 text-center text-[12px] text-muted-foreground">今日暂无明显异动或触发信号 ✓</div>
          )
        ) : (
          <div className="divide-y divide-border/40">
            {feed.map((it, i) => {
              const badge = FEED_BADGE[it.type] || { label: it.type, cls: 'bg-accent text-muted-foreground' }
              return (
                <div
                  key={i}
                  className={`flex items-center gap-3 py-2 ${it.symbol ? 'cursor-pointer hover:bg-accent/30' : ''}`}
                  onClick={() => it.symbol && openStock(it.symbol, it.market || 'CN', it.name || '')}
                >
                  <span className={`shrink-0 rounded px-1 text-[9px] ${badge.cls}`}>{badge.label}</span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] font-medium">{it.name || it.symbol}</div>
                    {it.why && <div className="truncate text-[11px] text-muted-foreground">{it.why}</div>}
                  </div>
                  {it.change_pct != null && (
                    <div className={`shrink-0 font-mono text-[13px] ${moveColor(it.change_pct)}`}>{pct(it.change_pct)}</div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {/* 组合体检(并入首页) */}
        <div className="card p-4">
          <div className="mb-2 flex items-center gap-2">
            <ShieldAlert className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-semibold">组合体检</h2>
            {benchReady && (
              <button
                type="button"
                onClick={() => setShareBench(true)}
                className="ml-auto inline-flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-primary"
                title="生成模拟盘成绩单分享图"
              >
                <Share2 className="h-3.5 w-3.5" />
                成绩单
              </button>
            )}
            {hasHoldings && (
              <button
                type="button"
                onClick={() => setShareDiag(true)}
                className={`${benchReady ? '' : 'ml-auto'} inline-flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-primary`}
                title="生成组合体检分享图"
              >
                <Share2 className="h-3.5 w-3.5" />
                体检图
              </button>
            )}
          </div>
          {!hasHoldings ? (
            <div className="py-6 text-center text-[12px] text-muted-foreground">
              {loading ? '加载中…' : '暂无持仓,添加持仓后这里给风险与相对大盘表现'}
            </div>
          ) : (
            <div className="space-y-2 text-[12px]">
              <div className="flex items-center justify-between rounded bg-accent/15 px-2 py-1.5">
                <span className="text-muted-foreground">近 60 日相对大盘</span>
                <span className={`font-mono font-semibold ${moveColor(benchReady ? bench!.excess_return : null)}`}>
                  {benchReady ? `超额 ${pct(bench!.excess_return)}` : '数据不足'}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">持仓 {diag!.position_count} 只 · 最大单仓</span>
                <span className={`font-mono ${diag!.max_weight >= 0.4 ? 'text-amber-600' : ''}`}>
                  {(diag!.max_weight * 100).toFixed(0)}%
                </span>
              </div>
              {Object.entries(diag!.by_market).map(([m, v]) => {
                const w = diag!.total_market_value > 0 ? (v / diag!.total_market_value) * 100 : 0
                return (
                  <div key={m}>
                    <div className="flex justify-between text-[11px]">
                      <span>{m}</span>
                      <span className="font-mono">{w.toFixed(0)}%</span>
                    </div>
                    <div className="h-1.5 rounded bg-accent/40">
                      <div className="h-1.5 rounded bg-primary/60" style={{ width: `${Math.min(100, w)}%` }} />
                    </div>
                  </div>
                )
              })}
              {diag!.alerts.length > 0 ? (
                <div className="space-y-1 pt-1">
                  {diag!.alerts.map((a, i) => (
                    <div key={i} className="flex items-start gap-1 text-[11px] text-amber-600">
                      <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                      <span>{a}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="pt-1 text-[11px] text-emerald-500">✓ 集中度/分布未见明显风险</div>
              )}
              {attribution.length > 1 && (
                <div className="flex justify-between pt-1 text-[11px] text-muted-foreground">
                  <span>
                    领涨 <span className={moveColor(attribution[0].contribution_pct)}>{attribution[0].name} {pct(attribution[0].contribution_pct)}</span>
                  </span>
                  <span>
                    拖累{' '}
                    <span className={moveColor(attribution[attribution.length - 1].contribution_pct)}>
                      {attribution[attribution.length - 1].name} {pct(attribution[attribution.length - 1].contribution_pct)}
                    </span>
                  </span>
                </div>
              )}
              <button
                type="button"
                onClick={runAiReview}
                disabled={aiReviewLoading}
                className="mt-1 w-full rounded border border-border/60 py-1 text-[11px] text-primary hover:bg-accent/30 disabled:opacity-60"
              >
                {aiReviewLoading ? 'AI 体检中…' : 'AI 体检报告'}
              </button>
              {aiReview?.content && (
                <div className="prose prose-sm dark:prose-invert mt-1 max-w-none break-words text-[12px] [&_p]:my-1 [&_ul]:my-1">
                  <ReactMarkdown>{aiReview.content}</ReactMarkdown>
                </div>
              )}
            </div>
          )}
        </div>

        {/* 机会精选 */}
        <div className="card p-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              <Sparkles className="h-4 w-4 text-primary" />
              机会精选
            </h2>
            <button
              type="button"
              className="text-[11px] text-muted-foreground hover:text-foreground"
              onClick={() => navigate('/opportunities')}
            >
              进入机会页
            </button>
          </div>
          {opportunities.length === 0 ? (
            <div className="py-6 text-center text-[12px] text-muted-foreground">{loading ? '加载中…' : '暂无活跃机会信号'}</div>
          ) : (
            <div className="divide-y divide-border/40">
              {opportunities.slice(0, 3).map((o) => (
                <div
                  key={`${o.stock_market}:${o.stock_symbol}`}
                  className="flex cursor-pointer items-center gap-2 py-2 hover:bg-accent/30"
                  onClick={() => openStock(o.stock_symbol, o.stock_market, o.stock_name || o.stock_symbol)}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate text-[13px] font-medium">{o.stock_name || o.stock_symbol}</span>
                      {o.action_label && <span className="rounded bg-primary/10 px-1 text-[9px] text-primary">{o.action_label}</span>}
                    </div>
                    {(o.signal || o.reason) && <div className="truncate text-[11px] text-muted-foreground">{o.signal || o.reason}</div>}
                  </div>
                  <div className="shrink-0 text-right">
                    <div className="font-mono text-[13px] text-foreground">{(o.rank_score ?? o.score ?? 0).toFixed(0)}</div>
                    <div className="text-[9px] text-muted-foreground">评分</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {brief && (brief.title || brief.content) && (
        <div className="card mt-3 p-4">
          <div className="mb-1 flex items-center justify-between">
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              <Newspaper className="h-4 w-4 text-primary" />
              {brief.agent_label}
              {brief.date && <span className="text-[11px] font-normal text-muted-foreground">{brief.date}</span>}
            </h2>
            {brief.content && (
              <button type="button" className="text-[11px] text-muted-foreground" onClick={() => setBriefOpen((v) => !v)}>
                {briefOpen ? '收起' : '展开'}
              </button>
            )}
          </div>
          {brief.title && <div className="text-[13px] font-medium">{brief.title}</div>}
          {briefOpen && brief.content && (
            <div className="prose prose-sm dark:prose-invert mt-1 max-w-none break-words text-[12px] [&_p]:my-1 [&_ul]:my-1">
              <ReactMarkdown>{brief.content}</ReactMarkdown>
            </div>
          )}
        </div>
      )}

      <DiscoveryPanel monitorStocks={scan} onOpenStock={openStock} />

      <StockInsightModal
        open={modal.open}
        onOpenChange={(o) => setModal((m) => ({ ...m, open: o }))}
        symbol={modal.symbol}
        market={modal.market}
        stockName={modal.name}
        hasPosition={modal.hasPosition}
      />

      {/* 分享卡:模拟盘成绩单(vs 基准) */}
      {shareBench && bench && (
        <BenchmarkShareCard open={shareBench} onClose={() => setShareBench(false)} bench={bench} />
      )}

      {/* 分享卡:组合体检(脱敏,无金额) */}
      {shareDiag && diag && (
        <DiagnosticsShareCard
          open={shareDiag}
          onClose={() => setShareDiag(false)}
          diag={diag}
          excessReturn={benchReady ? bench!.excess_return : null}
          benchmarkLabel={bench?.benchmark_label}
        />
      )}

      {/* 分享卡:今日盯盘 digest */}
      <DigestShareCard
        open={shareDigest}
        onClose={() => setShareDigest(false)}
        date={today}
        items={feed.map((it) => ({
          type: it.type,
          name: it.name,
          symbol: it.symbol,
          why: it.why,
          change_pct: it.change_pct ?? null,
        }))}
      />

      <Onboarding open={showOnboarding} onComplete={handleOnboardingComplete} hasStocks={hasWatchlist} />
    </div>
  )
}
