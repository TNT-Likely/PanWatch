import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { ToastProvider } from '@panwatch/base-ui/components/ui/toast'
import { getCurrentLocale } from './i18n'
import { MarketColorProvider } from '@/hooks/use-market-colors'
import { applyMarketColorScheme, readMarketColorPreference } from '@/lib/market-colors'
import './index.css'

applyMarketColorScheme(readMarketColorPreference(), getCurrentLocale())

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <MarketColorProvider>
      <BrowserRouter>
        <ToastProvider>
          <App />
        </ToastProvider>
      </BrowserRouter>
    </MarketColorProvider>
  </React.StrictMode>
)
