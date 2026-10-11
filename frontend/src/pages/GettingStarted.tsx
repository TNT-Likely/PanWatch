import { useEffect } from 'react'
import { Compass } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { useGuidedTour } from '@/components/onboarding/GuidedTour'

export default function GettingStartedPage() {
  const { t } = useTranslation('bizUi')
  const tour = useGuidedTour()
  const openWelcome = tour?.openWelcome
  useEffect(() => { openWelcome?.() }, [openWelcome])
  return <div className="mx-auto max-w-xl py-12">
    <div className="card p-6">
      <Compass className="mb-3 h-7 w-7 text-primary" />
      <h1 className="text-xl font-semibold">{t('tour.title')}</h1>
      <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{t('tour.welcome')}</p>
      {tour?.error && <p role="alert" className="mt-3 text-sm text-destructive">{tour.error}</p>}
      <div className="mt-5 flex gap-2"><Button onClick={openWelcome}>{t('tour.start')}</Button>{tour?.error && <Button variant="secondary" onClick={() => void tour.refresh()}>{t('gettingStarted.retry')}</Button>}</div>
    </div>
  </div>
}
