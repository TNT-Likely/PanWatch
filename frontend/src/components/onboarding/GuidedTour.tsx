import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useLocation, useNavigate } from 'react-router-dom'
import { ArrowLeft, ArrowRight, Check, Compass, Loader2, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { onboardingApi, type SetupStatus, type SetupStep, type SetupUpdate } from '@panwatch/api/onboarding'
import FeatureTour from './FeatureTour'
import { trackSteps, type FeatureTrack } from './tracks'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Popover, PopoverAnchor, PopoverContent } from '@panwatch/base-ui/components/ui/popover'

const paths: Record<SetupStep, string> = { stock: '/portfolio', quote: '/portfolio', ai: '/settings', analysis: '/portfolio', alert: '/alerts', notify: '/settings' }
export function tourSteps(): SetupStep[] {
  return ['stock', 'quote', 'ai', 'analysis', 'alert', 'notify']
}
export function resumeStep(status: SetupStatus): SetupStep {
  const steps = tourSteps()
  if (status.tour_step && status.tour_step !== 'done' && steps.includes(status.tour_step)) return status.tour_step
  return steps.find(key => status.steps.find(item => item.key === key)?.status === 'pending') || 'stock'
}
type TourContextValue = {
  status: SetupStatus | null
  active: boolean
  step: SetupStep | null
  busy: boolean
  error: string
  openWelcome: () => void
  refresh: () => Promise<void>
  selectStock: (id: number) => Promise<void>
  observeQuote: () => Promise<void>
  accept: (value: SetupStatus) => void
  startTrack: (track: FeatureTrack, restart?: boolean) => Promise<void>
  updateTrack: (patch: SetupUpdate) => Promise<SetupStatus | undefined>
}
const TourContext = createContext<TourContextValue | null>(null)
export function useGuidedTour() { return useContext(TourContext) }

function visible(selector: string): HTMLElement | null {
  return Array.from(document.querySelectorAll<HTMLElement>(selector)).find(element => {
    const rect = element.getBoundingClientRect()
    return rect.width > 0 && rect.height > 0 && getComputedStyle(element).visibility !== 'hidden'
  }) || null
}
type TourHint = 'stock' | 'stockForm' | 'stockChoose' | 'quote' | 'quoteRead' | 'ai' | 'providerForm' | 'modelForm' | 'analysis' | 'analysisForm' | 'analysisRead' | 'alert' | 'alertForm' | 'notify' | 'channelForm'
type Target = { element: HTMLElement; rect: DOMRect; dialog: HTMLElement | null; hint: TourHint }
function resolveTarget(step: SetupStep, status: SetupStatus, chooseExisting: boolean): { element: HTMLElement | null; hint: TourHint } {
  const pick = (selector: string, hint: TourHint) => ({ element: visible(selector), hint })
  if (step === 'stock') {
    if (visible('[data-tour="stock-form"]')) return pick('[data-tour="stock-form"]', 'stockForm')
    if (chooseExisting) return pick('[data-tour="watchlist"]', 'stockChoose')
    return pick('[data-tour="add-stock"]', 'stock')
  }
  if (step === 'quote') {
    if (!status.quote) return pick('[data-tour="refresh-quotes"]', 'quote')
    return pick('[data-tour-stock="' + status.selected_stock?.id + '"]', 'quoteRead')
  }
  if (step === 'ai') {
    if (visible('[data-tour="provider-form"]')) return pick('[data-tour="provider-form"]', 'providerForm')
    if (visible('[data-tour="model-form"]')) return pick('[data-tour="model-form"]', 'modelForm')
    if (visible('[data-tour="model-discovery"]')) return pick('[data-tour="model-discovery"]', 'modelForm')
    return pick(status.models.some(model => model.is_default) ? '[data-tour="test-default-model"]' : '#sec-ai', 'ai')
  }
  if (step === 'analysis') return visible('[data-tour="analysis-result"]')
    ? pick('[data-tour="analysis-result"]', 'analysisRead')
    : visible('[data-tour="analysis-form"]') ? pick('[data-tour="analysis-form"]', 'analysisForm')
      : pick('[data-tour="first-analysis"]', 'analysis')
  if (step === 'alert') return visible('[data-tour="alert-form"]')
    ? pick('[data-tour="alert-form"]', 'alertForm') : pick('[data-tour="add-alert"]', 'alert')
  if (visible('[data-tour="channel-form"]')) return pick('[data-tour="channel-form"]', 'channelForm')
  return pick(status.channels.some(channel => channel.enabled) ? '[data-tour="test-channel"]:not(:disabled)' : '#sec-notify', 'notify')
}

export function GuidedTourProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation('bizUi')
  const location = useLocation()
  const navigate = useNavigate()
  const [status, setStatus] = useState<SetupStatus | null>(null)
  const [welcome, setWelcome] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [chooseExisting, setChooseExisting] = useState(false)
  const [target, setTarget] = useState<Target | null>(null)
  const alive = useRef(true)
  const lock = useRef(false)
  const revision = useRef(0)
  const opener = useRef<HTMLElement | null>(null)
  const step = status?.tour_step && status.tour_step !== 'done' ? status.tour_step : null
  const mainTrack = !status?.active_track || status.active_track === 'main'
  const active = mainTrack && !!step && !status?.deferred && !welcome
  const onRoute = !!step && location.pathname === paths[step]

  const refresh = useCallback(async () => {
    const version = ++revision.current
    try {
      const value = await onboardingApi.status()
      if (alive.current && version === revision.current) { setStatus(previous => JSON.stringify(previous) === JSON.stringify(value) ? previous : value); setError('') }
    } catch (exc) {
      if (alive.current && version === revision.current) setError(exc instanceof Error ? exc.message : t('gettingStarted.errors.load'))
    }
  }, [t])
  useEffect(() => {
    alive.current = true
    void refresh()
    const onFocus = () => { if (!lock.current) void refresh() }
    window.addEventListener('focus', onFocus)
    return () => { alive.current = false; revision.current++; window.removeEventListener('focus', onFocus) }
  }, [refresh])
  useEffect(() => {
    if (!status || status.started || status.deferred) return
    setWelcome(true)
    navigate('/getting-started', { replace: true })
  }, [status, navigate])
  useEffect(() => {
    if (!active && status?.analysis?.status !== 'running') return
    const timer = window.setInterval(() => { if (!lock.current) void refresh() }, 3000)
    return () => window.clearInterval(timer)
  }, [active, status?.analysis?.status, refresh])

  const operation = async (action: () => Promise<SetupStatus>, route?: SetupStep | 'done') => {
    if (lock.current) return
    lock.current = true; revision.current++; setBusy(true); setError('')
    try {
      const value = await action()
      if (!alive.current) return
      setStatus(value)
      if (route) {
        setChooseExisting(false)
        setWelcome(false)
        navigate(route === 'done' ? '/getting-started' : paths[route])
      }
      return value
    } catch (exc) { if (alive.current) setError(exc instanceof Error ? exc.message : t('gettingStarted.errors.action')) }
    finally { lock.current = false; if (alive.current) setBusy(false) }
  }
  const move = (next: SetupStep | 'done', extra: SetupUpdate = {}) => operation(
    () => onboardingApi.update({ ...extra, track: 'main', tour_step: next, deferred: false }), next)
  const pause = () => operation(() => onboardingApi.update({ deferred: true })).then(() => {
    if (opener.current?.isConnected) opener.current.focus()
  })
  const openWelcome = useCallback(() => {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    setWelcome(true)
  }, [])
  const updateTrack = (patch: SetupUpdate) => operation(() => onboardingApi.update(patch))
  const startTrack = async (track: FeatureTrack, restart = false) => {
    const value = await updateTrack({ track, restart, deferred: false })
    if (!value) return
    setWelcome(false)
    const current = value.tracks.find(item => item.key === track)?.step
    navigate(trackSteps[track].find(item => item.key === current)?.path || trackSteps[track][0].path)
  }
  const selectStock = async (id: number) => {
    if (!active || step !== 'stock') return
    await move('quote', { stock_id: id })
  }
  const observeQuote = async () => {
    if (!active || step !== 'quote') return
    await operation(onboardingApi.quote)
  }

  // Targets appear after lazy routes, dialogs and responsive variants mount.
  // Re-measure while scrolling; only scroll automatically when a new target appears.
  useEffect(() => {
    if (!active || !onRoute || !step || !status) { setTarget(null); return }
    let frame = 0
    let last: HTMLElement | null = null
    const measure = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const resolved = resolveTarget(step, status, chooseExisting)
        const element = resolved.element
        if (!element) { last = null; setTarget(null); return }
        if (last !== element) {
          const rect = element.getBoundingClientRect()
          const top = 90, bottom = window.innerHeight - 100
          if (rect.top < top || rect.bottom > bottom) element.scrollIntoView({ block: 'center', behavior: 'instant' })
          if (!element.closest('[role="dialog"]') && element.tabIndex >= 0) element.focus({ preventScroll: true })
          last = element
        }
        const rect = element.getBoundingClientRect()
        setTarget(previous => previous?.element === element && previous.hint === resolved.hint &&
          previous.rect.x === rect.x && previous.rect.y === rect.y && previous.rect.width === rect.width && previous.rect.height === rect.height
          ? previous : { element, rect, hint: resolved.hint, dialog: element.closest<HTMLElement>('[role="dialog"]') })
      })
    }
    const observer = new MutationObserver(measure)
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'data-state', 'hidden'] })
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    window.visualViewport?.addEventListener('resize', measure)
    measure()
    return () => { observer.disconnect(); cancelAnimationFrame(frame); window.removeEventListener('resize', measure); window.removeEventListener('scroll', measure, true); window.visualViewport?.removeEventListener('resize', measure) }
  }, [active, onRoute, step, status, chooseExisting])
  useEffect(() => {
    if (!active) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      // Let a nested Select close itself before pausing the tour.
      if (visible('[role="listbox"]')) return
      event.preventDefault(); event.stopPropagation(); void pause()
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  })

  const sequence = tourSteps()
  const index = step ? sequence.indexOf(step) : 0
  const done = status?.steps.find(item => item.key === step)?.status === 'complete'
  const next: SetupStep | 'done' = index + 1 < sequence.length ? sequence[index + 1] : 'done'
  const virtualRef = useMemo(() => ({ current: { getBoundingClientRect: () => target?.rect || new DOMRect(), contextElement: target?.element } }), [target])
  const value: TourContextValue = { status, active, step, busy, error, openWelcome, refresh, selectStock, observeQuote, accept: setStatus, startTrack, updateTrack }
  const tooltip = active && onRoute && step && status && target && !target.dialog && <Popover open modal={false}>
    <PopoverAnchor virtualRef={virtualRef} />
    <PopoverContent side={target.rect.top > window.innerHeight / 2 ? 'top' : 'bottom'}
      sideOffset={12} collisionPadding={12} align="end"
      className="z-[71] w-[min(340px,calc(100vw-24px))] max-h-[min(360px,45dvh)] overflow-y-auto scrollbar pointer-events-auto shadow-xl"
      role="region" aria-label={t('tour.title')} aria-live="polite"
      onOpenAutoFocus={event => event.preventDefault()} onCloseAutoFocus={event => event.preventDefault()}
      onInteractOutside={event => event.preventDefault()} onEscapeKeyDown={event => event.preventDefault()}>
      <div className="mb-2 flex items-center justify-between gap-3">
        <p className="text-xs font-medium text-primary">{t('tour.progress', { current: index + 1, total: sequence.length })}{['alert', 'notify'].includes(step) && ' · ' + t('gettingStarted.optional')}</p>
        <Button size="icon" variant="ghost" className="h-7 w-7" aria-label={t('tour.pause')} disabled={busy} onClick={() => void pause()}><X className="h-4 w-4" /></Button>
      </div>
      <h2 className="font-semibold">{t(`tour.steps.${target.hint}.title`)}</h2>
      <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{t(`tour.steps.${target.hint}.description`)}</p>
      {step === 'quote' && status.quote && <p className="mt-2 text-xs text-muted-foreground">{t('gettingStarted.source', { value: status.quote.source })} · {status.quote.source_time || t('gettingStarted.unknown')}</p>}
      {done && <p className="mt-3 flex items-center gap-1.5 text-xs text-primary"><Check className="h-3.5 w-3.5" />{t('tour.verified')}</p>}
      {error && <p role="alert" className="mt-2 text-xs text-destructive">{error}</p>}
      {step === 'stock' && !target.dialog && !chooseExisting && <Button variant="link" className="mt-1 h-auto px-0 text-xs" onClick={() => setChooseExisting(true)}>{t('tour.chooseExisting')}</Button>}
      {step === 'stock' && chooseExisting && <Button variant="link" className="mt-1 h-auto px-0 text-xs" onClick={() => setChooseExisting(false)}>{t('gettingStarted.addStock')}</Button>}
      <div className="mt-4 flex items-center justify-between gap-2">
        <Button size="sm" variant="ghost" disabled={busy || index === 0 || !!target.dialog} onClick={() => void move(sequence[index - 1])}><ArrowLeft className="h-3.5 w-3.5" />{t('tour.back')}</Button>
        <div className="flex gap-1">
          {['alert', 'notify'].includes(step) && <Button size="sm" variant="ghost" disabled={busy || !!target.dialog} onClick={() => void move(next, { skip: step as 'alert' | 'notify' })}>{t('tour.skip')}</Button>}
          <Button size="sm" disabled={busy || !done || (!!target.dialog && step !== 'analysis')} onClick={() => void move(next)}>
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}{t(next === 'done' ? 'tour.finish' : 'tour.next')}<ArrowRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
      {!done && <p className="mt-2 text-[11px] text-muted-foreground">{t('tour.actOnPage')}</p>}
    </PopoverContent>
  </Popover>

  const instructionHost = target?.dialog?.querySelector('[data-tour-instructions]')
  return <TourContext.Provider value={value}>
    {children}
    <FeatureTour disabled={welcome} />
    {active && onRoute && target && !target.dialog && createPortal(<svg className="pointer-events-none fixed inset-0 z-[70] h-full w-full" aria-hidden="true">
      <defs><mask id="guided-tour-mask"><rect width="100%" height="100%" fill="white" /><rect x={target.rect.left - 6} y={target.rect.top - 6} width={target.rect.width + 12} height={target.rect.height + 12} rx="12" fill="black" /></mask></defs>
      <rect width="100%" height="100%" fill="rgba(0,0,0,.58)" mask="url(#guided-tour-mask)" />
      <rect x={target.rect.left - 6} y={target.rect.top - 6} width={target.rect.width + 12} height={target.rect.height + 12} rx="12" fill="none" stroke="hsl(var(--primary))" strokeWidth="2" />
    </svg>, document.body)}
    {tooltip}
    {active && onRoute && step && target?.dialog && instructionHost && createPortal(
      <div className="mb-4 rounded-xl border border-primary/25 bg-primary/5 p-3" role="region" aria-label={t('tour.title')} aria-live="polite">
        <div className="flex items-center justify-between gap-2"><p className="text-xs font-semibold text-primary">{t('tour.progress', { current: index + 1, total: sequence.length })} · {t(`tour.steps.${target.hint}.title`)}</p><Button size="icon" variant="ghost" className="h-7 w-7 shrink-0" aria-label={t('tour.pause')} disabled={busy} onClick={() => void pause()}><X className="h-4 w-4" /></Button></div>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{t(`tour.steps.${target.hint}.description`)}</p>
        {error && <p role="alert" className="mt-2 text-xs text-destructive">{error}</p>}
        {step === 'analysis' && done && <Button size="sm" className="mt-2" disabled={busy} onClick={() => void move(next)}>{t('tour.next')}<ArrowRight className="h-3.5 w-3.5" /></Button>}
      </div>, instructionHost)}
    {active && (!onRoute || !target) && createPortal(<div className="fixed bottom-20 right-3 z-[71] max-w-[calc(100vw-24px)] rounded-xl border bg-card p-3 shadow-lg">
      <p className="mb-2 text-sm" role="status">{t(onRoute ? 'tour.waiting' : 'tour.leftPage')}</p>
      {error && <p role="alert" className="mb-2 text-xs text-destructive">{error}</p>}
      <div className="flex gap-2"><Button size="sm" disabled={busy} onClick={() => { if (step) navigate(paths[step]); void refresh() }}>{t('tour.return')}</Button><Button size="sm" variant="ghost" disabled={busy} onClick={() => void pause()}>{t('tour.pause')}</Button></div>
    </div>, document.body)}
    <Dialog open={welcome} onOpenChange={open => { if (!busy) { if (!open && status && !status.started) void pause(); setWelcome(open) } }}>
      <DialogContent className="max-w-lg">
        <DialogHeader><DialogTitle className="flex items-center gap-2"><Compass className="h-5 w-5 text-primary" />{t('tour.title')}</DialogTitle><DialogDescription>{t('tour.welcome')}</DialogDescription></DialogHeader>
        <ol className="grid gap-3 rounded-xl border bg-muted/30 p-4 sm:grid-cols-2" aria-label={t('tour.flow')}>
          {sequence.map((item, order) => <li key={item} className="flex items-center gap-2 text-sm">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">{order + 1}</span>
            <span>{t(`gettingStarted.steps.${item}.title`)}{['alert', 'notify'].includes(item) && <span className="ml-1 text-xs text-muted-foreground">· {t('gettingStarted.optional')}</span>}</span>
          </li>)}
        </ol>
        {error && <p className="mt-3 text-sm text-destructive" role="alert">{error}</p>}
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <Button variant="ghost" disabled={busy} onClick={() => { void pause(); setWelcome(false) }}>{t('gettingStarted.later')}</Button>
          {status?.tour_step && status.tour_step !== 'done' && <Button variant="secondary" disabled={busy} onClick={() => void move(resumeStep(status))}>{t('gettingStarted.resume')}</Button>}
          <Button disabled={busy || !status} onClick={() => void move('stock')}>{busy && <Loader2 className="h-4 w-4 animate-spin" />}{t('tour.start')}</Button>
        </div>
      </DialogContent>
    </Dialog>
    <Dialog open={mainTrack && status?.tour_step === 'done' && !status.deferred} onOpenChange={open => { if (!open) void operation(() => onboardingApi.update({ deferred: true })) }}>
      <DialogContent>
        <DialogHeader><DialogTitle>{t('tour.doneTitle')}</DialogTitle><DialogDescription>{t('tour.doneDescription')}</DialogDescription></DialogHeader>
        <p className="text-sm text-muted-foreground">{t('gettingStarted.progress', { done: status?.completed_count || 0, total: status?.required_count || 0 })}</p>
        <Button className="mt-5" onClick={() => void operation(() => onboardingApi.update({ deferred: true }))}>{t('tour.doneAction')}</Button>
      </DialogContent>
    </Dialog>
  </TourContext.Provider>
}
