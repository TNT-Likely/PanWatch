import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  lazy,
  Suspense,
} from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  Bookmark,
  ChevronRight,
  FlaskConical,
  RefreshCw,
  Search,
  Sparkles,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  fetchAPI,
  qlibApi,
  stocksApi,
  type QlibBasketItem,
  type QlibHealth,
  type QlibOpportunities,
  type QlibRun,
  type QlibMarket,
  type StockItem,
} from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@panwatch/base-ui/components/ui/select'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@panwatch/base-ui/components/ui/dialog'
import { useConfirm } from '@panwatch/base-ui/components/ui/confirm-dialog'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { useLocalStorage } from '@/lib/utils'
import QlibDataPipeline from '@/components/qlib-data-pipeline'
import PaperPlanDialog, { planError } from '@/components/paper-plan-dialog'
import PaperPlanStatus from '@/components/paper-plan-status'
import PaperAutomation from '@/components/paper-automation'
import {
  securityCode,
  defaultOpportunityFilters,
  filterOpportunityCards,
  opportunityCards,
  savedOpportunityFilters,
  type OpportunityCard,
  type OpportunityFilters,
  type OpportunityPosition,
} from '@/lib/opportunity-view'

const StockInsightModal = lazy(
  () => import('@panwatch/biz-ui/components/stock-insight-modal'),
)
const format = (value: number | null | undefined, digits = 2) =>
  value == null || !Number.isFinite(value) ? '—' : value.toFixed(digits)
const cardsCount = (run: QlibRun | null | undefined, predictions: unknown[]) =>
  run?.prediction_count ?? predictions.length

export default function Opportunities() {
  const { t } = useTranslation('qlib')
  const qlibT = t as unknown as (
    key: string,
    values?: Record<string, unknown>,
  ) => string
  const tr = (key: string, values?: Record<string, unknown>) =>
    qlibT(`opportunityPage.${key}`, values)
  const confirmAction = useConfirm()
  const { toast } = useToast()
  const [searchParams, setSearchParams] = useSearchParams()
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [metadataError, setMetadataError] = useState(false)
  const [opportunities, setOpportunities] = useState<QlibOpportunities | null>(
    null,
  )
  const [health, setHealth] = useState<QlibHealth | null>(null)
  const [runs, setRuns] = useState<QlibRun[]>([])
  const [basket, setBasket] = useState<QlibBasketItem[]>([])
  const [stocks, setStocks] = useState<StockItem[] | null>(null)
  const [positions, setPositions] = useState<OpportunityPosition[] | null>(null)
  const [history, setHistory] = useState(() =>
    /^[a-f0-9]{32}$/.test(searchParams.get('run') ?? '')
      ? searchParams.get('run')!
      : 'latest',
  )
  const [market, setMarket] = useState<QlibMarket>(() =>
    ['CN', 'HK', 'US'].includes(searchParams.get('market') ?? '')
      ? (searchParams.get('market') as QlibMarket)
      : 'CN',
  )
  const [view, setView] = useState<'featured' | 'all'>('featured')
  const [planOpen, setPlanOpen] = useState(false)
  const [planFocus, setPlanFocus] = useState<string>()
  const [planVersion, setPlanVersion] = useState(0)
  const [page, setPage] = useState(1)
  const [savedFilters, setFilters] = useLocalStorage<OpportunityFilters>(
    'panwatch_qlib_opportunity_filters_v1',
    defaultOpportunityFilters,
  )
  const filters = useMemo(
    () => savedOpportunityFilters(savedFilters),
    [savedFilters],
  )
  const [draft, setDraft] = useState<OpportunityFilters>(filters)
  const [detail, setDetail] = useState<OpportunityCard | null>(null)
  const [detailRun, setDetailRun] = useState<QlibRun | null>(null)
  const [detailTotal, setDetailTotal] = useState(0)
  const [technical, setTechnical] = useState(false)
  const generation = useRef(0)

  const reload = useCallback(async () => {
    const epoch = ++generation.current
    setLoading(true)
    try {
      const [o, h, r, b] = await Promise.all([
        qlibApi.opportunities({
          market,
          page: String(page),
          limit: '50',
          search: filters.search,
          top: filters.top === 'all' ? '0' : filters.top,
          source: filters.source,
          holding: filters.holding,
          availability: filters.availability,
          view,
          ...(history !== 'latest' ? { run_id: history } : {}),
        }),
        qlibApi.health(),
        qlibApi.selections(market),
        qlibApi.basket(),
      ])
      if (epoch !== generation.current) return
      setOpportunities(o)
      setHealth(h)
      setRuns(r.items)
      setBasket(b.items)
      setError('')
      const needsMetadata =
        o.run?.data_kind === 'market' ||
        r.items.some((item) => item.data_kind === 'market')
      if (needsMetadata) {
        const [s, p] = await Promise.allSettled([
          stocksApi.list(),
          fetchAPI<OpportunityPosition[]>('/positions'),
        ])
        if (epoch !== generation.current) return
        setStocks(s.status === 'fulfilled' ? s.value : null)
        setPositions(p.status === 'fulfilled' ? p.value : null)
        setMetadataError(s.status === 'rejected' || p.status === 'rejected')
      } else {
        setStocks(null)
        setPositions(null)
        setMetadataError(false)
      }
    } catch (cause) {
      if (epoch === generation.current) {
        setError(
          cause instanceof Error
            ? cause.message
            : 'qlib_opportunities_unavailable',
        )
        setHealth(null)
      }
    } finally {
      if (epoch === generation.current) setLoading(false)
    }
    // Keep language changes independent of network reloads.
  }, [market, page, filters, history, view])
  useEffect(() => {
    const requested = searchParams.get('run') ?? ''
    setHistory(/^[a-f0-9]{32}$/.test(requested) ? requested : 'latest')
    const requestedMarket = searchParams.get('market')
    if (requestedMarket && ['CN', 'HK', 'US'].includes(requestedMarket))
      setMarket(requestedMarket as QlibMarket)
    setPage(1)
  }, [searchParams])
  useEffect(() => {
    void reload()
    return () => {
      generation.current += 1
    }
  }, [reload])
  const run =
    opportunities?.total != null
      ? opportunities.run
      : history === 'latest'
        ? opportunities?.run
        : runs.find((item) => item.id === history)
  const predictions =
    opportunities?.total != null || history === 'latest'
      ? (opportunities?.items ?? [])
      : (run?.predictions ?? [])
  const totalPredictions =
    opportunities?.prediction_count ?? cardsCount(run, predictions)
  const synthetic = run?.data_kind === 'synthetic'
  const detailSynthetic = detailRun?.data_kind === 'synthetic'
  const cards = useMemo(
    () =>
      opportunityCards(predictions, {
        synthetic,
        topk: run?.config.topk ?? 0,
        stocks,
        positions,
        basket,
        names: run?.names,
      }),
    [
      predictions,
      synthetic,
      run?.config.topk,
      run?.names,
      stocks,
      positions,
      basket,
    ],
  )
  const visible = useMemo(
    () =>
      opportunities?.total != null
        ? cards
        : filterOpportunityCards(
            cards,
            synthetic ? { ...filters, holding: 'all' } : filters,
          ),
    [cards, filters, synthetic, opportunities?.total],
  )
  const metadataUnavailable =
    !synthetic && (stocks == null || positions == null)
  const filteredMetadataUnavailable =
    !synthetic &&
    ((filters.source === 'watched' && stocks == null) ||
      (filters.holding !== 'all' && metadataUnavailable))
  const status =
    history !== 'latest'
      ? 'history'
      : error
        ? 'failed'
        : (opportunities?.status ?? 'no_champion')

  const follow = async (card: OpportunityCard) => {
    if (!run || busy) return
    if (!synthetic && card.watched == null) return
    if (!synthetic && card.watched && (card.held == null || card.held)) return
    if (
      !synthetic &&
      card.watched &&
      !(await confirmAction(
        tr('unfollowConfirm', { name: card.name || card.symbol }),
        { destructive: true },
      ))
    )
      return
    setBusy(true)
    try {
      if (synthetic) {
        const result = await qlibApi.setBasket(
          run.id,
          card.prediction.symbol,
          !card.watched,
        )
        setBasket(result.items)
      } else {
        const stock = stocks?.find(
          (item) =>
            item.market.toUpperCase() === card.prediction.market &&
            securityCode(item.symbol, item.market.toUpperCase()) ===
              card.symbol,
        )
        if (stock) await stocksApi.remove(stock.id)
        else
          await stocksApi.create({
            symbol: card.symbol,
            name: card.name || card.symbol,
            market: card.prediction.market,
            asset_type: 'stock',
          })
        await reload()
      }
      toast(tr('followSaved'), 'success')
    } catch (cause) {
      toast(cause instanceof Error ? cause.message : tr('loadFailed'), 'error')
    } finally {
      setBusy(false)
    }
  }
  const select = (
    label: string,
    value: string,
    change: (value: string) => void,
    options: [string, string][],
    disabled = false,
  ) => (
    <Select value={value} onValueChange={change} disabled={disabled}>
      <SelectTrigger className="h-8 text-xs" aria-label={tr(label)}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map(([key, text]) => (
          <SelectItem key={key} value={key}>
            {tr(text)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
  const setFilter = (key: keyof OpportunityFilters, value: string) =>
    setDraft((previous) => ({ ...previous, [key]: value }))
  const summary = (label: string, value: string, note: string) => (
    <div className="card p-3">
      <div className="text-[11px] text-muted-foreground">{tr(label)}</div>
      <div className="mt-1 text-lg font-bold">{value}</div>
      <div className="mt-1 text-[10px] text-muted-foreground">{note}</div>
    </div>
  )

  return (
    <div
      className="page-container min-w-0 pb-10"
      data-testid="opportunities-page"
    >
      <header className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold tracking-tight md:text-[22px]">
            <Sparkles className="h-5 w-5 text-primary" />
            {tr('title')}
          </h1>
          <p className="mt-1 text-xs text-muted-foreground">{tr('subtitle')}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] text-muted-foreground">
            {run?.asof_date ?? '—'}
          </span>
          <Button
            size="sm"
            variant="secondary"
            className="h-8 text-xs"
            disabled={loading || busy}
            onClick={() => void reload()}
          >
            <RefreshCw
              className={`mr-1 h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`}
            />
            {tr('refresh')}
          </Button>
          <Link
            to="/research"
            data-tour="opportunity-research"
            className="inline-flex h-8 items-center gap-1 rounded-md border px-3 text-xs text-muted-foreground hover:text-foreground"
          >
            <FlaskConical className="h-3.5 w-3.5" />
            {tr('research')}
          </Link>
          <Button
            size="sm"
            disabled={!run || history !== 'latest' || loading}
            onClick={() => {
              setPlanFocus(undefined)
              setPlanOpen(true)
            }}
          >
            {t('plan.previewTitle')}
          </Button>
        </div>
      </header>
      <PaperAutomation onChange={() => setPlanVersion(value => value + 1)} />
      <div className="card mb-4 p-3 space-y-2 text-sm">
        <p className="font-medium">{tr('journey')}</p>
        <p className="text-xs text-muted-foreground">{tr('journeyNote')}</p>
        <div
          className="flex flex-wrap gap-2"
          role="group"
          aria-label={tr('view')}
        >
          <Button
            size="sm"
            variant={view === 'featured' ? 'default' : 'outline'}
            aria-pressed={view === 'featured'}
            onClick={() => {
              setView('featured')
              setPage(1)
            }}
          >
            {tr('featured')}
          </Button>
          <Button
            size="sm"
            variant={view === 'all' ? 'default' : 'outline'}
            aria-pressed={view === 'all'}
            onClick={() => {
              setView('all')
              setPage(1)
            }}
          >
            {tr('fullPool')}
          </Button>
        </div>
      </div>
      <details className="mb-4" open={!loading && !run}>
        <summary className="cursor-pointer text-sm text-primary">
          {tr('advancedData')}
        </summary>
        <div className="mt-2">
          <QlibDataPipeline onChange={reload} market={market} />
        </div>
      </details>
      <div className="mb-4">
        <PaperPlanStatus compact refreshKey={planVersion} />
      </div>
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        {summary(
          'candidates',
          opportunities ? String(totalPredictions) : '—',
          tr('poolOnly'),
        )}
        {summary(
          synthetic ? 'sampleFollows' : 'watchedCandidates',
          !run || (!synthetic && stocks == null)
            ? '—'
            : String(
                run?.watched_count ??
                  cards.filter((card) => card.watched).length,
              ),
          tr(synthetic ? 'sampleFollowNote' : 'watchNote'),
        )}
        {summary(
          'results',
          opportunities ? String(opportunities.total ?? visible.length) : '—',
          tr('rankOnly'),
        )}
        {summary('dataDate', run?.asof_date ?? '—', tr(`statuses.${status}`))}
      </div>
      <div
        role="status"
        className="card mb-4 flex flex-wrap items-center justify-between gap-2 p-3 text-xs"
      >
        <div className="min-w-0">
          <span className="font-medium">{tr(`statuses.${status}`)}</span>
          {run && (
            <span className="ml-2 text-muted-foreground">
              {tr('horizon', { days: run.horizon })} ·{' '}
              {tr(synthetic ? 'sample' : 'marketData')}
            </span>
          )}
        </div>
        <Link
          to="/paper-trading"
          className="inline-flex items-center gap-1 text-primary"
        >
          {tr('paper')}
          <ChevronRight className="h-3.5 w-3.5" />
        </Link>
        <p className="w-full text-[11px] text-muted-foreground">
          {tr(synthetic ? 'sampleNotice' : 'notice')}
        </p>
      </div>
      {error && (
        <div
          role="alert"
          className="mb-4 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs break-words"
        >
          {tr('loadFailed')}: {error}
        </div>
      )}
      {!health?.available && !loading && (
        <p role="status" className="card mb-4 p-3 text-xs">
          {tr('workerUnavailable')}
        </p>
      )}
      {metadataError && (
        <p role="status" className="card mb-4 p-3 text-xs">
          {tr('metadataUnavailable')}
        </p>
      )}
      <form
        data-tour="opportunity-filters"
        className="card mb-4 p-3 md:p-4"
        onSubmit={(event) => {
          event.preventDefault()
          setFilters({ ...draft })
          if (
            draft.search.trim() ||
            draft.top !== 'all' ||
            draft.source !== 'all' ||
            draft.holding !== 'all'
          )
            setView('all')
          setPage(1)
        }}
      >
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4 lg:grid-cols-7">
          {select(
            'market',
            market,
            (value) => {
              setMarket(value as QlibMarket)
              setSearchParams({ market: value })
              setPage(1)
              setHistory('latest')
              setDetail(null)
              setOpportunities(null)
            },
            [
              ['CN', 'cn'],
              ['HK', 'hk'],
              ['US', 'us'],
            ],
          )}
          {select(
            'source',
            draft.source,
            (value) => setFilter('source', value),
            [
              ['all', 'allSources'],
              ['watched', synthetic ? 'sampleFollows' : 'myWatchlist'],
            ],
            !synthetic && stocks == null,
          )}
          {select(
            'holding',
            synthetic ? 'all' : draft.holding,
            (value) => setFilter('holding', value),
            [
              ['all', synthetic ? 'sampleHolding' : 'allHolding'],
              ['unheld', 'unheld'],
              ['held', 'held'],
            ],
            synthetic || metadataUnavailable,
          )}
          {select('rankFilter', draft.top, (value) => setFilter('top', value), [
            ['all', 'allRanks'],
            ['10', 'top10'],
            ['20', 'top20'],
            ['50', 'top50'],
          ])}
          {select(
            'availability',
            draft.availability,
            (value) => setFilter('availability', value),
            [
              ['all', 'allAvailability'],
              ['tradable', 'tradable'],
              ['blocked', 'blocked'],
            ],
          )}
          <Button type="submit" size="sm" className="h-8 text-xs">
            {tr('apply')}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-8 text-xs"
            onClick={() => {
              setDraft({ ...defaultOpportunityFilters })
              setFilters({ ...defaultOpportunityFilters })
              setPage(1)
            }}
          >
            {tr('clear')}
          </Button>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <label className="flex min-w-0 flex-1 items-center gap-2 rounded-md border px-2 py-1.5 text-xs">
            <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <input
              value={draft.search}
              onChange={(event) => setFilter('search', event.target.value)}
              aria-label={tr('search')}
              placeholder={tr('search')}
              className="w-full min-w-0 bg-transparent outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </label>
          <div className="w-full sm:w-64">
            <Select
              value={history}
              onValueChange={(value) => {
                setHistory(value)
                setSearchParams(
                  value === 'latest' ? { market } : { market, run: value },
                )
                setPage(1)
                setDetail(null)
                setTechnical(false)
              }}
            >
              <SelectTrigger className="h-8 text-xs" aria-label={tr('history')}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="latest">{tr('latest')}</SelectItem>
                {runs.map((item) => (
                  <SelectItem key={item.id} value={item.id}>
                    {item.asof_date} · {item.model_id.slice(0, 8)} ·{' '}
                    {item.id.slice(0, 8)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        {history !== 'latest' && (
          <p className="mt-2 text-[11px] text-muted-foreground">
            {tr('historyNotice')}
          </p>
        )}
      </form>
      {loading && (
        <p role="status" className="py-3 text-xs text-muted-foreground">
          {tr('loading')}
        </p>
      )}
      {opportunities?.total != null && opportunities.total > 50 && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs">
          <span>
            {qlibT('universe.page', { page, total: opportunities.total })}
          </span>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={loading || page <= 1}
              onClick={() => setPage((p) => p - 1)}
            >
              {qlibT('universe.previous')}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={loading || page * 50 >= opportunities.total}
              onClick={() => setPage((p) => p + 1)}
            >
              {qlibT('universe.next')}
            </Button>
          </div>
        </div>
      )}
      <div
        data-tour="opportunity-signals"
        className="grid min-h-12 grid-cols-1 gap-3 md:grid-cols-2"
        aria-busy={loading}
      >
        {visible.map((card) => (
          <article
            key={card.prediction.symbol}
            className={`card p-4 transition-colors ${card.selected ? 'border-primary/30' : ''}`}
          >
            <button
              type="button"
              className="w-full min-w-0 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
              onClick={() => {
                setDetail(card)
                setDetailRun(run ?? null)
                setDetailTotal(totalPredictions)
                setTechnical(false)
              }}
              aria-label={tr('openDetail', {
                name: synthetic
                  ? tr('sampleName', { code: card.symbol })
                  : card.name || card.symbol,
              })}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <h2 className="truncate text-[15px] font-semibold">
                    {synthetic
                      ? tr('sampleName', { code: card.symbol })
                      : card.name || card.symbol}
                  </h2>
                  <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                    {card.prediction.market}:{card.symbol}
                  </p>
                </div>
                <div className="shrink-0 text-right">
                  <span className="rounded border border-primary/25 bg-primary/10 px-2 py-0.5 text-[11px] text-primary">
                    {tr(card.selected ? 'candidate' : 'observe')}
                  </span>
                  <p className="mt-1 text-xs font-semibold">
                    {tr('rank', {
                      rank: card.prediction.rank,
                      total: totalPredictions,
                    })}
                  </p>
                </div>
              </div>
              <p className="mt-2 text-xs text-foreground">
                {tr('cardHint', { days: run?.horizon ?? '—' })}
              </p>
              <div className="mt-3 space-y-1 text-xs">
                <p className="font-medium">{tr('observableEvidence')}</p>
                {card.prediction.observed_context ? (
                  <>
                    <p>
                      {tr('priceContext', {
                        value: (
                          card.prediction.observed_context
                            .return_20_observations * 100
                        ).toFixed(1),
                      })}
                    </p>
                    <p>
                      {tr('volumeContext', {
                        value:
                          card.prediction.observed_context.volume_ratio_20_observations?.toFixed(
                            2,
                          ) ?? '—',
                      })}
                    </p>
                    <p className="text-muted-foreground">
                      {tr('drawdownContext', {
                        value: (
                          card.prediction.observed_context
                            .drawdown_from_60_observation_high * 100
                        ).toFixed(1),
                      })}
                    </p>
                  </>
                ) : (
                  <p className="text-muted-foreground">
                    {tr('contextUnavailable')}
                  </p>
                )}
                <p className="text-muted-foreground">{tr('evidenceNote')}</p>
                <p>
                  {tr('simulationState')}:{' '}
                  {synthetic
                    ? planError('qlib_synthetic_execution_not_supported', qlibT)
                    : card.prediction.paper_block_reason
                      ? planError(card.prediction.paper_block_reason, qlibT)
                      : tr('planPreviewRequired')}
                </p>
              </div>
              <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-[11px]">
                <div>
                  <dt className="text-muted-foreground">
                    {tr('referencePrice')}
                  </dt>
                  <dd className="mt-0.5 font-mono">
                    {format(card.prediction.reference_close)}{' '}
                    {card.prediction.currency ??
                      (market === 'CN'
                        ? 'CNY'
                        : market === 'HK'
                          ? 'HKD'
                          : 'USD')}{' '}
                    <span className="text-muted-foreground">
                      {run?.asof_date}
                    </span>
                  </dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">{tr('rankChange')}</dt>
                  <dd className="mt-0.5">
                    {card.prediction.rank_change == null
                      ? '—'
                      : tr('rankDelta', {
                          delta:
                            card.prediction.rank_change > 0
                              ? `+${card.prediction.rank_change}`
                              : card.prediction.rank_change,
                        })}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">{tr('holding')}</dt>
                  <dd className="mt-0.5">
                    {tr(
                      synthetic
                        ? 'sampleHolding'
                        : card.held == null
                          ? 'unknown'
                          : card.held
                            ? 'held'
                            : 'unheld',
                    )}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">
                    {tr('availability')}
                  </dt>
                  <dd className="mt-0.5">
                    {tr(card.prediction.tradable ? 'tradable' : 'blocked')}
                  </dd>
                </div>
              </dl>
            </button>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-border/50 pt-3">
              <span className="text-[10px] text-muted-foreground">
                {tr(synthetic ? 'sample' : 'poolOnly')} · {tr('engine')}
              </span>
              <Button
                size="sm"
                variant="outline"
                disabled={history !== 'latest' || loading}
                onClick={() => {
                  setPlanFocus(card.prediction.symbol)
                  setPlanOpen(true)
                }}
              >
                {t('plan.previewTitle')}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 gap-1 px-2 text-[11px]"
                disabled={
                  busy ||
                  (!synthetic &&
                    (card.watched == null ||
                      (!!card.watched && (card.held == null || !!card.held))))
                }
                onClick={() => void follow(card)}
                aria-label={tr(card.watched ? 'unfollowLabel' : 'followLabel', {
                  code: card.symbol,
                })}
              >
                <Bookmark
                  className={`h-3.5 w-3.5 ${card.watched ? 'fill-primary text-primary' : ''}`}
                />
                {tr(
                  synthetic
                    ? card.watched
                      ? 'sampleFollowed'
                      : 'sampleFollow'
                    : card.held && card.watched
                      ? 'held'
                      : card.watched
                        ? 'followed'
                        : 'follow',
                )}
              </Button>
            </div>
          </article>
        ))}
      </div>
      {!loading && !visible.length && (
        <div className="card mt-4 p-8 text-center text-xs text-muted-foreground">
          <p>
            {tr(
              filteredMetadataUnavailable
                ? 'metadataUnavailable'
                : cards.length
                  ? 'emptyFiltered'
                  : `empty.${status}`,
            )}
          </p>
          {!cards.length && (
            <Link
              className="mt-3 inline-block text-primary hover:underline"
              to="/research?tab=data"
            >
              {tr('setup')}
            </Link>
          )}
        </div>
      )}
      <details className="mt-5 text-xs text-muted-foreground">
        <summary className="cursor-pointer">{tr('method')}</summary>
        <p className="mt-2 leading-relaxed">{tr('methodNote')}</p>
      </details>
      <PaperPlanDialog
        runId={run?.id ?? null}
        focusSymbol={planFocus}
        open={planOpen}
        onOpenChange={setPlanOpen}
        onConfirmed={() => {
          setPlanVersion((v) => v + 1)
          void reload()
        }}
      />
      {detail && !detailSynthetic && (
        <Suspense fallback={<p role="status">{tr('loading')}</p>}>
          <StockInsightModal
            open
            symbol={detail.symbol}
            market={detail.prediction.market}
            stockName={detail.name ?? undefined}
            hasPosition={detail.held ?? undefined}
            onOpenChange={(open) => {
              if (!open) {
                setDetail(null)
                void reload()
              }
            }}
            researchContext={
              <div className="mb-3 rounded-lg border border-primary/20 bg-primary/5 p-3 text-xs">
                <p>
                  {tr('rank', {
                    rank: detail.prediction.rank,
                    total: detailTotal,
                  })}{' '}
                  · {detailRun?.asof_date} ·{' '}
                  {tr('horizon', { days: detailRun?.horizon })}
                </p>
                <p className="mt-1 text-muted-foreground">{tr('detailNote')}</p>
                <Button
                  variant="link"
                  size="sm"
                  className="h-7 px-0 text-xs"
                  onClick={() => setTechnical(true)}
                >
                  {tr('technical')}
                </Button>
              </div>
            }
          />
        </Suspense>
      )}
      <Dialog
        open={!!detail && (detailSynthetic || technical)}
        onOpenChange={(open) => {
          if (!open) {
            setTechnical(false)
            if (detailSynthetic) setDetail(null)
          }
        }}
      >
        <DialogContent className="w-[92vw] max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              {detailSynthetic
                ? tr('sampleName', { code: detail?.symbol })
                : detail?.name || detail?.symbol}
            </DialogTitle>
            <DialogDescription>
              {tr(detailSynthetic ? 'sampleNotice' : 'detailNote')}
            </DialogDescription>
          </DialogHeader>
          {detail && (
            <>
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div>
                  {tr('rank', {
                    rank: detail.prediction.rank,
                    total: detailTotal,
                  })}
                </div>
                <div>
                  {tr('referencePrice')}:{' '}
                  {format(detail.prediction.reference_close)}
                </div>
                <div>
                  {tr('dataDate')}: {detailRun?.asof_date}
                </div>
                <div>{tr('horizon', { days: detailRun?.horizon })}</div>
              </div>
              <details className="mt-3 text-xs" open={technical}>
                <summary className="cursor-pointer text-primary">
                  {tr('technical')}
                </summary>
                <p className="mt-2 text-muted-foreground">{t('inputNote')}</p>
                <p className="mt-2">
                  {t('rawScore')}: {format(detail.prediction.raw_score, 5)} ·{' '}
                  {t('model')}:{' '}
                  <code className="break-all">{detailRun?.model_id}</code>
                </p>
                <div className="mt-3 max-h-56 overflow-y-auto scrollbar rounded border p-2">
                  <dl className="space-y-1">
                    {Object.entries(detail.prediction.factor_inputs).map(
                      ([name, value]) => (
                        <div
                          key={name}
                          className="flex justify-between gap-3 font-mono"
                        >
                          <dt>{name}</dt>
                          <dd>{format(value, 4)}</dd>
                        </div>
                      ),
                    )}
                  </dl>
                </div>
              </details>
              <Link
                to="/research"
                className="mt-3 text-xs text-primary hover:underline"
              >
                {tr('research')}
              </Link>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
