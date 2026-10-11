import { Compass, ArrowRight, Check, Sparkles, Activity, MessageCircle, Bot, Database } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { useGuidedTour } from '@/components/onboarding/GuidedTour'
import { featureTracks } from '@/components/onboarding/tracks'

const icons = { opportunities: Sparkles, paper: Activity, assistant: MessageCircle, automation: Bot, datasources: Database }

export default function GettingStartedPage() {
  const { t } = useTranslation('bizUi')
  const tour = useGuidedTour()
  const status = tour?.status
  return <div className="mx-auto max-w-5xl space-y-6 py-4 md:py-6">
    <header><h1 className="flex items-center gap-2 text-2xl font-semibold"><Compass className="h-6 w-6 text-primary" />{t('guideCenter.title')}</h1><p className="mt-2 text-sm leading-relaxed text-muted-foreground">{t('guideCenter.description')}</p></header>
    {tour?.error && <div role="alert" className="rounded-xl border border-destructive/25 p-3 text-sm text-destructive">{tour.error}<Button variant="ghost" size="sm" className="ml-2" onClick={() => void tour.refresh()}>{t('gettingStarted.retry')}</Button></div>}
    <section className="card border-primary/25 bg-primary/5 p-5 md:p-6" aria-label={t('guideCenter.mainTitle')}>
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div><p className="mb-2 text-xs font-medium text-primary">{t('guideCenter.mainBadge')}</p><h2 className="text-lg font-semibold">{t('guideCenter.mainTitle')}</h2><p className="mt-2 text-sm leading-relaxed text-muted-foreground">{t('guideCenter.mainDescription')}</p><p className="mt-3 text-xs text-muted-foreground">{status ? t('gettingStarted.progress', { done: status.completed_count, total: status.required_count }) : t('gettingStarted.loading')}</p></div>
        <Button disabled={tour?.busy || !status} onClick={tour?.openWelcome}>{t(status?.completed ? 'guideCenter.replay' : status?.started ? 'gettingStarted.resume' : 'tour.start')}<ArrowRight className="h-4 w-4" /></Button>
      </div>
      <ol className="mt-4 flex flex-wrap gap-2" aria-label={t('tour.flow')}>{(['stock', 'quote', 'ai', 'analysis', 'alert', 'notify'] as const).map(key => <li key={key} className="flex items-center gap-1.5 rounded-full border bg-card px-3 py-1.5 text-xs">{status?.steps.find(item => item.key === key)?.status === 'complete' && <Check className="h-3 w-3 text-primary" />}{t(`gettingStarted.steps.${key}.title`)}{['alert', 'notify'].includes(key) && <span className="text-muted-foreground">· {t('gettingStarted.optional')}</span>}</li>)}</ol>
    </section>
    <div><h2 className="mb-1 text-lg font-semibold">{t('guideCenter.moreTitle')}</h2><p className="text-sm text-muted-foreground">{t('guideCenter.moreDescription')}</p></div>
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {featureTracks.map(key => {
        const progress = status?.tracks.find(item => item.key === key)
        const finished = progress?.completed && progress.step === 'done'
        const Icon = icons[key]
        return <section key={key} className="card flex flex-col p-5" aria-label={t(`guideCenter.tracks.${key}.title`)}>
          <div className="mb-3 flex items-center justify-between gap-2"><span className="rounded-xl bg-primary/10 p-2.5 text-primary"><Icon className="h-5 w-5" /></span><span className="text-xs text-muted-foreground">{t(finished ? 'gettingStarted.completed' : progress?.started ? 'guideCenter.inProgress' : 'guideCenter.notStarted')}</span></div>
          <h3 className="font-semibold">{t(`guideCenter.tracks.${key}.title`)}</h3><p className="mt-2 flex-1 text-sm leading-relaxed text-muted-foreground">{t(`guideCenter.tracks.${key}.description`)}</p>
          <div className="mt-5 flex items-center justify-between gap-2"><span className="text-xs text-muted-foreground">{t('guideCenter.trackProgress', { done: progress?.completed_steps.length || 0, total: progress?.total || 3 })}</span><Button variant="secondary" size="sm" disabled={tour?.busy || !status} onClick={() => void tour?.startTrack(key, !!finished)}>{t(finished ? 'guideCenter.replay' : progress?.started ? 'gettingStarted.resume' : 'guideCenter.explore')}<ArrowRight className="h-3.5 w-3.5" /></Button></div>
        </section>
      })}
    </div>
  </div>
}
