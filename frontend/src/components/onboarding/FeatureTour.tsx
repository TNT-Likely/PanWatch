import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useLocation, useNavigate } from 'react-router-dom'
import { ArrowLeft, ArrowRight, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Popover, PopoverAnchor, PopoverContent } from '@panwatch/base-ui/components/ui/popover'
import type { SetupUpdate } from '@panwatch/api/onboarding'
import { useGuidedTour } from './GuidedTour'
import { trackSteps, type FeatureTrack } from './tracks'

function visible(selector: string) {
  return Array.from(document.querySelectorAll<HTMLElement>(selector)).find(element => {
    const rect = element.getBoundingClientRect()
    return rect.width > 0 && rect.height > 0 && getComputedStyle(element).visibility !== 'hidden'
  }) || null
}

/** These optional tours acknowledge exploration. Business actions remain in the native forms. */
export default function FeatureTour({ disabled = false }: { disabled?: boolean }) {
  const tour = useGuidedTour()
  const { t } = useTranslation('bizUi')
  const tr = t as unknown as (key: string) => string
  const location = useLocation()
  const navigate = useNavigate()
  const track = tour?.status?.active_track
  const feature = track && track !== 'main' ? track as FeatureTrack : null
  const progress = tour?.status?.tracks.find(item => item.key === feature)
  const sequence = feature ? trackSteps[feature] : []
  const index = sequence.findIndex(item => item.key === progress?.step)
  const step = sequence[index]
  const active = !disabled && !!step && !tour?.status?.deferred
  const onRoute = !!step && (location.pathname === step.path || (step.path === '/assistant' && location.pathname.startsWith('/assistant/')))
  const [target, setTarget] = useState<{ element: HTMLElement; rect: DOMRect; dialog: HTMLElement | null } | null>(null)

  useEffect(() => {
    if (!active || !onRoute || !step) { setTarget(null); return }
    let frame = 0
    let last: HTMLElement | null = null
    const measure = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const element = (step.dialog && visible(step.dialog)) || visible(step.selector) || (step.fallback && visible(step.fallback))
        if (!element) { setTarget(null); last = null; return }
        const rect = element.getBoundingClientRect()
        if (last !== element && (rect.top < 90 || rect.bottom > window.innerHeight - 100)) element.scrollIntoView({ block: 'center', behavior: 'instant' })
        last = element
        const next = element.getBoundingClientRect()
        setTarget(previous => previous?.element === element && previous.rect.x === next.x && previous.rect.y === next.y && previous.rect.width === next.width && previous.rect.height === next.height ? previous : { element, rect: next, dialog: element.closest<HTMLElement>('[role="dialog"]') })
      })
    }
    const observer = new MutationObserver(measure)
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'data-state', 'hidden'] })
    window.addEventListener('scroll', measure, true)
    window.addEventListener('resize', measure)
    measure()
    return () => { cancelAnimationFrame(frame); observer.disconnect(); window.removeEventListener('scroll', measure, true); window.removeEventListener('resize', measure) }
  }, [active, onRoute, step])
  useEffect(() => {
    if (!active) return
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || visible('[role="listbox"]') || visible('[role="dialog"]')) return
      event.preventDefault()
      void tour?.updateTrack({ deferred: true })
    }
    document.addEventListener('keydown', escape, true)
    return () => document.removeEventListener('keydown', escape, true)
  }, [active, tour])
  const anchor = useMemo(() => ({ current: { getBoundingClientRect: () => target?.rect || new DOMRect(), contextElement: target?.element } }), [target])
  if (!active || !feature || !step || !tour) return null
  const save = async (patch: SetupUpdate) => {
    const value = await tour.updateTrack(patch)
    if (!value) return
    const next = value.tracks.find(item => item.key === feature)?.step
    navigate(next === 'done' ? '/getting-started' : sequence.find(item => item.key === next)?.path || step.path)
  }
  const title = tr(`guideCenter.tracks.${feature}.steps.${step.key}.title`)
  const description = tr(`guideCenter.tracks.${feature}.steps.${step.key}.description`)
  const instruction = <>
    <div className="mb-2 flex items-center justify-between gap-2"><p className="text-xs font-medium text-primary">{t('tour.progress', { current: index + 1, total: sequence.length })} · {t(`guideCenter.tracks.${feature}.title`)}</p><Button size="icon" variant="ghost" className="h-7 w-7 shrink-0" aria-label={t('tour.pause')} disabled={tour.busy} onClick={() => void tour.updateTrack({ deferred: true })}><X className="h-4 w-4" /></Button></div>
    <h2 className="font-semibold">{title}</h2><p className="mt-2 text-sm leading-relaxed text-muted-foreground">{description}</p>
    {tour.error && <p role="alert" className="mt-2 text-xs text-destructive">{tour.error}</p>}
    {target?.dialog ? <p className="mt-2 text-xs text-muted-foreground">{t('guideCenter.closeDialog')}</p> : <>
      <p className="mt-2 text-xs text-muted-foreground">{t('guideCenter.explorationHint')}</p>
      <div className="mt-4 flex justify-between gap-2"><Button variant="ghost" size="sm" disabled={tour.busy || index === 0} onClick={() => void save({ track_step: sequence[index - 1].key })}><ArrowLeft className="h-3.5 w-3.5" />{t('tour.back')}</Button><Button size="sm" disabled={tour.busy} onClick={() => void save({ complete_step: step.key })}>{t(index === sequence.length - 1 ? 'guideCenter.finishTrack' : 'guideCenter.understood')}<ArrowRight className="h-3.5 w-3.5" /></Button></div>
    </>}
  </>
  const host = target?.dialog?.querySelector('[data-tour-instructions]')
  return <>
    {onRoute && target && !target.dialog && createPortal(<svg className="pointer-events-none fixed inset-0 z-[70] h-full w-full" aria-hidden="true"><defs><mask id="feature-tour-mask"><rect width="100%" height="100%" fill="white" /><rect x={target.rect.left - 6} y={target.rect.top - 6} width={target.rect.width + 12} height={target.rect.height + 12} rx="12" fill="black" /></mask></defs><rect width="100%" height="100%" fill="rgba(0,0,0,.58)" mask="url(#feature-tour-mask)" /><rect x={target.rect.left - 6} y={target.rect.top - 6} width={target.rect.width + 12} height={target.rect.height + 12} rx="12" fill="none" stroke="hsl(var(--primary))" strokeWidth="2" /></svg>, document.body)}
    {onRoute && target && !target.dialog && <Popover open modal={false}><PopoverAnchor virtualRef={anchor} /><PopoverContent side={target.rect.top > window.innerHeight / 2 ? 'top' : 'bottom'} align="end" sideOffset={12} collisionPadding={12} className="z-[71] w-[min(360px,calc(100vw-24px))] max-h-[45dvh] overflow-y-auto scrollbar pointer-events-auto shadow-xl" role="region" aria-label={t('tour.title')} onOpenAutoFocus={event => event.preventDefault()} onCloseAutoFocus={event => event.preventDefault()} onInteractOutside={event => event.preventDefault()}>{instruction}</PopoverContent></Popover>}
    {onRoute && host && createPortal(<div className="mb-4 rounded-xl border border-primary/25 bg-primary/5 p-3" role="region" aria-label={t('tour.title')}>{instruction}</div>, host)}
    {(!onRoute || !target) && createPortal(<div className="fixed bottom-20 right-3 z-[71] max-w-[calc(100vw-24px)] rounded-xl border bg-card p-3 shadow-lg"><p className="mb-2 text-sm">{t(onRoute ? 'tour.waiting' : 'tour.leftPage')}</p><div className="flex gap-2"><Button size="sm" onClick={() => navigate(step.path)}>{t('tour.return')}</Button><Button size="sm" variant="ghost" disabled={tour.busy} onClick={() => void tour.updateTrack({ deferred: true })}>{t('tour.pause')}</Button></div></div>, document.body)}
  </>
}
