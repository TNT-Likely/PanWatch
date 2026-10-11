import { useTranslation } from 'react-i18next'
import { Button } from '@panwatch/base-ui/components/ui/button'

export default function AuthStatusFeedback({ checking, retry }: { checking: boolean; retry: () => void }) {
  const { t } = useTranslation('auth')
  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      {checking ? (
        <div role="status" aria-label={t('messages.checking')}>
          <span className="block w-6 h-6 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
        </div>
      ) : (
        <div className="card p-6 w-full max-w-sm space-y-4 text-center">
          <p role="alert" className="text-sm text-muted-foreground">{t('messages.statusFailed')}</p>
          <Button onClick={retry}>{t('actions.retry')}</Button>
        </div>
      )}
    </div>
  )
}
