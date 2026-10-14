import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  Activity,
  ArrowRight,
  Bookmark,
  Database,
  Download,
  FlaskConical,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  qlibApi,
  defaultQlibConfig,
  type QlibConfig,
  type QlibDataset,
  type QlibJob,
  type QlibModel,
  type QlibGovernance,
  type QlibHealth,
  type QlibOpportunities,
  type QlibPrediction,
  type QlibTemplate,
  type QlibRun,
  type QlibPaper,
  type QlibBasketItem,
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

const tabs = [
  'opportunities',
  'research',
  'governance',
  'data',
  'paper',
] as const
type Tab = (typeof tabs)[number]
const number = (value: number | null | undefined, digits = 3) =>
  value == null || !Number.isFinite(value) ? '—' : value.toFixed(digits)
const percent = (value: number | null | undefined) =>
  value == null ? '—' : `${number(value * 100, 2)}%`
const short = (value: string | null | undefined) =>
  value ? value.slice(0, 10) : '—'
const key = () => crypto.randomUUID().replace(/-/g, '')
const inputClass =
  'w-full min-w-0 rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border bg-card p-4 sm:p-5">
      <h2 className="mb-3 text-base font-semibold">{title}</h2>
      {children}
    </section>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border bg-background/50 p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 break-words font-mono text-lg">{value}</div>
    </div>
  )
}

function Curve({ model }: { model: QlibModel }) {
  const { t } = useTranslation('qlib')
  const qlibT = t as unknown as (key: string) => string
  const curve = model.metrics.backtest.curve
  if (curve.length < 2) return null
  const values = curve.map((row) => row.equity)
  const low = Math.min(...values),
    high = Math.max(...values),
    range = high - low || 1
  const points = values
    .map(
      (value, index) =>
        `${12 + (index / (values.length - 1)) * 576},${138 - ((value - low) / range) * 122}`,
    )
    .join(' ')
  return (
    <figure className="my-3 rounded-lg border bg-background p-3">
      <svg
        viewBox="0 0 600 156"
        role="img"
        aria-label={qlibT('netReturn')}
        className="w-full"
      >
        <polyline
          points={points}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className="text-primary"
        />
      </svg>
      <figcaption className="flex justify-between gap-2 text-xs text-muted-foreground">
        <span>{curve[0].date}</span>
        <span>{curve[curve.length - 1].date}</span>
      </figcaption>
    </figure>
  )
}

export default function QlibResearch() {
  const { t } = useTranslation('qlib')
  const qlibT = t as unknown as (key: string) => string
  const confirmAction = useConfirm()
  const { toast } = useToast()
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedTab = searchParams.get('tab')
  const tab: Tab = tabs.includes(requestedTab as Tab) ? requestedTab as Tab : 'research'
  const setTab = (value: Tab) => setSearchParams({ tab: value }, { replace: true })
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [health, setHealth] = useState<QlibHealth | null>(null)
  const [data, setData] = useState<QlibDataset[]>([])
  const [jobs, setJobs] = useState<QlibJob[]>([])
  const [experiments, setExperiments] = useState<QlibModel[]>([])
  const [governance, setGovernance] = useState<QlibGovernance | null>(null)
  const [opportunities, setOpportunities] = useState<QlibOpportunities | null>(
    null,
  )
  const [templates, setTemplates] = useState<QlibTemplate[]>([])
  const [runs, setRuns] = useState<QlibRun[]>([])
  const [paper, setPaper] = useState<QlibPaper | null>(null)
  const [basket, setBasket] = useState<QlibBasketItem[]>([])
  const [dataset, setDataset] = useState('none')
  const [config, setConfig] = useState<QlibConfig>({ ...defaultQlibConfig })
  const [templateName, setTemplateName] = useState('')
  const [history, setHistory] = useState('latest')
  const [detail, setDetail] = useState<QlibPrediction | null>(null)
  const [report, setReport] = useState<QlibModel | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [comparison, setComparison] = useState<{
    comparable: boolean
    items: QlibModel[]
  } | null>(null)
  const [approval, setApproval] = useState<QlibModel | null>(null)
  const [approvalAction, setApprovalAction] = useState<'publish' | 'retire'>('publish')
  const [mode, setMode] = useState<'research' | 'validated'>('research')
  const [reason, setReason] = useState('')
  const [scheduleEnabled, setScheduleEnabled] = useState(false)
  const [dailyHour, setDailyHour] = useState(18)
  const [retrainDays, setRetrainDays] = useState(30)
  const [tickStatus, setTickStatus] = useState('')
  const upload = useRef<HTMLInputElement>(null)
  const trainRequest = useRef<string | null>(null)
  const generation = useRef(0)

  const reload = useCallback(async () => {
    const current = ++generation.current
    try {
      const [h, d, j, e, g, o, t, r, p, b] = await Promise.all([
        qlibApi.health(),
        qlibApi.datasets(),
        qlibApi.jobs(),
        qlibApi.experiments(),
        qlibApi.governance(),
        qlibApi.opportunities(),
        qlibApi.templates(),
        qlibApi.selections(),
        qlibApi.executions(),
        qlibApi.basket(),
      ])
      if (generation.current !== current) return
      setHealth(h)
      setData(d.items)
      setJobs(j.items)
      setExperiments(e.items)
      setGovernance(g)
      setOpportunities(o)
      setTemplates(t.items)
      setRuns(r.items)
      setPaper(p)
      setBasket(b.items)
      setError('')
      setDataset((previous) =>
        previous === 'none' && d.items.length ? d.items[0].id : previous,
      )
    } catch (cause) {
      if (generation.current === current)
        setError(cause instanceof Error ? cause.message : qlibT('error'))
    } finally {
      if (generation.current === current) setLoading(false)
    }
  }, [qlibT])
  useEffect(() => {
    void reload()
    return () => {
      generation.current += 1
    }
  }, [reload])
  useEffect(() => {
    if (
      !jobs.some((job) => job.status === 'queued' || job.status === 'running')
    )
      return
    const timer = window.setInterval(() => void reload(), 4000)
    return () => window.clearInterval(timer)
  }, [jobs, reload])
  useEffect(() => {
    if (!governance) return
    setScheduleEnabled(governance.settings.enabled)
    setDailyHour(governance.settings.daily_hour)
    setRetrainDays(governance.settings.retrain_every_days)
  }, [
    governance?.settings.enabled,
    governance?.settings.daily_hour,
    governance?.settings.retrain_every_days,
  ])

  const act = async (operation: () => Promise<unknown>, message?: string) => {
    setBusy(true)
    setError('')
    try {
      await operation()
      toast(message ?? qlibT('done'), 'success')
      await reload()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : qlibT('error'))
      toast(qlibT('error'), 'error')
    } finally {
      setBusy(false)
    }
  }
  const train = (datasetId = dataset, params = config) =>
    act(async () => {
      const requestKey = trainRequest.current ?? key()
      trainRequest.current = requestKey
      await qlibApi.train({
        dataset_id: datasetId,
        config: params,
        request_key: requestKey,
      })
      trainRequest.current = null
    })
  const importFile = async (file?: File) => {
    if (!file) return
    if (file.size > 40_000_000 || !file.name.toLowerCase().endsWith('.json')) {
      setError(qlibT('badFile'))
      return
    }
    let payload: unknown
    try {
      payload = JSON.parse(await file.text())
    } catch {
      setError(qlibT('badFile'))
      return
    }
    if (!(await confirmAction(qlibT('importConfirm')))) return
    await act(async () => {
      const item = await qlibApi.importDataset(payload)
      setDataset(item.id)
    })
  }
  const exportReport = async (item: QlibModel) =>
    act(async () => {
      const output = await qlibApi.export(item.id)
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(output, null, 2)], {
          type: 'application/json',
        }),
      )
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `panwatch-qlib-${item.id}.json`
      anchor.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    })
  const historical = runs.find((run) => run.id === history)
  const activeRun = history === 'latest' ? opportunities?.run : historical
  const items =
    history === 'latest'
      ? (opportunities?.items ?? [])
      : (historical?.predictions ?? [])
  const selector = (
    <Select
      value={dataset}
      onValueChange={(value) => {
        trainRequest.current = null
        setDataset(value)
      }}
    >
      <SelectTrigger aria-label={qlibT('dataset')}>
        <SelectValue placeholder={qlibT('chooseDataset')} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="none">{qlibT('chooseDataset')}</SelectItem>
        {data.map((item) => (
          <SelectItem key={item.id} value={item.id}>
            {item.name} · {item.end_date} · {short(item.id)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
  const metrics = (item: QlibModel) => (
    <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
      <Metric label={qlibT('rankIC')} value={number(item.metrics.rank_ic)} />
      <Metric label={qlibT('icDays')} value={String(item.metrics.ic_dates)} />
      <Metric
        label={qlibT('netReturn')}
        value={percent(item.metrics.backtest.return)}
      />
      <Metric
        label={qlibT('drawdown')}
        value={percent(item.metrics.backtest.max_drawdown)}
      />
    </div>
  )
  const numericFields: {
    field: keyof QlibConfig
    label: string
    step: number
    min: number
    max: number
  }[] = [
    { field: 'horizon', label: 'horizon', step: 1, min: 1, max: 20 },
    { field: 'topk', label: 'topk', step: 1, min: 1, max: 50 },
    { field: 'num_boost_round', label: 'rounds', step: 1, min: 10, max: 400 },
    {
      field: 'capital',
      label: 'capital',
      step: 10000,
      min: 10000,
      max: 100000000,
    },
    { field: 'open_cost', label: 'openCost', step: 0.0001, min: 0, max: 0.02 },
    {
      field: 'close_cost',
      label: 'closeCost',
      step: 0.0001,
      min: 0,
      max: 0.02,
    },
    { field: 'min_cost', label: 'minCost', step: 1, min: 0, max: 100 },
    { field: 'max_drawdown', label: 'maxDD', step: 0.01, min: 0, max: 1 },
    { field: 'min_ic', label: 'minIC', step: 0.01, min: -1, max: 1 },
    { field: 'min_test_days', label: 'minDays', step: 1, min: 10, max: 250 },
  ]

  return (
    <div className="min-w-0 space-y-4 pb-6" data-testid="qlib-workbench">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link to="/opportunities" className="mb-2 inline-block text-xs text-primary hover:underline">
            {qlibT('opportunityPage.back')}
          </Link>
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            <FlaskConical className="h-6 w-6 text-primary" />
            {qlibT('title')}
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            {qlibT('subtitle')}
          </p>
        </div>
        <Button variant="outline" onClick={() => void reload()} disabled={busy}>
          <RefreshCw className="mr-2 h-4 w-4" />
          {qlibT('refresh')}
        </Button>
      </header>
      <p className="rounded-lg border border-primary/20 bg-primary/5 p-3 text-sm">
        {qlibT('notice')}
      </p>
      {error && (
        <div
          role="alert"
          className="break-words rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm"
        >
          <strong>{qlibT('error')}</strong>
          <p>{error}</p>
        </div>
      )}
      {health && !health.available && (
        <div role="status" className="rounded-lg border p-3 text-sm">
          {qlibT('missingWorker')} <code>{health.error_code}</code>
        </div>
      )}
      <div
        role="tablist"
        aria-label={qlibT('title')}
        className="flex gap-1 overflow-x-auto scrollbar rounded-lg border bg-card p-1"
      >
        {tabs.map((item, index) => (
          <Button
            key={item}
            id={`qlib-tab-${item}`}
            role="tab"
            aria-selected={tab === item}
            aria-controls={`qlib-panel-${item}`}
            tabIndex={tab === item ? 0 : -1}
            variant={tab === item ? 'default' : 'ghost'}
            className="shrink-0"
            onClick={() => setTab(item)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
                event.preventDefault()
                const next =
                  tabs[
                    (index +
                      (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) %
                      tabs.length
                  ]
                setTab(next)
                document.getElementById(`qlib-tab-${next}`)?.focus()
              }
            }}
          >
            {qlibT(`tabs.${item}`)}
          </Button>
        ))}
      </div>
      {loading ? (
        <p role="status">{qlibT('loading')}</p>
      ) : (
        <div
          id={`qlib-panel-${tab}`}
          role="tabpanel"
          aria-labelledby={`qlib-tab-${tab}`}
          className="space-y-4"
        >
          {tab === 'opportunities' && (
            <>
              <Panel title={qlibT('tabs.opportunities')}>
                <div className="mb-4 grid gap-3 sm:grid-cols-2">
                  <div>
                    <div className="mb-1 text-xs text-muted-foreground">
                      {qlibT('history')}
                    </div>
                    <Select value={history} onValueChange={setHistory}>
                      <SelectTrigger aria-label={qlibT('history')}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="latest">
                          {qlibT('latest')}
                        </SelectItem>
                        {runs.map((run) => (
                          <SelectItem key={run.id} value={run.id}>
                            {run.asof_date} · {short(run.model_id)} ·{' '}
                            {short(run.id)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="flex items-end gap-2">
                    <Button
                      disabled={
                        busy ||
                        history !== 'latest' ||
                        opportunities?.status !== 'ready' || !health?.available
                      }
                      onClick={async () => {
                        if (
                          activeRun &&
                          (await confirmAction(qlibT('decisionConfirm')))
                        )
                          await act(
                            () => qlibApi.decision(activeRun.id, true),
                            qlibT('decisionSaved'),
                          )
                      }}
                    >
                      <ArrowRight className="mr-2 h-4 w-4" />
                      {qlibT('decision')}
                    </Button>
                  </div>
                </div>
                <div className="mb-4 flex flex-wrap gap-2 text-xs">
                  <span className="rounded bg-accent px-2 py-1">
                    {qlibT(`status.${opportunities?.status ?? 'no_champion'}`)}
                  </span>
                  <span className="rounded bg-accent px-2 py-1">
                    {qlibT(`mode.${opportunities?.mode ?? 'research'}`)}
                  </span>
                  {activeRun && (
                    <>
                      <span>
                        {qlibT('asof')}: {activeRun.asof_date}
                      </span>
                      <span>
                        {qlibT('model')}:{' '}
                        <code>{short(activeRun.model_id)}</code>
                      </span>
                      <span>
                        {qlibT('horizon')}: {activeRun.horizon}
                      </span>
                      <span>{qlibT(`dataKind.${activeRun.data_kind}`)}</span>
                    </>
                  )}
                </div>
                {!items.length ? (
                  <p className="py-8 text-center text-sm text-muted-foreground">
                    {qlibT('empty')}
                  </p>
                ) : (
                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                    {items.map((item) => (
                      <article
                        key={item.symbol}
                        className="rounded-xl border p-4"
                      >
                        <div className="flex items-center justify-between">
                          <h3 className="font-semibold">{item.symbol}</h3>
                          <span className="rounded bg-primary/10 px-2 py-1 font-mono text-primary">
                            #{item.rank}
                          </span>
                        </div>
                        <dl className="my-3 grid grid-cols-2 gap-2 text-sm">
                          <div>
                            <dt className="text-xs text-muted-foreground">
                              {qlibT('rawScore')}
                            </dt>
                            <dd className="font-mono">
                              {number(item.raw_score, 5)}
                            </dd>
                          </div>
                          <div>
                            <dt className="text-xs text-muted-foreground">
                              {qlibT('percentile')}
                            </dt>
                            <dd>{number(item.rank_percentile, 1)}</dd>
                          </div>
                          <div>
                            <dt className="text-xs text-muted-foreground">
                              {qlibT('rankChange')}
                            </dt>
                            <dd>{item.rank_change ?? '—'}</dd>
                          </div>
                          <div className="text-xs">
                            {qlibT(item.tradable ? 'tradable' : 'blocked')}
                          </div>
                        </dl>
                        <div className="flex flex-wrap gap-2">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setDetail(item)}
                          >
                            {qlibT('inputs')}
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            aria-label={qlibT(
                              basket.some((row) => row.symbol === item.symbol)
                                ? 'removeBasket'
                                : 'addBasket',
                            )}
                            disabled={busy || !activeRun}
                            onClick={() =>
                              activeRun &&
                              void act(() =>
                                qlibApi.setBasket(
                                  activeRun.id,
                                  item.symbol,
                                  !basket.some(
                                    (row) => row.symbol === item.symbol,
                                  ),
                                ),
                              )
                            }
                          >
                            <Bookmark
                              className={`h-4 w-4 ${basket.some((row) => row.symbol === item.symbol) ? 'fill-primary text-primary' : ''}`}
                            />
                          </Button>
                        </div>
                      </article>
                    ))}
                  </div>
                )}
              </Panel>
              <Panel title={qlibT('basket')}>
                <div className="flex flex-wrap gap-2">
                  {basket.length ? (
                    basket.map((row) => (
                      <span
                        key={row.symbol}
                        className="rounded border px-3 py-1 text-sm"
                      >
                        {row.symbol} · <code>{short(row.run_id)}</code>
                      </span>
                    ))
                  ) : (
                    <span className="text-sm text-muted-foreground">—</span>
                  )}
                </div>
              </Panel>
            </>
          )}
          {tab === 'research' && (
            <>
              <Panel title={qlibT('algorithm')}>
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="space-y-1 text-sm">
                    <span>{qlibT('dataset')}</span>
                    {selector}
                  </label>
                  <label className="space-y-1 text-sm">
                    <span>{qlibT('algorithm')}</span>
                    <Select
                      value={config.algorithm}
                      onValueChange={(value) => {
                        trainRequest.current = null
                        setConfig({
                          ...config,
                          algorithm: value as QlibConfig['algorithm'],
                        })
                      }}
                    >
                      <SelectTrigger aria-label={qlibT('algorithm')}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="lightgbm">
                          {qlibT('lightgbm')}
                        </SelectItem>
                        <SelectItem value="ridge">{qlibT('ridge')}</SelectItem>
                      </SelectContent>
                    </Select>
                  </label>
                </div>
                <p className="mb-2 mt-4 text-xs text-muted-foreground">
                  {qlibT('fees')}
                </p>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                  {numericFields.map((field) => (
                    <label key={field.field} className="space-y-1 text-xs">
                      <span>{qlibT(field.label)}</span>
                      <input
                        aria-label={qlibT(field.label)}
                        className={inputClass}
                        type="number"
                        min={field.min}
                        max={field.max}
                        step={field.step}
                        value={config[field.field]}
                        onChange={(event) => {
                          trainRequest.current = null
                          setConfig({
                            ...config,
                            [field.field]: Number(event.target.value),
                          })
                        }}
                      />
                    </label>
                  ))}
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Button
                    disabled={busy || dataset === 'none' || !health?.available}
                    onClick={() => void train()}
                  >
                    <FlaskConical className="mr-2 h-4 w-4" />
                    {qlibT('train')}
                  </Button>
                  <Button
                    variant="outline"
                    disabled={
                      busy ||
                      dataset === 'none' ||
                      !governance?.champion_id ||
                      !health?.available
                    }
                    onClick={() =>
                      governance?.champion_id &&
                      void act(() =>
                        qlibApi.predict(
                          governance.champion_id!,
                          dataset,
                          key(),
                        ),
                      )
                    }
                  >
                    {qlibT('predict')}
                  </Button>
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  <input
                    className={`${inputClass} max-w-sm`}
                    aria-label={qlibT('templateName')}
                    placeholder={qlibT('templateName')}
                    maxLength={100}
                    value={templateName}
                    onChange={(event) => setTemplateName(event.target.value)}
                  />
                  <Button
                    variant="outline"
                    disabled={busy || !templateName.trim()}
                    onClick={() =>
                      void act(() =>
                        qlibApi.saveTemplate(templateName.trim(), config),
                      )
                    }
                  >
                    {qlibT('saveTemplate')}
                  </Button>
                </div>
                {templates.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {templates.map((item) => (
                      <Button
                        variant="ghost"
                        size="sm"
                        key={item.id}
                        onClick={() => {
                          trainRequest.current = null
                          setConfig(item.config)
                        }}
                      >
                        {item.name}
                      </Button>
                    ))}
                  </div>
                )}
              </Panel>
              <Panel title={qlibT('jobs')}>
                <div className="space-y-2">
                  {jobs.length ? (
                    jobs.map((job) => (
                      <div
                        key={job.id}
                        className="flex flex-wrap items-center justify-between gap-2 rounded border p-3 text-sm"
                      >
                        <div>
                          <code>{short(job.id)}</code> · {job.config.algorithm}{' '}
                          · {qlibT(`jobStatus.${job.status}`)}
                          {job.error_code && (
                            <p className="mt-1 break-all font-mono text-xs text-destructive">
                              {job.error_code}
                            </p>
                          )}
                        </div>
                        {['queued', 'running'].includes(job.status) && (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={busy}
                            onClick={async () => {
                              if (await confirmAction(qlibT('cancelConfirm')))
                                await act(() => qlibApi.cancel(job.id))
                            }}
                          >
                            {qlibT('cancel')}
                          </Button>
                        )}
                        {job.status === 'failed' && (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={busy || !health?.available}
                            onClick={() =>
                              job.model_id ? void act(() => qlibApi.predict(job.model_id!, job.dataset_id, key())) : void train(job.dataset_id, job.config)
                            }
                          >
                            {qlibT('replay')}
                          </Button>
                        )}
                      </div>
                    ))
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      {qlibT('empty')}
                    </p>
                  )}
                </div>
              </Panel>
              <Panel title={qlibT('experiment')}>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy || selected.length < 2}
                  onClick={() =>
                    void act(async () =>
                      setComparison(await qlibApi.compare(selected)),
                    )
                  }
                >
                  {qlibT('compare')}
                </Button>
                <div className="mt-3 space-y-3">
                  {experiments.map((item) => (
                    <article key={item.id} className="rounded-lg border p-3">
                      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                        <label className="flex min-w-0 items-center gap-2 text-sm">
                          <input
                            type="checkbox"
                            checked={selected.includes(item.id)}
                            aria-label={`${qlibT('compare')} ${short(item.id)}`}
                            disabled={
                              !selected.includes(item.id) &&
                              selected.length >= 4
                            }
                            onChange={(event) =>
                              setSelected(
                                event.target.checked
                                  ? [...selected, item.id]
                                  : selected.filter((id) => id !== item.id),
                              )
                            }
                          />
                          <span className="break-all">
                            {item.name} · {short(item.id)} ·{' '}
                            {qlibT(`dataKind.${item.data_kind}`)}
                          </span>
                        </label>
                        <div className="flex gap-2">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setReport(item)}
                          >
                            {qlibT('inputs')}
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={busy || !health?.available}
                            onClick={() =>
                              void train(item.dataset_id, item.config)
                            }
                          >
                            {qlibT('replay')}
                          </Button>
                        </div>
                      </div>
                      {metrics(item)}
                    </article>
                  ))}
                </div>
              </Panel>
            </>
          )}
          {tab === 'governance' && (
            <>
              <Panel title={qlibT('registry')}>
                <p className="mb-4 text-sm text-muted-foreground">
                  {qlibT('publishNote')}
                </p>
                <div className="space-y-3">
                  {governance?.models.map((item) => (
                    <article key={item.id} className="rounded-lg border p-4">
                      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                        <div className="text-sm">
                          {item.name} · <code>{short(item.id)}</code> ·{' '}
                          {qlibT(`modelStates.${item.state ?? 'candidate'}`)} ·{' '}
                          {qlibT(`dataKind.${item.data_kind}`)}
                        </div>
                        <div className="flex gap-2">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setReport(item)}
                          >
                            {qlibT('inputs')}
                          </Button>
                          <Button
                            size="sm"
                            disabled={busy || item.state === 'champion' || item.state === 'retired'}
                            onClick={() => {
                              setReason('')
                              setMode('research')
                              setApprovalAction('publish')
                              setApproval(item)
                            }}
                          >
                            <ShieldCheck className="mr-2 h-4 w-4" />
                            {qlibT(
                              item.state === 'previous'
                                ? 'rollback'
                                : 'publish',
                            )}
                          </Button>
                          <Button variant="outline" size="sm" disabled={busy || item.state === 'champion' || item.state === 'retired'}
                            onClick={() => { setReason(''); setApprovalAction('retire'); setApproval(item) }}>
                            {qlibT('retire')}
                          </Button>
                        </div>
                      </div>
                      {metrics(item)}
                    </article>
                  ))}
                </div>
              </Panel>
              <Panel title={qlibT('monitoring')}>
                {health?.monitor.forward && <div className="mb-3 grid grid-cols-2 gap-2 lg:grid-cols-4">
                  <Metric label={qlibT('forwardNet')} value={number(health.monitor.forward.champion_realized_net_pnl, 2)} />
                  <Metric label={qlibT('forwardOpenNet')} value={number(health.monitor.forward.champion_unrealized_net_pnl, 2)} />
                  <Metric label={qlibT('exposure')} value={percent(health.monitor.forward.qlib_equity_exposure)} />
                  <Metric label={qlibT('maxWeight')} value={percent(health.monitor.forward.max_position_equity_weight)} />
                </div>}
                <p className="mb-3 text-xs text-muted-foreground">{qlibT('forwardMonitorNote')}</p>
                <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
                  <Metric
                    label={qlibT('asof')}
                    value={health?.monitor.prediction_date ?? '—'}
                  />
                  <Metric
                    label={qlibT('coverage')}
                    value={percent(health?.monitor.coverage.fraction)}
                  />
                  <Metric
                    label={qlibT('churn')}
                    value={percent(health?.monitor.topk_change_fraction)}
                  />
                  <Metric
                    label={qlibT('inputShift')}
                    value={number(health?.monitor.coverage.input_abs_z_mean)}
                  />
                </div>
                <p className="mt-3 text-xs text-muted-foreground">
                  {qlibT('shiftNote')}
                </p>
                {health?.monitor.recent_failures.map((item) => (
                  <p
                    key={item.id}
                    className="mt-2 break-all font-mono text-xs text-destructive"
                  >
                    {short(item.id)} · {item.error_code}
                  </p>
                ))}
              </Panel>
              <Panel title={qlibT('schedule')}>
                <p className="mb-3 text-sm text-muted-foreground">
                  {qlibT('scheduleHint')}
                </p>
                <div className="grid gap-3 sm:grid-cols-2">
                  {selector}
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={scheduleEnabled}
                      onChange={(event) =>
                        setScheduleEnabled(event.target.checked)
                      }
                    />
                    {qlibT('enabled')}
                  </label>
                  <label className="space-y-1 text-sm">
                    <span>{qlibT('dailyHour')}</span>
                    <input
                      aria-label={qlibT('dailyHour')}
                      className={inputClass}
                      type="number"
                      min={16}
                      max={23}
                      value={dailyHour}
                      onChange={(event) =>
                        setDailyHour(Number(event.target.value))
                      }
                    />
                  </label>
                  <label className="space-y-1 text-sm">
                    <span>{qlibT('retrainDays')}</span>
                    <input
                      aria-label={qlibT('retrainDays')}
                      className={inputClass}
                      type="number"
                      min={7}
                      max={365}
                      value={retrainDays}
                      onChange={(event) =>
                        setRetrainDays(Number(event.target.value))
                      }
                    />
                  </label>
                </div>
                <div className="mt-3 flex gap-2">
                  <Button
                    disabled={busy || (scheduleEnabled && dataset === 'none')}
                    onClick={() =>
                      void act(() =>
                        qlibApi.schedule({
                          enabled: scheduleEnabled,
                          dataset_id: dataset === 'none' ? null : dataset,
                          daily_hour: dailyHour,
                          retrain_every_days: retrainDays,
                        }),
                      )
                    }
                  >
                    {qlibT('saveSchedule')}
                  </Button>
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => void act(async () => { const result = await qlibApi.tick(); setTickStatus(result.status) })}
                  >
                    <Activity className="mr-2 h-4 w-4" />
                    {qlibT('tick')}
                  </Button>
                </div>
                {tickStatus && <p role="status" className="mt-3 text-sm">{qlibT(`tickStates.${tickStatus}`)}</p>}
              </Panel>
              <Panel title={qlibT('audit')}>
                <div className="max-h-72 overflow-y-auto scrollbar space-y-2">
                  {governance?.audit.map((item) => (
                    <div key={item.id} className="rounded border p-2 text-xs">
                      <div className="flex flex-wrap gap-2">
                        <time>{item.created_at}</time>
                        <code>{item.action}</code>
                        <span>{item.actor}</span>
                        <code>{short(item.target_id)}</code>
                      </div>
                      <pre className="mt-1 whitespace-pre-wrap break-all text-muted-foreground">
                        {JSON.stringify(item.detail)}
                      </pre>
                    </div>
                  ))}
                </div>
              </Panel>
            </>
          )}
          {tab === 'data' && (
            <>
              <Panel title={qlibT('tabs.data')}>
                <div className="flex items-center gap-2 text-sm">
                  <Database className="h-4 w-4" />
                  {qlibT(health?.available ? 'runtimeReady' : 'missingWorker')}
                </div>
                <div className="my-3 grid gap-2 sm:grid-cols-3">
                  {Object.entries(health?.runtime ?? {}).map(
                    ([name, version]) => (
                      <div key={name} className="rounded border p-2 text-sm">
                        <code>
                          {name} · {version}
                        </code>
                      </div>
                    ),
                  )}
                </div>
                <p className="mb-3 text-sm text-muted-foreground">
                  {qlibT('noAutoData')}
                </p>
                <p className="mb-3 text-sm text-muted-foreground">
                  {qlibT('importHint')}
                </p>
                <input
                  ref={upload}
                  type="file"
                  accept=".json,application/json"
                  className="sr-only"
                  aria-label={qlibT('upload')}
                  onChange={(event) => {
                    void importFile(event.target.files?.[0])
                    event.target.value = ''
                  }}
                />
                <Button disabled={busy} onClick={() => upload.current?.click()}>
                  {qlibT('upload')}
                </Button>
              </Panel>
              {data.map((item) => (
                <Panel key={item.id} title={item.name}>
                  <p className="mb-2 text-sm">
                    {qlibT(`dataKind.${item.data_kind}`)} · {item.start_date} →{' '}
                    {item.end_date} · {item.rows.toLocaleString()} ·{' '}
                    {item.symbols.length}
                  </p>
                  <dl className="space-y-1 break-words text-sm">
                    <div>
                      {qlibT('source')}: {item.source}
                    </div>
                    <div>
                      {qlibT('license')}: {item.license}
                    </div>
                    <div>
                      {item.universe_kind} · {item.adjustment}
                    </div>
                    <div className="break-all">
                      {qlibT('hash')}: <code>{item.content_hash}</code>
                    </div>
                  </dl>
                  {item.warnings.map((warning) => (
                    <p
                      className="mt-2 break-all font-mono text-xs text-muted-foreground"
                      key={warning}
                    >
                      {warning}
                    </p>
                  ))}
                </Panel>
              ))}
            </>
          )}
          {tab === 'paper' && (
            <>
              <Panel title={qlibT('paperTitle')}>
                <p className="mb-4 text-sm text-muted-foreground">
                  {qlibT('paperNote')}
                </p>
                <Link
                  to="/paper-trading"
                  className="inline-flex items-center gap-2 text-sm text-primary hover:underline"
                >
                  {qlibT('openPaper')}
                  <ArrowRight className="h-4 w-4" />
                </Link>
                <div className="mt-4 space-y-3">
                  {paper?.decisions.map((item) => (
                    <article
                      className="rounded-lg border p-3 text-sm"
                      key={item.id}
                    >
                      <p className="break-all">
                        <code>{short(item.id)}</code> · {qlibT('executionDate')}
                        : {item.execution_date} ·{' '}
                        {qlibT(
                          item.research_mode
                            ? 'mode.research'
                            : 'mode.validated',
                        )}
                      </p>
                      <p className="mt-1 break-all text-xs text-muted-foreground">
                        {item.policy} · {short(item.run_id)} ·{' '}
                        {short(item.model_id)}
                      </p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {Object.entries(item.target_weights).map(
                          ([symbol, weight]) => (
                            <span
                              className="rounded bg-accent px-2 py-1 font-mono text-xs"
                              key={symbol}
                            >
                              {symbol} {percent(weight)}
                            </span>
                          ),
                        )}
                      </div>
                    </article>
                  ))}
                </div>
              </Panel>
              <Panel title={qlibT('executions')}>
                <div className="space-y-2">
                  {paper?.items.map((item) => (
                    <article
                      className="rounded border p-3 text-sm"
                      key={item.id}
                    >
                      <div className="flex flex-wrap gap-2">
                        <span>{item.symbol}</span>
                        <code>
                          {item.side} · {item.status}
                        </code>
                        <span>
                          {qlibT('quantity')}: {item.quantity}
                        </span>
                        <span>
                          {qlibT('price')}: {number(item.price, 2)}
                        </span>
                      </div>
                      <p className="mt-1 break-all font-mono text-xs text-muted-foreground">
                        {item.reason} · {short(item.run_id)} /{' '}
                        {short(item.model_id)} / {short(item.decision_id)}
                      </p>
                    </article>
                  ))}
                </div>
              </Panel>
            </>
          )}
        </div>
      )}
      <Dialog
        open={detail !== null}
        onOpenChange={(open) => {
          if (!open) setDetail(null)
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              {detail?.symbol} · {qlibT('inputs')}
            </DialogTitle>
            <DialogDescription>{qlibT('inputNote')}</DialogDescription>
          </DialogHeader>
          <div className="break-all font-mono text-xs">
            {activeRun?.id} / {activeRun?.model_id} / {activeRun?.dataset_id}
          </div>
          <dl className="grid grid-cols-2 gap-2 text-sm">
            {Object.entries(detail?.factor_inputs ?? {}).map(
              ([name, value]) => (
                <div key={name} className="rounded border p-2">
                  <dt className="break-all text-xs text-muted-foreground">
                    {name}
                  </dt>
                  <dd className="font-mono">{number(value, 5)}</dd>
                </div>
              ),
            )}
          </dl>
        </DialogContent>
      </Dialog>
      <Dialog
        open={report !== null}
        onOpenChange={(open) => {
          if (!open) setReport(null)
        }}
      >
        <DialogContent className="max-w-4xl">
          <DialogHeader>
            <DialogTitle>
              {report?.name} · {qlibT('experiment')}
            </DialogTitle>
            <DialogDescription>{qlibT('backtestNote')}</DialogDescription>
          </DialogHeader>
          {report && (
            <>
              <p className="break-all font-mono text-xs">
                {report.id} / {report.dataset_id} ·{' '}
                {qlibT(`dataKind.${report.data_kind}`)}
              </p>
              {metrics(report)}
              <Curve model={report} />
              <Panel title={qlibT('baseline')}>
                <div className="grid grid-cols-2 gap-2">
                  <Metric
                    label={qlibT('rankIC')}
                    value={number(report.baseline.rank_ic)}
                  />
                  <Metric
                    label={qlibT('netReturn')}
                    value={percent(report.baseline.backtest.return)}
                  />
                </div>
              </Panel>
              <Panel title={qlibT('splits')}>
                <dl className="space-y-2 text-sm">
                  {Object.entries(report.segments).map(([name, dates]) => (
                    <div key={name}>
                      <dt className="font-semibold">
                        {qlibT(`splitLabels.${name}`)}
                      </dt>
                      <dd>{dates.join(' → ')}</dd>
                    </div>
                  ))}
                </dl>
              </Panel>
              <Panel title={qlibT('rolling')}>
                <div className="space-y-2 text-sm">
                  {report.rolling.map((fold, index) => (
                    <div key={index} className="rounded border p-2">
                      <p>{fold.segments.test.join(' → ')}</p>
                      <p>
                        {qlibT('rankIC')}: {number(fold.metrics.rank_ic)} ·{' '}
                        {qlibT('icDays')}: {fold.metrics.ic_dates} ·{' '}
                        {qlibT('drawdown')}:{' '}
                        {percent(fold.metrics.backtest.max_drawdown)}
                      </p>
                    </div>
                  ))}
                </div>
              </Panel>
              <Button disabled={busy} onClick={() => void exportReport(report)}>
                <Download className="mr-2 h-4 w-4" />
                {qlibT('export')}
              </Button>
            </>
          )}
        </DialogContent>
      </Dialog>
      <Dialog
        open={approval !== null}
        onOpenChange={(open) => {
          if (!open) setApproval(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {qlibT(approvalAction === 'retire' ? 'retire' : approval?.state === 'previous' ? 'rollback' : 'publish')}
            </DialogTitle>
            <DialogDescription>{qlibT(approvalAction === 'retire' ? 'retireNote' : 'publishNote')}</DialogDescription>
          </DialogHeader>
          <code className="break-all text-xs">{approval?.id}</code>
          {approvalAction === 'publish' && <label className="space-y-1 text-sm">
            <span>{qlibT('publishMode')}</span>
            <Select
              value={mode}
              onValueChange={(value) => setMode(value as typeof mode)}
            >
              <SelectTrigger aria-label={qlibT('publishMode')}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="research">
                  {qlibT('mode.research')}
                </SelectItem>
                <SelectItem value="validated">
                  {qlibT('mode.validated')}
                </SelectItem>
              </SelectContent>
            </Select>
          </label>}
          <label className="space-y-1 text-sm">
            <span>{qlibT('reason')}</span>
            <textarea
              className={inputClass}
              aria-label={qlibT('reason')}
              value={reason}
              maxLength={500}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <Button
            disabled={busy || reason.trim().length < 5}
            onClick={async () => {
              if (
                approval &&
                governance &&
                (await confirmAction(qlibT('confirmPublish')))
              )
                await act(async () => {
                  if (approvalAction === 'retire') await qlibApi.retire(approval.id, governance.revision, reason.trim())
                  else await qlibApi.publish(
                    approval.id,
                    governance.revision,
                    reason.trim(),
                    mode,
                    approval.state === 'previous',
                  )
                  setApproval(null)
                })
            }}
          >
            {qlibT(approvalAction === 'retire' ? 'retire' : 'publish')}
          </Button>
        </DialogContent>
      </Dialog>
      <Dialog
        open={comparison !== null}
        onOpenChange={(open) => {
          if (!open) setComparison(null)
        }}
      >
        <DialogContent className="max-w-4xl">
          <DialogHeader>
            <DialogTitle>{qlibT('comparison')}</DialogTitle>
            <DialogDescription>
              {qlibT(comparison?.comparable ? 'comparable' : 'incomparable')}
            </DialogDescription>
          </DialogHeader>
          {comparison?.items.map((item) => (
            <div key={item.id} className="space-y-2">
              <p className="text-sm">
                {item.name} · <code>{short(item.id)}</code>
              </p>
              {metrics(item)}
            </div>
          ))}
        </DialogContent>
      </Dialog>
    </div>
  )
}
