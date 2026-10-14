import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { qlibApi, type QlibPaperPlan } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { useConfirm } from '@panwatch/base-ui/components/ui/confirm-dialog'
import { planError } from './paper-plan-dialog'

export default function PaperPlanStatus({
  compact = false,
  refreshKey = 0,
}: {
  compact?: boolean
  refreshKey?: number
}) {
  const { t } = useTranslation('qlib')
  const qlibT = t as unknown as (
    key: string,
    values?: Record<string, unknown>,
  ) => string
  const tr = (key: string, values?: Record<string, unknown>) =>
    qlibT('plan.' + key, values)
  const confirmAction = useConfirm()
  const [items, setItems] = useState<QlibPaperPlan[] | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const reload = useCallback(async () => {
    try {
      setItems((await qlibApi.paperPlans()).items)
      setError('')
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause))
    }
  }, [])
  useEffect(() => {
    void reload()
    const timer = window.setInterval(() => void reload(), 20000)
    return () => window.clearInterval(timer)
  }, [reload, refreshKey])
  return (
    <section className="card p-3 space-y-3" data-testid="paper-plan-status">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-medium text-sm">{tr('statusTitle')}</h2>
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => void reload()}
        >
          {tr('refreshStatus')}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{tr('statusNote')}</p>
      {error && (
        <p className="break-words text-xs text-destructive" role="alert">
          {planError(error, qlibT)}
        </p>
      )}
      {!items && !error && (
        <p role="status" className="text-xs">
          {tr('loading')}
        </p>
      )}
      {items?.length === 0 && <p className="text-sm">{tr('emptyPlans')}</p>}
      {(compact ? items?.slice(0, 1) : items)?.map((plan) => (
        <article key={plan.id} className="space-y-2 border-t pt-3 text-sm">
          <p className="font-medium">
            {tr('states.' + plan.status)} · {plan.execution_date} 09:30–09:35
          </p>
          <p className="text-xs text-muted-foreground">
            {tr('resultCount', {
              filled: plan.filled,
              rejected: plan.rejected,
            })}{' '}
            · {tr('sourceDate', { date: plan.asof_date })}
          </p>
          {!compact && (
            <div className="flex flex-wrap gap-2 text-xs">
              {plan.orders.map((o) => (
                <span className="rounded border px-2 py-1" key={o.symbol}>
                  {tr(o.side)} {o.name} ·{' '}
                  {tr('estimatedShares', { count: o.estimated_quantity })}
                </span>
              ))}
            </div>
          )}
          {!compact && !!plan.events.length && (
            <div className="max-h-48 overflow-auto scrollbar rounded border p-2 space-y-2 text-xs">
              {plan.events.map((e, index) => (
                <p key={index}>
                  <code>{e.symbol}</code> ·{' '}
                  {tr(
                    e.side === 'buy' || e.side === 'sell' ? e.side : 'filter',
                  )}{' '}
                  · {tr('eventStates.' + e.status)} · {e.quantity}{' '}
                  {e.reason && <>· {planError(e.reason, qlibT)}</>}
                </p>
              ))}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-3 text-xs">
            <Link
              className="text-primary underline"
              to={'/opportunities?market=CN&run=' + plan.run_id}
            >
              {tr('sourceOpportunity')}
            </Link>
            {compact && (
              <Link className="text-primary underline" to="/paper-trading">
                {tr('track')}
              </Link>
            )}
            {plan.can_cancel && (
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={async () => {
                  if (!(await confirmAction(tr('cancelNote')))) return
                  setBusy(true)
                  try {
                    await qlibApi.cancelPlan(plan.id)
                    await reload()
                  } catch (cause) {
                    setError(
                      String(cause instanceof Error ? cause.message : cause),
                    )
                  } finally {
                    setBusy(false)
                  }
                }}
              >
                {tr('cancel')}
              </Button>
            )}
          </div>
        </article>
      ))}
    </section>
  )
}
