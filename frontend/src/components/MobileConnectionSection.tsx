import { useCallback, useEffect, useRef, useState } from 'react'
import { Copy, Link2, QrCode, Smartphone } from 'lucide-react'
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
type ConnectionCapabilities = { pairing: boolean; password_login?: boolean }

function active(state: PairingState) {
  return state === 'waiting_for_device' || state === 'waiting_for_approval' || state === 'approved'
}

export async function readMobileConnections(signal?: AbortSignal) {
  const { pairing, password_login } = await fetchAPI<ConnectionCapabilities>(`${api}/capabilities`, { signal })
  const devices = pairing ? await fetchAPI<Device[]>(`${api}/devices`, { signal }) : []
  return { pairing, password_login, devices }
}

export function MobileConnectionButton({ firstConnection, onClick }: {
  firstConnection: boolean
  onClick: () => void
}) {
  const { t } = useTranslation('configuration')
  const label = t(firstConnection ? 'mobile.title' : 'mobile.menuTitle')
  const Icon = firstConnection ? QrCode : Smartphone
  return (
    <Button type="button" variant="ghost" size="sm" onClick={onClick} aria-haspopup="dialog"
      aria-label={label} title={label}
      className={`h-8 shrink-0 rounded-xl md:h-9 ${firstConnection
        ? 'gap-1.5 bg-primary/10 px-2 text-primary hover:bg-primary/15 hover:text-primary'
        : 'w-8 px-0 text-muted-foreground hover:bg-accent hover:text-foreground md:w-9'}`}>
      <Icon aria-hidden="true" className="h-4 w-4 shrink-0" />
      {firstConnection && <span className="hidden whitespace-nowrap min-[520px]:inline">{label}</span>}
    </Button>
  )
}

export default function MobileConnectionSection({ onOpen }: { onOpen: () => void }) {
  const { t } = useTranslation('configuration')
  return (
    <section id="sec-mobile" className="card p-4 lg:col-span-12 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-1.5 text-sm font-semibold"><Smartphone aria-hidden="true" className="h-4 w-4" />{t('mobile.title')}</h3>
          <p className="mt-1 text-xs text-muted-foreground">{t('mobile.description')}</p>
        </div>
        <Button type="button" size="sm" onClick={onOpen} aria-haspopup="dialog">{t('mobile.manage')}</Button>
      </div>
    </section>
  )
}

export function MobileConnectionDialog({ open, onOpenChange, onFirstConnectionChange }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onFirstConnectionChange: (firstConnection: boolean) => void
}) {
  const { t } = useTranslation(['configuration', 'common'])
  const { toast } = useToast()
  const confirmAction = useConfirm()
  const [capabilities, setCapabilities] = useState<ConnectionCapabilities | null>(null)
  const [devices, setDevices] = useState<Device[]>([])
  const [qr, setQR] = useState<PairingQR | null>(null)
  const [review, setReview] = useState<PairingStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  const [reload, setReload] = useState(0)
  const heading = useRef<HTMLHeadingElement>(null)
  const attempted = useRef(false)
  const loadRevision = useRef(0)
  const https = window.location.protocol === 'https:'
  const origin = window.location.origin
  const state = review?.status ?? 'waiting_for_device'
  const pairingActive = qr !== null && active(state)
  const canCreate = https && capabilities?.pairing && !busy && !pairingActive
  const codeExpiry = qr && t('mobile.expiresAt', { time: formatDate(qr.expires_at * 1000, { timeStyle: 'short' }) })

  const load = useCallback(async (signal?: AbortSignal) => {
    const revision = ++loadRevision.current
    try {
      const { devices: items, ...available } = await readMobileConnections(signal)
      if (signal?.aborted || revision !== loadRevision.current) return false
      setCapabilities(available)
      setDevices(items)
      setError(false)
      const firstConnection = available.pairing && items.length === 0
      onFirstConnectionChange(firstConnection)
      return firstConnection
    } catch {
      if (!signal?.aborted && revision === loadRevision.current) setError(true)
      return false
    }
  }, [onFirstConnectionChange])

  const create = useCallback(async () => {
    setBusy(true)
    try {
      setQR(await fetchAPI<PairingQR>(`${api}/pairings`, {
        method: 'POST',
        body: JSON.stringify({ origin }),
      }))
      setReview(null)
    } catch {
      toast(t('mobile.actionFailed'), 'error')
    } finally {
      setBusy(false)
    }
  }, [origin, t, toast])

  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    void load(controller.signal).then(firstConnection => {
      if (firstConnection && https && !attempted.current && !controller.signal.aborted) {
        attempted.current = true
        void create()
      }
    })
    return () => controller.abort()
  }, [create, https, load, open, reload])

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
    if (busy) return
    if (qr && pairingActive) {
      void fetchAPI(`${api}/pairings/${qr.pairing_id}/decision`, {
        method: 'POST',
        body: JSON.stringify({ decision: 'cancel' }),
      }).catch(() => undefined)
    }
    attempted.current = false
    loadRevision.current += 1
    setQR(null)
    setReview(null)
    setCapabilities(null)
    setDevices([])
    setError(false)
    onOpenChange(false)
  }

  const copyAddress = async () => {
    try {
      await navigator.clipboard.writeText(origin)
      toast(t('common:states.copied'), 'success')
    } catch {
      toast(t('mobile.actionFailed'), 'error')
    }
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
    <Dialog open={open} onOpenChange={value => { if (!value) close() }}>
      <DialogContent className="max-w-[44rem] space-y-4 border border-border/60 p-4 sm:p-6" onOpenAutoFocus={event => {
        event.preventDefault()
        heading.current?.focus({ preventScroll: true })
      }}>
        <DialogHeader className="mb-0 flex items-center gap-3 pr-9">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Smartphone aria-hidden="true" className="h-5 w-5" />
          </span>
          <div className="min-w-0">
            <DialogTitle ref={heading} tabIndex={-1} className="text-lg focus:outline-none">{t('mobile.menuTitle')}</DialogTitle>
            <DialogDescription className="mt-0.5">{t('mobile.pairingDescription')}</DialogDescription>
          </div>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2">
          <section className="min-w-0 space-y-3 rounded-xl border border-border/70 bg-accent/20 p-3 sm:p-4">
            <h3 className="text-sm font-semibold">{t('mobile.scanTitle')}</h3>
            {!https && <p className="text-sm text-muted-foreground">{t('mobile.httpsRequired')}</p>}
            {https && capabilities?.pairing === false && <p className="text-sm text-muted-foreground">{t('mobile.loginRequired')}</p>}
            {https && !capabilities && !error && <p role="status" className="text-sm text-muted-foreground">{t('common:states.loading')}</p>}
            {busy && !pairingActive && <p role="status" className="text-sm text-muted-foreground">{t('mobile.creating')}</p>}
            {qr && state === 'waiting_for_device' && (
              <div className="space-y-2 text-center">
                <div className="mx-auto w-full max-w-48 overflow-hidden rounded-xl bg-white shadow-sm">
                  <QRCodeSVG className="block h-auto w-full" value={JSON.stringify(qr)} size={192} marginSize={4}
                    role="img" title={t('mobile.qrLabel')} />
                </div>
                <p className="text-sm">{t('mobile.scanInstruction')}</p>
                <div className="flex flex-wrap justify-center gap-x-2 text-xs text-muted-foreground">
                  <span role="status">{t('mobile.waiting_for_device')}</span>
                  <span>{codeExpiry}</span>
                </div>
              </div>
            )}
            {qr && state !== 'waiting_for_device' && (
              <p role="status" className="text-sm">{t(state === 'expired' ? 'mobile.qrExpired' : `mobile.${state}`)}</p>
            )}
            {state === 'approved' && <p className="text-xs text-muted-foreground">{codeExpiry}</p>}
            {canCreate && (
              <div className="space-y-3">
                {!qr && <p className="text-sm text-muted-foreground">{t('mobile.scanInstruction')}</p>}
                <Button type="button" size="sm" onClick={() => void create()}>
                  <QrCode aria-hidden="true" className="h-4 w-4" />
                  {t(qr && state !== 'redeemed' ? 'mobile.createAgain' : 'mobile.create')}
                </Button>
              </div>
            )}
            {state === 'waiting_for_approval' && review && (
              <div className="space-y-3 border-t border-border/70 pt-3">
                <div className="flex flex-wrap items-baseline justify-between gap-x-2">
                  <p className="min-w-0 break-words text-sm font-medium">{review.device_name}</p>
                  {review.platform && <span className="text-xs text-muted-foreground">{review.platform === 'ios' ? 'iOS' : 'Android'}</span>}
                </div>
                <div className="flex flex-wrap items-center justify-between gap-x-2 rounded-lg bg-primary/10 px-3 py-2">
                  <span className="text-xs font-medium">{t('mobile.verificationCode')}</span>
                  <span className="font-mono text-lg font-semibold tracking-widest text-primary">{review.verification_code}</span>
                </div>
                <div className="space-y-1 text-xs">
                  <p className="font-medium">{t('mobile.requestedAccess')}</p>
                  <ul className="list-disc space-y-1 pl-4 text-muted-foreground">
                    <li>{t(review.manage_alerts ? 'mobile.alertAccess' : review.renewable ? 'mobile.renewableAccess' : 'mobile.portfolioAccess')}</li>
                    {review.read_diagnostics && <li>{t('mobile.diagnosticsAccess')}</li>}
                    {review.manage_watchlist && <li>{t('mobile.watchlistAddAccess')}</li>}
                  </ul>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Button type="button" size="sm" disabled={busy} onClick={() => void decide('approve')}>{t('mobile.approve')}</Button>
                  <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void decide('deny')}>{t('mobile.deny')}</Button>
                  <span className="text-xs text-muted-foreground">{codeExpiry}</span>
                </div>
              </div>
            )}
          </section>
          <section className="flex min-w-0 flex-col gap-3 rounded-xl border border-border/70 p-3 sm:p-4">
            <h3 className="text-sm font-semibold">{t('mobile.devices')}</h3>
            {error && (
              <div className="space-y-2">
                <p role="status" className="text-sm text-muted-foreground">{t('mobile.deviceLoadFailed')}</p>
                <Button type="button" size="sm" variant="outline" onClick={() => setReload(value => value + 1)}>{t('mobile.retry')}</Button>
              </div>
            )}
            {capabilities && !devices.length && !error && (
              <div className="flex flex-1 items-center gap-2 text-muted-foreground sm:flex-col sm:justify-center sm:text-center">
                <Smartphone aria-hidden="true" className="h-4 w-4 shrink-0 sm:h-6 sm:w-6" />
                <p className="text-sm">{t('mobile.empty')}</p>
              </div>
            )}
            {!!devices.length && (
              <ul className="divide-y divide-border/70">
                {devices.map(device => {
                  const status = device.revoked_at ? 'revoked' : device.expires_at * 1000 <= Date.now() ? 'expired' : 'connected'
                  const connected = status === 'connected'
                  return (
                    <li key={device.id} className="flex items-start gap-2 py-2.5 first:pt-0 last:pb-0">
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent/70 text-muted-foreground">
                        <Smartphone aria-hidden="true" className="h-4 w-4" />
                      </span>
                      <div className="min-w-0 flex-1 space-y-0.5">
                        <p className="break-words text-sm font-medium">{device.name}</p>
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
                          <span>{device.platform === 'ios' ? 'iOS' : 'Android'}</span>
                          <span className={connected ? 'rounded-full bg-primary/10 px-1.5 text-primary' : undefined}>{t(`mobile.${status}`)}</span>
                        </div>
                        {connected && <p className="text-xs text-muted-foreground">
                          {t('mobile.expiresAt', { time: formatDate(device.expires_at * 1000, { dateStyle: 'medium' }) })}
                        </p>}
                      </div>
                      {connected && <Button type="button" size="sm" variant="ghost" className="shrink-0 px-2" onClick={() => void revoke(device)}>{t('mobile.revoke')}</Button>}
                    </li>
                  )
                })}
              </ul>
            )}
          </section>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/70 pt-3">
          <div className="flex min-w-0 flex-1 basis-56 items-start gap-2">
            <Link2 aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0">
              <p className="text-xs text-muted-foreground">{t('mobile.serviceAddress')}</p>
              <p className="select-all break-all text-sm font-medium">{origin}</p>
              {https && capabilities?.password_login && <p className="mt-0.5 text-xs text-muted-foreground">{t('mobile.passwordLoginHint')}</p>}
            </div>
          </div>
          <div className="ml-auto flex flex-wrap justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" onClick={() => void copyAddress()} aria-label={t('mobile.copyAddress')}>
              <Copy aria-hidden="true" className="h-3.5 w-3.5" />{t('common:actions.copy')}
            </Button>
            <Button type="button" variant="outline" size="sm" disabled={busy} onClick={close}>
              {pairingActive ? t('mobile.cancelledAction') : qr ? t('mobile.done') : t('common:actions.close')}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
