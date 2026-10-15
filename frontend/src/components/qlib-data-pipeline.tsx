import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  qlibApi,
  type QlibUniverseStatus,
  type QlibUniverseMember,
  type QlibGovernance,
  type QlibModel,
  type QlibJob,
  type QlibMarket,
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

const active = (status?: string) => status === 'queued' || status === 'running'
const field = 'w-full rounded-md border bg-background px-3 py-2 text-sm'
export default function QlibDataPipeline({
  onChange,
  market = 'CN',
}: {
  onChange: () => Promise<void>
  market?: QlibMarket
}) {
  const { t } = useTranslation('qlib')
  const tr = (key: string, values?: Record<string, unknown>) =>
    (t as unknown as (k: string, v?: Record<string, unknown>) => string)(
      'universe.' + key,
      values,
    )
  const confirmAction = useConfirm()
  const issue = (code: string) => {
    const text = tr('issues.' + code)
    return text === 'universe.issues.' + code ? code : text
  }
  const [state, setState] = useState<QlibUniverseStatus | null>(null)
  const [governance, setGovernance] = useState<QlibGovernance | null>(null)
  const [jobs, setJobs] = useState<QlibJob[]>([])
  const [open, setOpen] = useState(false),
    [license, setLicense] = useState(''),
    [consent, setConsent] = useState(false),
    [automatic, setAutomatic] = useState(false),
    [budget, setBudget] = useState(128)
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [approval, setApproval] = useState<QlibModel | null>(null),
    [reason, setReason] = useState('')
  const [poolOpen, setPoolOpen] = useState(false),
    [page, setPage] = useState(1),
    [search, setSearch] = useState(''),
    [coverage, setCoverage] = useState('')
  const [members, setMembers] = useState<{
    total: number
    items: QlibUniverseMember[]
  } | null>(null)
  const alive = useRef(true)
  const reload = useCallback(async () => {
    const [data, g, j] = await Promise.all([
      qlibApi.universe(),
      qlibApi.governance(),
      qlibApi.jobs(),
    ])
    if (!alive.current) return
    setState(data)
    setGovernance(g)
    setJobs(j.items)
  }, [])
  useEffect(() => {
    alive.current = true
    void reload().catch(() => setError('qlib_universe_status_unavailable'))
    return () => {
      alive.current = false
    }
  }, [reload])
  const anyWorking = state?.jobs.some((j) => active(j.status))
  const working = state?.jobs.find(
      (j) => active(j.status) && (j.market === market || j.market == null),
    ),
    training = jobs.find(
      (j) =>
        j.kind === 'train' && active(j.status) && (j.market ?? 'CN') === market,
    ),
    pending = !!working || !!training
  useEffect(() => {
    const timer = window.setInterval(
      () => {
        void reload()
          .then(() => {
            if (alive.current) return onChange()
          })
          .catch(() => {
            if (alive.current) setError('qlib_universe_status_unavailable')
          })
      },
      pending ? 4000 : 30000,
    )
    return () => window.clearInterval(timer)
  }, [reload, pending, onChange])
  useEffect(() => {
    if (!poolOpen) return
    let current = true
    setMembers(null)
    const timer = window.setTimeout(() => {
      void qlibApi
        .universeMembers(market, page, search, coverage)
        .then((r) => {
          if (current) setMembers(r)
        })
        .catch(() => {
          if (current) setError('qlib_universe_members_unavailable')
        })
    }, 200)
    return () => {
      current = false
      window.clearTimeout(timer)
    }
  }, [poolOpen, market, page, search, coverage])
  const data = state?.markets[market],
    candidate = governance?.models.find(
      (m) =>
        m.state === 'candidate' &&
        (m.market ?? 'CN') === market &&
        m.universe_snapshot_id,
    )
  const latest = state?.jobs.find(
    (j) => j.market === market || j.kind === 'catalog',
  )
  const relatedModelJob = jobs.find((j) => j.id === latest?.result_id)
  const latestHistory = state?.jobs.find(
    (j) => j.kind === 'history' && j.market === market,
  )
  const resumable = latestHistory?.resumable
    ? latestHistory
    : latest?.resumable
      ? latest
      : null
  const statusError =
    error ||
    latest?.error_code ||
    relatedModelJob?.error_code ||
    latestHistory?.error_code
  const errorCode = statusError?.match(/qlib_[a-z_]+/)?.[0] ?? statusError
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
        cause instanceof Error ? cause.message : 'qlib_universe_action_failed',
      )
    } finally {
      setBusy(false)
    }
  }
  const edit = () => {
    setLicense(state?.config?.license ?? '')
    setBudget(state?.config?.training_stocks ?? 128)
    setAutomatic(state?.config?.automatic ?? false)
    setConsent(false)
    setOpen(true)
  }
  return (
    <section className="card mb-4 p-3" data-testid="qlib-data-pipeline">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">{tr('title')}</h2>
        <div className="flex flex-wrap gap-2">
          {!state?.config && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy || anyWorking}
              onClick={edit}
            >
              {tr('start')}
            </Button>
          )}
          {!!data?.total && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setPage(1)
                setSearch('')
                setCoverage('')
                setPoolOpen(true)
              }}
            >
              {tr('browse')}
            </Button>
          )}
          {!pending && resumable && (
            <Button
              size="sm"
              disabled={busy}
              onClick={() =>
                void act(() =>
                  qlibApi.universeJobAction(resumable.id, 'resume'),
                )
              }
            >
              {tr('resume')}
            </Button>
          )}
        </div>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">{tr('dailyHelp')}</p>
      <details className="mt-3" data-testid="research-maintenance">
        <summary className="cursor-pointer text-xs text-primary">
          {tr('maintenance')}
        </summary>
        <p className="mt-2 text-xs text-muted-foreground">
          {tr('maintenanceNote')}
        </p>
        <div className="flex flex-wrap gap-2">
          {state?.config && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy || anyWorking}
              onClick={edit}
            >
              {tr('configure')}
            </Button>
          )}
          {state?.config && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy || anyWorking}
              onClick={() => void act(() => qlibApi.universeJob('catalog'))}
            >
              {tr('catalog')}
            </Button>
          )}
          {!!data?.total && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy || pending || !data.vendors.length}
              onClick={() =>
                void act(() => qlibApi.universeJob('history', market))
              }
            >
              {tr('history')}
            </Button>
          )}
          {(data?.counts.ready ?? 0) + (data?.counts.stale ?? 0) >= 3 && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy || pending}
              onClick={() =>
                void act(() => qlibApi.universeJob('train', market))
              }
            >
              {tr('train')}
            </Button>
          )}
          {!!data?.total && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy || pending || !data.vendors.length}
              onClick={() =>
                void act(async () => {
                  if (await confirmAction(tr('rebuildConfirm')))
                    await qlibApi.universeJob(
                      'history',
                      market,
                      undefined,
                      true,
                    )
                })
              }
            >
              {tr('rebuild')}
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
          {data?.champion_id &&
            (data.counts.ready ?? 0) + (data.counts.stale ?? 0) >= 3 && (
              <Button
                size="sm"
                disabled={busy || pending}
                onClick={() =>
                  void act(() => qlibApi.universeJob('predict', market))
                }
              >
                {tr('predict')}
              </Button>
            )}
          {(working || training) && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  if (await confirmAction(tr('cancelConfirm')))
                    if (working)
                      await qlibApi.universeJobAction(working.id, 'cancel')
                    else if (training) await qlibApi.cancel(training.id)
                })
              }
            >
              {tr('cancel')}
            </Button>
          )}
        </div>
      </details>
      <p className="mt-2 text-xs text-muted-foreground">{tr('flow')}</p>
      <div
        className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4"
        data-testid="universe-coverage"
      >
        {(['total', 'ready', 'predicted', 'excluded'] as const).map((key) => (
          <div key={key} className="rounded-md bg-muted/40 p-2">
            <p className="text-[11px] text-muted-foreground">{tr(key)}</p>
            <p className="mt-1 font-semibold">
              {key === 'total'
                ? (data?.total ?? '—')
                : key === 'predicted'
                  ? (data?.predicted ?? '—')
                  : data
                    ? (data.counts[key] ?? 0)
                    : '—'}
            </p>
          </div>
        ))}
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        {tr('coverageNote', {
          pending: data?.counts.pending ?? 0,
          stale: data?.counts.stale ?? 0,
          date: data?.completed_day ?? '—',
        })}
      </p>
      {data && (
        <div
          className="mt-2 space-y-1 text-xs text-muted-foreground"
          data-testid="coverage-diagnostics"
        >
          <p>
            {tr('dateSeparation', {
              latest: data.completed_day,
              prediction: data.prediction_date ?? '—',
              predicted: data.predicted,
            })}
          </p>
          {Object.entries(data.reasons)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 5)
            .map(([code, count]) => (
              <p key={code}>
                {issue(code)} · {tr('affected', { count })}
              </p>
            ))}
        </div>
      )}
      {data?.catalog && (
        <p className="mt-1 break-words text-[11px] text-muted-foreground">
          {data.catalog.source} · {data.catalog.source_date} ·{' '}
          {tr('currentOnly')}
        </p>
      )}
      {state?.config && (
        <p className="mt-1 text-xs text-muted-foreground">
          {tr('budgetNote', { count: state.config.training_stocks })} ·{' '}
          {tr(state.config.automatic ? 'autoOn' : 'autoOff')}
        </p>
      )}
      {data && !data.vendors.length && (
        <p className="mt-2 text-xs" role="status">
          {tr('sourceDisabled', { market })}{' '}
          <Link className="text-primary" to="/datasources">
            {tr('sources')}
          </Link>
        </p>
      )}
      {(working || training) && (
        <p className="mt-2 text-xs" role="status">
          {working
            ? tr('working', {
                phase: tr('phases.' + working.progress.phase),
                completed: working.progress.completed,
                total: working.progress.total,
                market: working.progress.market ?? working.market ?? '',
              })
            : tr('training')}
        </p>
      )}
      {statusError && (
        <div className="mt-2 break-words text-xs text-destructive" role="alert">
          <p>
            {tr('failed')} {issue(errorCode ?? statusError)}
          </p>
          <details className="mt-1 text-muted-foreground">
            <summary className="cursor-pointer">
              {tr('technicalDetails')}
            </summary>
            <code>{statusError}</code>
          </details>
        </div>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>{tr('setup')}</DialogTitle>
            <DialogDescription>{tr('scope')}</DialogDescription>
          </DialogHeader>
          <p className="text-xs text-muted-foreground">{tr('limitations')}</p>
          <label className="space-y-1 text-sm">
            <span>{tr('budget')}</span>
            <input
              type="number"
              className={field}
              min={3}
              max={256}
              aria-label={tr('budget')}
              value={budget}
              onChange={(e) => setBudget(Number(e.target.value))}
            />
          </label>
          <p className="text-xs text-muted-foreground">{tr('budgetHelp')}</p>
          <label className="space-y-1 text-sm">
            <span>{tr('license')}</span>
            <input
              className={field}
              maxLength={300}
              aria-label={tr('license')}
              value={license}
              onChange={(e) => setLicense(e.target.value)}
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
          {error && (
            <p className="break-words text-xs text-destructive" role="alert">
              {error}
            </p>
          )}
          <div className="flex flex-wrap justify-end gap-2">
            <Button
              variant="outline"
              onClick={() => setOpen(false)}
              disabled={busy}
            >
              {tr('close')}
            </Button>
            <Button
              disabled={
                busy ||
                !consent ||
                license.trim().length < 5 ||
                !Number.isInteger(budget) ||
                budget < 3 ||
                budget > 256
              }
              onClick={() =>
                void act(async () => {
                  await qlibApi.configureUniverse({
                    markets: ['CN', 'HK', 'US'],
                    license: license.trim(),
                    license_acknowledged: true,
                    automatic,
                    training_stocks: budget,
                  })
                  await qlibApi.universeJob('catalog')
                  setOpen(false)
                })
              }
            >
              {tr('save')}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
      <Dialog
        open={!!approval}
        onOpenChange={(v) => {
          if (!v) setApproval(null)
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
                {market} · {approval.name} · {approval.id.slice(0, 8)}
              </p>
              <p>
                {tr('metrics', {
                  ic: approval.metrics.rank_ic?.toFixed(3) ?? '—',
                  drawdown:
                    approval.metrics.backtest.max_drawdown == null
                      ? '—'
                      : (approval.metrics.backtest.max_drawdown * 100).toFixed(
                          1,
                        ) + '%',
                })}
              </p>
              <Link className="text-primary" to="/research?tab=governance">
                {tr('fullReport')}
              </Link>
            </div>
          )}
          <label className="space-y-1 text-sm">
            <span>{tr('reason')}</span>
            <textarea
              className={field}
              maxLength={500}
              aria-label={tr('reason')}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          {error && (
            <p className="text-xs text-destructive" role="alert">
              {error}
            </p>
          )}
          <div className="flex flex-wrap justify-end gap-2">
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
                  await qlibApi.universeJob('predict', market)
                  setApproval(null)
                })
              }
            >
              {tr('approve')}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
      <Dialog open={poolOpen} onOpenChange={setPoolOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>
              {market} · {tr('browse')}
            </DialogTitle>
            <DialogDescription>{tr('memberNote')}</DialogDescription>
          </DialogHeader>
          <div className="flex min-w-0 flex-wrap gap-2">
            <input
              className={field + ' min-w-0 flex-1'}
              aria-label={tr('search')}
              placeholder={tr('search')}
              value={search}
              onChange={(e) => {
                setSearch(e.target.value)
                setPage(1)
              }}
            />
            <Select
              value={coverage || 'all'}
              onValueChange={(v) => {
                setCoverage(v === 'all' ? '' : v)
                setPage(1)
              }}
            >
              <SelectTrigger className="w-36" aria-label={tr('coverageFilter')}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {['all', 'pending', 'ready', 'stale', 'excluded'].map((s) => (
                  <SelectItem key={s} value={s}>
                    {tr(s)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="scrollbar max-h-[50vh] min-w-0 overflow-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 z-10 bg-background">
                <tr>
                  {[
                    'security',
                    'board',
                    'currency',
                    'state',
                    'exclusionReason',
                  ].map((k) => (
                    <th key={k} className="p-2 text-left">
                      {tr(k)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {members?.items.map((m) => (
                  <tr key={m.symbol} className="border-t">
                    <td className="p-2">
                      <p className="max-w-64 break-words">{m.name}</p>
                      <code>{m.code}</code>
                    </td>
                    <td className="p-2">{m.board}</td>
                    <td className="p-2">{m.currency}</td>
                    <td className="p-2">{tr(m.status)}</td>
                    <td className="max-w-64 break-words p-2">
                      {m.error_code
                        ? issue(m.error_code)
                        : Object.values(m.quality_warnings ?? {}).some(Boolean)
                          ? tr('qualityWarning', {
                              count: Object.values(
                                m.quality_warnings ?? {},
                              ).reduce((sum, n) => sum + n, 0),
                            })
                          : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!members && (
              <p role="status" className="p-3 text-xs">
                {tr('loading')}
              </p>
            )}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <span>{tr('page', { page, total: members?.total ?? '—' })}</span>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={page <= 1 || !members}
                onClick={() => setPage((p) => p - 1)}
              >
                {tr('previous')}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={!members || page * 100 >= members.total}
                onClick={() => setPage((p) => p + 1)}
              >
                {tr('next')}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </section>
  )
}
