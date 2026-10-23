import React from 'react'
import { useTranslation } from 'react-i18next'
import { ConfirmProvider } from '@panwatch/base-ui/components/ui/confirm-dialog'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { ToastProvider } from '@panwatch/base-ui/components/ui/toast'
import { getCurrentLocale, i18nReady } from './i18n'
import { MarketColorProvider } from '@/hooks/use-market-colors'
import { applyMarketColorScheme, readMarketColorPreference } from '@/lib/market-colors'
import './index.css'

function ApplicationConfirmProvider({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation('common')
  return <ConfirmProvider labels={{ title: t('actions.confirm'), cancel: t('actions.cancel'), confirm: t('actions.confirm') }}>{children}</ConfirmProvider>
}

void i18nReady.then(() => {
applyMarketColorScheme(readMarketColorPreference(), getCurrentLocale())
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <React.Suspense fallback={<div className="min-h-dvh bg-background" />}>
    <MarketColorProvider>
      <BrowserRouter>
        <ToastProvider>
          <ApplicationConfirmProvider><App /></ApplicationConfirmProvider>
        </ToastProvider>
      </BrowserRouter>
    </MarketColorProvider>
    </React.Suspense>
  </React.StrictMode>
)
})
