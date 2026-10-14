import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  qlibApi,
  stocksApi,
  defaultQlibConfig,
  type QlibDataSourceStatus,
  type QlibGovernance,
  type QlibJob,
  type QlibMarketDataSource,
  type QlibModel,
} from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@panwatch/base-ui/components/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@panwatch/base-ui/components/ui/select'
import { useConfirm } from '@panwatch/base-ui/components/ui/confirm-dialog'
import { cnSymbol } from '@/lib/opportunity-view'

// Explicit, editable example universe, not recommendations or an index replica.
const EXAMPLE_POOL =
  'SH600000 SH600036 SH600519 SH600900 SH601318 SH601398 SZ000001 SZ000333 SZ000651 SZ000858 SZ002415 SZ002594'
const field = 'w-full rounded-md border bg-background px-3 py-2 text-sm'
const active = (status?: string) => status === 'queued' || status === 'running'
const requestKey = () => `marketdata-${crypto.randomUUID()}`
export default function QlibDataPipeline({
  onChange,
}: {
  onChange: () => Promise<void>
}) {
  const { t } = useTranslation('qlib')
  const tr = (key: string, values?: Record<string, unknown>) =>
    (t as unknown as (k: string, v?: Record<string, unknown>) => string)(
      `dataPipeline.${key}`,
      values,
    )
  const confirmAction = useConfirm()
  const [state, setState] = useState<QlibDataSourceStatus | null>(null)
  const [governance, setGovernance] = useState<QlibGovernance | null>(null)
  const [jobs, setJobs] = useState<QlibJob[]>([])
  const [open, setOpen] = useState(false)
  const [pool, setPool] = useState(EXAMPLE_POOL)
  const [vendor, setVendor] = useState<QlibMarketDataSource['vendor']>('auto')
  const [license, setLicense] = useState('')
  const [consent, setConsent] = useState(false)
  const [automatic, setAutomatic] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [approval, setApproval] = useState<QlibModel | null>(null)
  const [reason, setReason] = useState('')
  const alive = useRef(true)
  const reload = useCallback(async () => {
    const [data, modelState, modelJobs] = await Promise.all([
      qlibApi.dataSource(),
      qlibApi.governance(),
      qlibApi.jobs(),
    ])
    if (!alive.current) return
    setState(data)
    setGovernance(modelState)
    setJobs(modelJobs.items)
    setError('')
  }, [])
  useEffect(() => {
    alive.current = true
    void reload().catch(() => {
      if (alive.current) setError('qlib_marketdata_status_unavailable')
    })
    return () => {
      alive.current = false
    }
  }, [reload])
  const training =
    jobs.find(
      (job) =>
        job.kind === 'train' &&
        job.dataset_id === state?.dataset_id &&
        active(job.status),
    ) ?? jobs.find((job) => job.id === state?.job?.train_job_id)
  const working = active(state?.job?.status) || active(training?.status)
  const prediction = jobs.find((job) => job.id === state?.job?.predict_job_id)
  const pending = working || active(prediction?.status)
  useEffect(() => {
    const timer = window.setInterval(
      () => {
        void reload()
          .then(() => {
            if (alive.current) return onChange()
          })
          .catch(() => {
            if (alive.current) setError('qlib_marketdata_status_unavailable')
          })
      },
      pending ? 4000 : 30000,
    )
    return () => window.clearInterval(timer)
  }, [pending, reload, onChange])
  const candidate = governance?.models.find(
    (model) =>
      model.state === 'candidate' && model.dataset_id === state?.dataset_id,
  )
  const act = async (operation: () => Promise<unknown>) => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await operation()
      await reload()
      await onChange()
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'qlib_marketdata_action_failed',
      )
    } finally {
      setBusy(false)
    }
  }
  const edit = () => {
    setPool(state?.source?.symbols.join(' ') ?? EXAMPLE_POOL)
    setVendor(state?.source?.vendor ?? 'auto')
    setLicense(state?.source?.license ?? '')
    setConsent(false)
    setAutomatic(state?.source?.automatic ?? false)
    setOpen(true)
  }
  const symbols = pool
    .trim()
    .split(/[\s,，;；]+/)
    .filter(Boolean)
    .map((s) => {
      const code = cnSymbol(s)
      return `${code.startsWith('6') ? 'SH' : 'SZ'}${code}`
    })
  const valid =
    symbols.length >= 3 &&
    symbols.length <= 20 &&
    new Set(symbols).size === symbols.length &&
    symbols.every((s) => /^(SH(600|601|603|605)|SZ(000|001|002))\d{3}$/.test(s))
  const submit = async (initialize: boolean) => {
    if (!valid || !consent || license.trim().length < 5) return
    if (
      initialize &&
      state?.dataset_id &&
      !(await confirmAction(tr('reinitializeConfirm')))
    )
      return
    await act(async () => {
      await qlibApi.configureDataSource({
        symbols,
        vendor,
        license: license.trim(),
        license_acknowledged: true,
        automatic,
      })
      if (initialize) await qlibApi.syncDataSource(requestKey(), true, true)
      setOpen(false)
    })
  }
  const failures: Record<string, string> = {
    qlib_research_source_disabled: 'sourceDisabled',
    qlib_marketdata_stale: 'stale',
    qlib_pool_quote_or_status_unavailable: 'poolUnavailable',
    qlib_pool_history_insufficient_or_stale: 'poolUnavailable',
    qlib_history_changed_reinitialize_required: 'historyChanged',
    qlib_source_changed_reinitialize_required: 'historyChanged',
    qlib_worker_unavailable: 'workerUnavailable',
    qlib_marketdata_fetch_failed: 'fetchFailed',
  }
  const failure =
    error ||
    state?.job?.error_code ||
    training?.error_code ||
    prediction?.error_code
  return (
    <section className="card mb-4 p-3" data-testid="qlib-data-pipeline">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">{tr('title')}</h2>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={edit}
            disabled={busy || pending}
          >
            {tr(state?.source ? 'configure' : 'start')}
          </Button>
          {state?.source && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy || pending}
              onClick={() =>
                void act(() => qlibApi.syncDataSource(requestKey()))
              }
            >
              {tr('sync')}
            </Button>
          )}
          {state?.dataset_id && !candidate && !pending && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() =>
                void act(() =>
                  qlibApi.train({
                    dataset_id: state.dataset_id!,
                    config: {
                      ...defaultQlibConfig,
                      topk: Math.min(10, state.source?.symbols.length ?? 10),
                    },
                    request_key: requestKey(),
                  }),
                )
              }
            >
              {tr('train')}
            </Button>
          )}
          {candidate && (
            <Button
              size="sm"
              disabled={busy || pending}
              onClick={() => {
                setReason('')
                setApproval(candidate)
              }}
            >
              {tr('review')}
            </Button>
          )}
          {active(state?.job?.status) && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  if (await confirmAction(tr('cancelConfirm')))
                    await qlibApi.cancelDataSync(state.job!.id)
                })
              }
            >
              {tr('cancel')}
            </Button>
          )}
        </div>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">{tr('flow')}</p>
      <p className="mt-2 text-xs" role="status">
        {pending
          ? tr('working', {
              completed: state?.job?.progress.completed ?? 0,
              total: state?.job?.progress.total ?? 0,
              phase: tr(
                `phases.${active(prediction?.status) ? 'prediction' : active(training?.status) ? 'training' : (state?.job?.progress.phase ?? 'queued')}`,
              ),
            })
          : candidate
            ? tr('candidateReady')
            : state?.dataset_id
              ? tr('dataReady')
              : tr('notConfigured')}
      </p>
      {state?.source && (
        <p className="mt-1 break-words text-xs text-muted-foreground">
          {tr('poolStatus', {
            count: state.source.symbols.length,
            vendor: state.job?.progress.vendor || state.source.vendor,
            automatic: tr(
              state.source.automatic && governance?.settings.enabled
                ? 'on'
                : 'off',
            ),
          })}
        </p>
      )}
      {failure && (
        <p role="alert" className="mt-2 break-words text-xs text-destructive">
          {tr(failures[failure] ?? 'failed')} <code>{failure}</code>
        </p>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>{tr('setupTitle')}</DialogTitle>
            <DialogDescription>{tr('limits')}</DialogDescription>
          </DialogHeader>
          <label className="space-y-1 text-sm">
            <span>{tr('pool')}</span>
            <textarea
              className={`${field} min-h-24`}
              aria-label={tr('pool')}
              value={pool}
              onChange={(e) => setPool(e.target.value)}
            />
          </label>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => setPool(EXAMPLE_POOL)}
            >
              {tr('examples')}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  const stocks = await stocksApi.list()
                  setPool(
                    stocks
                      .filter(
                        (s) =>
                          s.market.toUpperCase() === 'CN' &&
                          s.asset_type !== 'etf',
                      )
                      .map((s) => cnSymbol(s.symbol))
                      .filter((s) =>
                        /^(600|601|603|605|000|001|002)\d{3}$/.test(s),
                      )
                      .join(' '),
                  )
                })
              }
            >
              {tr('watchlist')}
            </Button>
            <span>{tr('poolCount', { count: symbols.length })}</span>
          </div>
          <label className="space-y-1 text-sm">
            <span>{tr('vendor')}</span>
            <Select
              value={vendor}
              onValueChange={(v) =>
                setVendor(v as QlibMarketDataSource['vendor'])
              }
            >
              <SelectTrigger aria-label={tr('vendor')}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">{tr('auto')}</SelectItem>
                {state?.vendors.map((v) => (
                  <SelectItem key={v} value={v}>
                    {v}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="space-y-1 text-sm">
            <span>{tr('license')}</span>
            <input
              className={field}
              maxLength={300}
              aria-label={tr('license')}
              value={license}
              onChange={(e) => setLicense(e.target.value)}
              placeholder={tr('licensePlaceholder')}
            />
          </label>
          <label className="flex items-start gap-2 text-xs">
            <input
              type="checkbox"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
            />
            <span>{tr('consent')}</span>
          </label>
          <label className="flex items-start gap-2 text-xs">
            <input
              type="checkbox"
              checked={automatic}
              onChange={(e) => setAutomatic(e.target.checked)}
            />
            <span>{tr('automatic')}</span>
          </label>
          {!valid && (
            <p className="text-xs text-destructive">{tr('invalidPool')}</p>
          )}
          {failure && (
            <p role="alert" className="break-words text-xs text-destructive">
              {tr(failures[failure] ?? 'failed')} <code>{failure}</code>
            </p>
          )}
          <div className="flex flex-wrap justify-end gap-2">
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => setOpen(false)}
            >
              {tr('close')}
            </Button>
            <Button
              variant="outline"
              disabled={busy || !valid || !consent || license.trim().length < 5}
              onClick={() => void submit(false)}
            >
              {tr('save')}
            </Button>
            <Button
              disabled={
                busy ||
                !valid ||
                !consent ||
                license.trim().length < 5 ||
                !state?.vendors.length
              }
              onClick={() => void submit(true)}
            >
              {tr('initialize')}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
      <Dialog
        open={!!approval}
        onOpenChange={(value) => {
          if (!value) setApproval(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{tr('review')}</DialogTitle>
            <DialogDescription>{tr('reviewNotice')}</DialogDescription>
          </DialogHeader>
          {approval && (
            <div className="space-y-2 text-sm">
              <p>
                {approval.name} · {approval.id.slice(0, 8)}
              </p>
              <p>
                {tr('metrics', {
                  ic: approval.metrics.rank_ic?.toFixed(3) ?? '—',
                  drawdown:
                    approval.metrics.backtest.max_drawdown == null
                      ? '—'
                      : `${(approval.metrics.backtest.max_drawdown * 100).toFixed(1)}%`,
                })}
              </p>
              <Link className="text-primary" to="/research?tab=governance">
                {tr('fullReport')}
              </Link>
            </div>
          )}
          <label className="space-y-1 text-sm">
            <span>{tr('reviewReason')}</span>
            <textarea
              className={field}
              maxLength={500}
              aria-label={tr('reviewReason')}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          {error && (
            <p className="break-words text-xs text-destructive" role="alert">
              {tr('failed')} <code>{error}</code>
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button
              variant="outline"
              onClick={() => setApproval(null)}
              disabled={busy}
            >
              {tr('close')}
            </Button>
            <Button
              disabled={busy || reason.trim().length < 5 || !governance}
              onClick={() =>
                void act(async () => {
                  await qlibApi.publish(
                    approval!.id,
                    governance!.revision,
                    reason.trim(),
                    'research',
                  )
                  setApproval(null)
                })
              }
            >
              {tr('approve')}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </section>
  )
}
