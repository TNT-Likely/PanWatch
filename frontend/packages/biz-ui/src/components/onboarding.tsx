import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Compass } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { onboardingApi, type SetupStatus } from '@panwatch/api/onboarding'
import { Button } from '@panwatch/base-ui/components/ui/button'

export function Onboarding() {
  const { t } = useTranslation('bizUi')
  const [status, setStatus] = useState<SetupStatus | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    let alive = true
    onboardingApi.status().then(value => { if (alive) setStatus(value) })
      .catch(() => { if (alive) setError(t('gettingStarted.errors.load')) })
    return () => { alive = false }
  }, [t])
  if (status?.completed || status?.deferred) return (
    <Link to="/getting-started" className="mb-4 inline-flex items-center gap-2 text-xs text-muted-foreground hover:text-primary">
      <Compass className="h-4 w-4" />{t(status.completed ? 'gettingStarted.review' : 'gettingStarted.resume')}
    </Link>
  )
  return (
    <section className="card mb-4 border-primary/20 bg-primary/5 p-4 md:p-5" aria-label={t('gettingStarted.title')}>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 font-semibold"><Compass className="h-5 w-5 text-primary" />{t('gettingStarted.bannerTitle')}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t('gettingStarted.bannerDescription')}</p>
          {status && <p className="mt-2 text-xs text-muted-foreground">{t('gettingStarted.progress', { done: status.completed_count, total: status.required_count })}</p>}
          {error && <p role="alert" className="mt-2 text-sm text-destructive">{error}</p>}
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Button asChild><Link to="/getting-started">{t(status?.started ? 'gettingStarted.continue' : 'gettingStarted.start')}<ArrowRight className="h-4 w-4" /></Link></Button>
          <Button variant="ghost" disabled={saving} onClick={async () => {
            setSaving(true)
            try { setStatus(await onboardingApi.update({ deferred: true })); setError('') }
            catch { setError(t('gettingStarted.errors.action')) }
            finally { setSaving(false) }
          }}>{t('gettingStarted.later')}</Button>
        </div>
      </div>
    </section>
  )
}
