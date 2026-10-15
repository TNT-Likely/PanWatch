import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { qlibApi, type QlibAutomationStatus } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@panwatch/base-ui/components/ui/dialog'
import { useConfirm } from '@panwatch/base-ui/components/ui/confirm-dialog'
import { planError } from './paper-plan-dialog'
import { formatDateTime } from '@/lib/utils'

export default function PaperAutomation({
  onChange,
  onStatus,
}: {
  onChange?: () => void
  onStatus?: (status: QlibAutomationStatus) => void
}) {
  const { t } = useTranslation('qlib')
  const translate = t as unknown as (
    key: string,
    values?: Record<string, unknown>,
  ) => string
  const tr = (key: string, values?: Record<string, unknown>) =>
    translate('automation.' + key, values)
  const confirmAction = useConfirm()
  const [status, setStatus] = useState<QlibAutomationStatus | null>(null)
  const [error, setError] = useState('')
  const [open, setOpen] = useState(false)
  const [ack, setAck] = useState(false)
  const [cash, setCash] = useState('')
  const [requestKey, setRequestKey] = useState('')
  const [busy, setBusy] = useState(false)
  const reload = useCallback(async () => {
    try {
      const next = await qlibApi.automationStatus()
      setStatus(next)
      onStatus?.(next)
      setError('')
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause))
    }
  }, [onStatus])
  useEffect(() => {
    void reload()
    const timer = window.setInterval(() => void reload(), 20000)
    return () => window.clearInterval(timer)
  }, [reload])
  const update = async (
    body: Parameters<typeof qlibApi.configureAutomation>[0],
  ) => {
    setBusy(true)
    setError('')
    try {
      const next = await qlibApi.configureAutomation(body)
      setStatus(next)
      onStatus?.(next)
      setOpen(false)
      onChange?.()
    } catch (cause) {
      setError(String(cause instanceof Error ? cause.message : cause))
      setAck(false)
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="card mb-4 p-3 space-y-3" data-testid="paper-automation">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-medium text-sm">{tr('title')}</h2>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            disabled={busy || !status?.can_authorize}
            onClick={() => {
              setRequestKey(crypto.randomUUID())
              setCash(String(status?.recommended_cash_limit ?? 0))
              setAck(false)
              setError('')
              setOpen(true)
            }}
          >
            {tr(status?.authorized ? 'renew' : 'start')}
          </Button>
          {status?.mode !== 'manual' && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy || !status}
              onClick={async () => {
                if (await confirmAction(tr('manualConfirm')))
                  await update({ mode: 'manual' })
              }}
            >
              {tr('manual')}
            </Button>
          )}
        </div>
      </div>
      <p className="text-xs text-muted-foreground">{tr('flow')}</p>
      {error && (
        <p role="alert" className="text-sm text-destructive break-words">
          {planError(error, translate)}
        </p>
      )}
      {status && (
        <>
          <p className="text-sm" role="status">
            {tr('phases.' + status.phase)}
          </p>
          {status.reason && (
            <p className="text-sm">{planError(status.reason, translate)}</p>
          )}
          {status.authorized && (
            <p className="text-xs text-muted-foreground">
              {tr('bounds', {
                model: status.bound_model_id?.slice(0, 8),
                cap: status.max_buy_cash?.toLocaleString(),
                expires: formatDateTime(status.expires_at),
              })}
            </p>
          )}
          {!status.can_authorize && (
            <p className="text-xs">
              <Link className="text-primary underline" to="/research">
                {tr('setup')}
              </Link>{' '}
              ·{' '}
              <Link className="text-primary underline" to="/paper-trading">
                {tr('account')}
              </Link>
            </p>
          )}
          {status.authorized && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={async () => {
                if (await confirmAction(tr('stopConfirm')))
                  await update({ mode: 'automatic', enabled: false })
              }}
            >
              {tr('stop')}
            </Button>
          )}
        </>
      )}
      <Dialog
        open={open}
        onOpenChange={(value) => {
          if (!busy) setOpen(value)
        }}
      >
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>{tr('start')}</DialogTitle>
            <DialogDescription>{tr('consentNote')}</DialogDescription>
          </DialogHeader>
          <p className="text-sm break-words">
            {tr('model', {
              model: status?.model_name,
              id: status?.model_id?.slice(0, 8),
            })}
          </p>
          <label className="block text-sm space-y-1">
            <span>{tr('cashLimit')}</span>
            <input
              aria-label={tr('cashLimit')}
              type="number"
              min="1"
              max={status?.recommended_cash_limit}
              value={cash}
              onChange={(event) => setCash(event.target.value)}
              className="w-full rounded-md border bg-background p-2"
              disabled={busy}
            />
          </label>
          <p className="text-xs text-muted-foreground">{tr('risk')}</p>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              checked={ack}
              onChange={(event) => setAck(event.target.checked)}
              disabled={busy}
            />
            <span>{tr('ack')}</span>
          </label>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {planError(error, translate)}
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
              disabled={
                busy ||
                !ack ||
                !Number.isFinite(Number(cash)) ||
                Number(cash) <= 0 ||
                Number(cash) > (status?.recommended_cash_limit ?? 0)
              }
              onClick={() =>
                void update({
                  mode: 'automatic',
                  request_key: requestKey,
                  model_id: status?.model_id ?? undefined,
                  max_buy_cash: Number(cash),
                  days: 30,
                  acknowledge_research: true,
                })
              }
            >
              {tr('authorize')}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </section>
  )
}
