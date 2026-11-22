import { useCallback, useEffect, useState } from 'react'
import { Smartphone } from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import { useTranslation } from 'react-i18next'
import { fetchAPI } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { useConfirm } from '@panwatch/base-ui/components/ui/confirm-dialog'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { formatDate } from '@/i18n/format'

const api = '/mobile/v1'
type PairingQR = {
  kind: 'panwatch-pairing'
  protocol: 1
  pairing_id: string
  origin: string
  challenge: string
  expires_at: number
}
type PairingState = 'waiting_for_device' | 'waiting_for_approval' | 'approved' | 'denied' | 'cancelled' | 'redeemed' | 'expired'
type PairingStatus = {
  status: PairingState
  device_name: string | null
  platform: 'ios' | 'android' | null
  verification_code: string | null
  renewable?: boolean
  manage_alerts?: boolean
  read_diagnostics?: boolean
  manage_watchlist?: boolean
}
type Device = {
  id: string
  name: string
  platform: 'ios' | 'android'
  expires_at: number
  revoked_at: number | null
}

function active(state: PairingState) {
  return state === 'waiting_for_device' || state === 'waiting_for_approval' || state === 'approved'
}

export default function MobileConnectionSection() {
  const { t } = useTranslation('configuration')
  const { toast } = useToast()
  const confirmAction = useConfirm()
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [devices, setDevices] = useState<Device[]>([])
  const [qr, setQR] = useState<PairingQR | null>(null)
  const [review, setReview] = useState<PairingStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  const https = window.location.protocol === 'https:'
  const state = review?.status ?? 'waiting_for_device'

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const capabilities = await fetchAPI<{ pairing: boolean }>(`${api}/capabilities`, { signal })
      const items = capabilities.pairing ? await fetchAPI<Device[]>(`${api}/devices`, { signal }) : []
      setEnabled(capabilities.pairing)
      setDevices(items)
      setError(false)
    } catch {
      if (!signal?.aborted) setError(true)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [load])

  useEffect(() => {
    if (!qr || busy || !active(state)) return
    const controller = new AbortController()
    let timer: number
    let next = 1_200
    const poll = async () => {
      try {
        const status = await fetchAPI<PairingStatus>(`${api}/pairings/${qr.pairing_id}`, { signal: controller.signal })
        if (controller.signal.aborted) return
        setReview(status)
        if (status.status === 'redeemed') void load()
        if (!active(status.status)) return
        next = 1_200
      } catch {
        if (Date.now() >= qr.expires_at * 1000 && !controller.signal.aborted) {
          setReview({ status: 'expired', device_name: null, platform: null, verification_code: null })
          return
        }
        next = Math.min(5_000, next * 2)
      }
      if (!controller.signal.aborted) timer = window.setTimeout(poll, next)
    }
    void poll()
    return () => {
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [busy, load, qr, state])

  const create = async () => {
    setBusy(true)
    try {
      setQR(await fetchAPI<PairingQR>(`${api}/pairings`, {
        method: 'POST',
        body: JSON.stringify({ origin: window.location.origin }),
      }))
      setReview(null)
    } catch {
      toast(t('mobile.actionFailed'), 'error')
    } finally {
      setBusy(false)
    }
  }

  const decide = async (decision: 'approve' | 'deny' | 'cancel') => {
    if (!qr) return
    setBusy(true)
    try {
      setReview(await fetchAPI<PairingStatus>(`${api}/pairings/${qr.pairing_id}/decision`, {
        method: 'POST',
        body: JSON.stringify({ decision, verification_code: review?.verification_code }),
      }))
    } catch {
      toast(t('mobile.actionFailed'), 'error')
    } finally {
      setBusy(false)
    }
  }

  const close = () => {
    if (qr && active(state)) {
      void fetchAPI(`${api}/pairings/${qr.pairing_id}/decision`, {
        method: 'POST',
        body: JSON.stringify({ decision: 'cancel' }),
      }).catch(() => undefined)
    }
    setQR(null)
    setReview(null)
  }

  const revoke = async (device: Device) => {
    if (!await confirmAction(t('mobile.revokeConfirm', { name: device.name }), { destructive: true })) return
    try {
      const revoked = await fetchAPI<Device>(`${api}/devices/${device.id}`, { method: 'DELETE' })
      setDevices(items => items.map(item => item.id === revoked.id ? revoked : item))
    } catch {
      toast(t('mobile.actionFailed'), 'error')
    }
  }

  return (
    <section id="sec-mobile" className="card space-y-3 p-4 lg:col-span-12 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-1.5 text-sm font-semibold"><Smartphone className="h-4 w-4" />{t('mobile.title')}</h3>
          <p className="mt-1 text-xs text-muted-foreground">{t('mobile.description')}</p>
        </div>
        <Button size="sm" disabled={!https || !enabled || busy} onClick={create}>{t('mobile.create')}</Button>
      </div>
      {!https && <p className="text-xs text-muted-foreground">{t('mobile.httpsRequired')}</p>}
      {enabled === false && <p className="text-xs text-muted-foreground">{t('mobile.loginRequired')}</p>}
      {error && <Button size="sm" variant="outline" onClick={() => void load()}>{t('mobile.retry')}</Button>}
      {enabled && !devices.length && <p className="text-xs text-muted-foreground">{t('mobile.empty')}</p>}
      {devices.map(device => {
        const status = device.revoked_at ? 'revoked' : device.expires_at * 1000 <= Date.now() ? 'expired' : 'connected'
        return (
          <div key={device.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border px-3 py-2">
            <div className="min-w-0">
              <p className="break-words text-sm font-medium">{device.name}</p>
              <p className="text-xs text-muted-foreground">{t(`mobile.${status}`)} · {device.platform === 'ios' ? 'iOS' : 'Android'}</p>
            </div>
            {status === 'connected' && <Button size="sm" variant="outline" onClick={() => void revoke(device)}>{t('mobile.revoke')}</Button>}
          </div>
        )
      })}
      <Dialog open={qr !== null} onOpenChange={open => { if (!open && !busy) close() }}>
        <DialogContent className="scrollbar max-h-[85dvh] overflow-y-auto sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t('mobile.title')}</DialogTitle>
            <DialogDescription>{t('mobile.pairingDescription')}</DialogDescription>
          </DialogHeader>
          {qr && state === 'waiting_for_device' && (
            <div className="mx-auto rounded-lg bg-white">
              <QRCodeSVG value={JSON.stringify(qr)} size={232} marginSize={4} title={t('mobile.qrLabel')} />
            </div>
          )}
          <p aria-live="polite" className="text-sm">{t(`mobile.${state}`)}</p>
          {qr && active(state) && (
            <p className="break-all text-xs text-muted-foreground">
              {qr.origin} · {formatDate(qr.expires_at * 1000, { timeStyle: 'short' })}
            </p>
          )}
          {state === 'waiting_for_approval' && review && (
            <div className="space-y-2 rounded-lg border border-border p-3">
              <p className="break-words text-sm font-medium">{review.device_name}</p>
              <p className="text-xs text-muted-foreground">
                {t(review.manage_alerts ? 'mobile.alertAccess' : review.renewable ? 'mobile.renewableAccess' : 'mobile.portfolioAccess')}
              </p>
              {review.read_diagnostics && <p className="text-xs text-muted-foreground">{t('mobile.diagnosticsAccess')}</p>}
              {review.manage_watchlist && <p className="text-xs text-muted-foreground">{t('mobile.watchlistAddAccess')}</p>}
              <p className="font-mono text-lg tracking-widest">{review.verification_code}</p>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" disabled={busy} onClick={() => void decide('approve')}>{t('mobile.approve')}</Button>
                <Button size="sm" variant="outline" disabled={busy} onClick={() => void decide('deny')}>{t('mobile.deny')}</Button>
              </div>
            </div>
          )}
          <Button variant="outline" disabled={busy} onClick={close}>{active(state) ? t('mobile.cancelledAction') : t('mobile.done')}</Button>
        </DialogContent>
      </Dialog>
    </section>
  )
}
