import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { qlibApi, type QlibPlanPreview } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@panwatch/base-ui/components/ui/dialog'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

export function planError(
  error: string,
  t: (key: string, options?: Record<string, unknown>) => string,
) {
  const key =
    error.match(
      /(?:qlib|paper_trading)_[a-z_]+|insufficient_cash_or_lot|legacy_position_not_managed|opening_quote_unavailable|quote_stale_or_missing|opening_price_limit/,
    )?.[0] ?? error
  return t('plan.errors.' + key, { defaultValue: error })
}
const money = (value: number | null | undefined) =>
  value == null
    ? '—'
    : value.toLocaleString(undefined, { maximumFractionDigits: 2 })

export default function PaperPlanDialog({
  runId,
  focusSymbol,
  open,
  onOpenChange,
  onConfirmed,
}: {
  runId: string | null
  focusSymbol?: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirmed: () => void
}) {
  const { t } = useTranslation('qlib')
  const qlibT = t as unknown as (
    key: string,
    values?: Record<string, unknown>,
  ) => string
  const tr = (key: string, values?: Record<string, unknown>) =>
    qlibT('plan.' + key, values)
  const { toast } = useToast()
  const [preview, setPreview] = useState<QlibPlanPreview | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [ack, setAck] = useState(false)
  const [version, setVersion] = useState(0)
  useEffect(() => {
    if (!open || !runId) return
    let current = true
    setLoading(true)
    setPreview(null)
    setAck(false)
    setError('')
    void qlibApi
      .previewPlan(runId)
      .then((value) => {
        if (current) setPreview(value)
      })
      .catch((cause) => {
        if (current)
          setError(String(cause instanceof Error ? cause.message : cause))
      })
      .finally(() => {
        if (current) setLoading(false)
      })
    return () => {
      current = false
    }
  }, [open, runId, version])
  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (!busy) onOpenChange(value)
      }}
    >
      <DialogContent className="max-w-3xl" aria-busy={loading || busy}>
        <DialogHeader>
          <DialogTitle>{tr('previewTitle')}</DialogTitle>
          <DialogDescription>{tr('previewNote')}</DialogDescription>
        </DialogHeader>
        {loading && <p role="status">{tr('loading')}</p>}
        {error && (
          <p role="alert" className="break-words text-sm text-destructive">
            {planError(error, qlibT)}
          </p>
        )}
        {preview && (
          <>
            <p className="text-sm">
              {preview.market} · {tr('dataAsOf', { date: preview.asof_date })} ·{' '}
              {tr('scope', {
                ranked: preview.ranked_count,
                pool: preview.pool_count,
              })}
            </p>
            {preview.blocking_reason && (
              <p
                role="status"
                className="rounded-lg border bg-muted p-3 text-sm"
              >
                {planError(preview.blocking_reason, qlibT)}
              </p>
            )}
            {preview.blocking_reason === 'qlib_paper_account_required' && (
              <Link
                className="text-sm text-primary underline"
                to="/paper-trading"
              >
                {tr('setupAccount')}
              </Link>
            )}
            {preview.existing_decision_id && (
              <Link
                className="text-sm text-primary underline"
                to="/paper-trading"
              >
                {tr('track')}
              </Link>
            )}
            {focusSymbol &&
              !preview.orders.some((o) => o.symbol === focusSymbol) && (
                <p className="text-xs text-muted-foreground">
                  {tr('notInOrders', { symbol: focusSymbol })}
                </p>
              )}
            {!!preview.orders.length && (
              <>
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div>
                    {tr('availableCash')}
                    <p className="font-medium">
                      {money(preview.available_cash)} {tr('currency')}
                    </p>
                  </div>
                  <div>
                    {tr('cashCeiling')}
                    <p className="font-medium">
                      {money(preview.cash_budget)} {tr('currency')}
                    </p>
                  </div>
                  <div>
                    {tr('estimatedFees')}
                    <p className="font-medium">
                      {money(preview.estimated_fees)} {tr('currency')}
                    </p>
                  </div>
                  <div>
                    {tr('executionDate')}
                    <p className="font-medium">
                      {preview.execution_date} · {preview.execution_window}
                    </p>
                  </div>
                </div>
                <div className="max-h-64 overflow-auto scrollbar rounded-lg border">
                  <table className="w-full text-left text-xs">
                    <thead className="sticky top-0 z-10 bg-card">
                      <tr>
                        {[
                          'stock',
                          'action',
                          'reference',
                          'quantity',
                          'fee',
                          'itemCashLimit',
                        ].map((k) => (
                          <th className="p-2" key={k}>
                            {tr(k)}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {preview.orders.map((order) => (
                        <tr key={order.symbol} className="border-t">
                          <td className="p-2">
                            <p>{order.name}</p>
                            <code>{order.symbol}</code>
                          </td>
                          <td className="p-2">{tr(order.side)}</td>
                          <td className="p-2">
                            {money(order.reference_price)}
                          </td>
                          <td className="p-2">{order.estimated_quantity}</td>
                          <td className="p-2">{money(order.estimated_fee)}</td>
                          <td className="p-2">{money(order.cash_limit)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="text-xs text-muted-foreground">
                  {tr('estimatesNote')}
                </p>
                <p className="rounded-lg border bg-muted p-3 text-xs">
                  {tr('exitNote')}
                </p>
                {!preview.account_enabled && (
                  <p role="status" className="text-sm">
                    {tr('pausedNote')}
                  </p>
                )}
                {preview.replaces_decision_id && (
                  <p className="text-xs">{tr('replaceNote')}</p>
                )}
                <label className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={ack}
                    onChange={(e) => setAck(e.target.checked)}
                    disabled={busy}
                  />
                  <span>{tr('ack')}</span>
                </label>
              </>
            )}
            {(preview.kept.length > 0 || preview.excluded.length > 0) && (
              <details className="text-xs rounded border p-2">
                <summary className="cursor-pointer">
                  {tr('keptAndExcluded', {
                    kept: preview.kept.length,
                    excluded: preview.excluded_count ?? preview.excluded.length,
                  })}
                </summary>
                <div className="max-h-32 overflow-auto scrollbar mt-2 space-y-2">
                  {preview.kept.map((item) => (
                    <p key={item.symbol}>
                      <code>{item.symbol}</code> · {tr('keep')} ·{' '}
                      {item.quantity}
                    </p>
                  ))}
                  {preview.excluded.map((item) => (
                    <p key={item.symbol}>
                      <code>{item.symbol}</code> ·{' '}
                      {planError(item.reason, qlibT)}
                    </p>
                  ))}
                </div>
                {(preview.excluded_count ?? 0) > preview.excluded.length && (
                  <p className="mt-2">
                    {tr('exclusionLimit', { count: preview.excluded.length })}
                  </p>
                )}
              </details>
            )}
          </>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <Button
            variant="outline"
            disabled={busy || loading}
            onClick={() => setVersion((v) => v + 1)}
          >
            {tr('repreview')}
          </Button>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => onOpenChange(false)}
          >
            {tr('close')}
          </Button>
          <Button
            disabled={
              !preview?.ready ||
              !preview.preview_hash ||
              !ack ||
              busy ||
              loading
            }
            onClick={async () => {
              if (!runId || !preview?.preview_hash) return
              setBusy(true)
              setError('')
              try {
                await qlibApi.confirmPlan(runId, preview.preview_hash, true)
                toast(tr('confirmed'), 'success')
                onOpenChange(false)
                onConfirmed()
              } catch (cause) {
                setError(String(cause instanceof Error ? cause.message : cause))
                setAck(false)
              } finally {
                setBusy(false)
              }
            }}
          >
            {busy ? tr('confirming') : tr('confirm')}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
