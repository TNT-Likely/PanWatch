import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@panwatch/base-ui/components/ui/button'

export default function SetupReturnLink() {
  const { t } = useTranslation('bizUi')
  if (!new URLSearchParams(window.location.search).has('setup')) return null
  return <div className="mb-4"><Button variant="secondary" asChild>
    <Link to="/getting-started"><ArrowLeft className="h-4 w-4" />{t('gettingStarted.continue')}</Link>
  </Button></div>
}
