import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Bell, Bot, Check, Compass, Loader2, RefreshCw, Search, TrendingUp } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useTranslation } from 'react-i18next'
import { fetchAPI, localizedApiError } from '@panwatch/api/client'
import { onboardingApi, type SetupStatus, type SetupStep } from '@panwatch/api/onboarding'
import { stocksApi, type StockItem } from '@panwatch/api/stocks'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import PriceAlertFormDialog from '@panwatch/biz-ui/components/price-alert-form-dialog'
import { marketSignTextClass } from '@/lib/market-colors'

type SearchResult = { symbol: string; name: string; market: string }

export default function GettingStartedPage() {
  const { t, i18n } = useTranslation('bizUi')
  const [status, setStatus] = useState<SetupStatus | null>(null)
  const [stocks, setStocks] = useState<StockItem[]>([])
  const [active, setActive] = useState<SetupStep | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [market, setMarket] = useState('CN')
  const [results, setResults] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')
  const [channelId, setChannelId] = useState('')
  const [alertOpen, setAlertOpen] = useState(false)
  const alive = useRef(true)
  const requestId = useRef(0)
  const actionBusy = useRef(false)

  const load = useCallback(async () => {
    const id = ++requestId.current
    const responses = await Promise.allSettled([onboardingApi.status(), stocksApi.list()])
    if (!alive.current || id !== requestId.current) return
    if (responses[0].status === 'fulfilled') { setStatus(responses[0].value); setError('') }
    else setError(t('gettingStarted.errors.load'))
    if (responses[1].status === 'fulfilled') setStocks(responses[1].value)
  }, [t])

  useEffect(() => {
    alive.current = true
    void load()
    const onFocus = () => { if (!actionBusy.current) void load() }
    window.addEventListener('focus', onFocus)
    return () => { alive.current = false; requestId.current++; window.removeEventListener('focus', onFocus) }
  }, [load])

  useEffect(() => {
    if (status?.analysis?.status !== 'running') return
    const timer = window.setInterval(() => { if (!actionBusy.current) void load() }, 3000)
    return () => window.clearInterval(timer)
  }, [status?.analysis?.status, load])

  useEffect(() => {
    let current = true
    setResults([]); setSearchError('')
    if (!query.trim()) { setSearching(false); return }
    setSearching(true)
    const timer = window.setTimeout(() => {
      fetchAPI<SearchResult[]>(`/stocks/search?q=${encodeURIComponent(query.trim())}&market=${market}`)
        .then(value => { if (current) setResults(value) })
        .catch(exc => { if (current) setSearchError(exc instanceof Error ? exc.message : t('gettingStarted.errors.action')) })
        .finally(() => { if (current) setSearching(false) })
    }, 300)
    return () => { current = false; window.clearTimeout(timer) }
  }, [query, market, t])

  const action = async (key: string, operation: () => Promise<SetupStatus | void>) => {
    if (actionBusy.current) return
    actionBusy.current = true
    requestId.current++
    setBusy(key); setError('')
    try {
      const value = await operation()
      if (!alive.current) return
      if (value) setStatus(value)
      else await load()
    } catch (exc) {
      if (alive.current) setError(exc instanceof Error ? exc.message : t('gettingStarted.errors.action'))
    } finally { actionBusy.current = false; if (alive.current) setBusy('') }
  }

  const step = active || status?.steps.find(item => item.required && item.status !== 'complete')?.key || 'stock'
  const currentStep = status?.steps.find(item => item.key === step)
  const selected = status?.selected_stock
  const defaultModel = status?.models.find(model => model.is_default)
  const enabledChannels = status?.channels.filter(channel => channel.enabled) || []
  const effectiveChannelId = enabledChannels.some(channel => String(channel.id) === channelId)
    ? channelId : String(enabledChannels.find(channel => channel.is_default)?.id || enabledChannels[0]?.id || '')
  const running = status?.analysis?.status === 'running'
  const time = (value: string) => !value ? t('gettingStarted.unknown') : /^\d{4}-\d{2}-\d{2}$/.test(value) ? value
    : new Date(value).toLocaleString(i18n.language)
  const advance = () => {
    const index = status?.steps.findIndex(item => item.key === step) ?? 0
    setActive(status?.steps[index + 1]?.key || step)
    setError('')
  }

  return (
    <div className="page-container max-w-6xl space-y-5 pb-10">
      <section className="card relative overflow-hidden p-5 md:p-7">
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-primary/10 via-transparent to-accent/20" />
        <div className="relative flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
          <div className="min-w-0">
            <h1 className="flex items-center gap-2 text-xl font-bold"><Compass className="h-6 w-6 text-primary" />{t('gettingStarted.title')}</h1>
            <p className="mt-2 max-w-2xl text-sm text-muted-foreground">{t('gettingStarted.description')}</p>
            {status && <p className="mt-3 text-sm font-medium text-primary">{t('gettingStarted.progress', { done: status.completed_count, total: status.required_count })}</p>}
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <Button variant="ghost" disabled={!!busy} onClick={() => void action('defer', () => onboardingApi.update({ deferred: true }))}>{t('gettingStarted.later')}</Button>
            <Button variant="secondary" asChild><Link to="/">{t('gettingStarted.backHome')}</Link></Button>
          </div>
        </div>
        <div className="relative mt-5 grid gap-3 sm:grid-cols-2" role="group" aria-label={t('gettingStarted.title')}>
          {(['quotes', 'ai'] as const).map(goal => (
            <button key={goal} type="button" aria-pressed={status?.goal === goal} disabled={!!busy || !status}
              onClick={() => void action('goal', () => onboardingApi.update({ goal, deferred: false }))}
              className={`flex items-start gap-3 rounded-xl border p-4 text-left transition-colors ${status?.goal === goal ? 'border-primary bg-primary/10 ring-1 ring-primary/20' : 'border-border bg-background/60 hover:border-primary/40'}`}>
              {goal === 'quotes' ? <TrendingUp className="mt-0.5 h-5 w-5 shrink-0 text-primary" /> : <Bot className="mt-0.5 h-5 w-5 shrink-0 text-primary" />}
              <div><p className="font-semibold">{t(goal === 'quotes' ? 'gettingStarted.quotesGoal' : 'gettingStarted.aiGoal')}</p>
                <p className="mt-1 text-xs text-muted-foreground">{t(goal === 'quotes' ? 'gettingStarted.quotesGoalDesc' : 'gettingStarted.aiGoalDesc')}</p></div>
            </button>
          ))}
        </div>
      </section>

      {error && <div role="alert" className="card flex flex-wrap items-center justify-between gap-2 border-destructive/30 p-4 text-sm text-destructive">
        <p>{error}</p><Button variant="secondary" onClick={() => void load()} disabled={!!busy}>{t('gettingStarted.retry')}</Button>
      </div>}
      {!status && !error && <p role="status" className="flex items-center gap-2 text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />{t('gettingStarted.loading')}</p>}

      {status && <>
        {status.deferred && <div className="card flex flex-wrap items-center justify-between gap-3 p-4">
          <p className="text-sm text-muted-foreground">{t('gettingStarted.skippedHint')}</p>
          <Button disabled={!!busy} onClick={() => void action('resume', () => onboardingApi.update({ deferred: false }))}>{t('gettingStarted.resume')}</Button>
        </div>}
        {status.completed && <section className="card border-primary/20 p-5" role="status">
          <h2 className="flex items-center gap-2 font-semibold"><Check className="h-5 w-5 text-primary" />{t('gettingStarted.doneTitle')}</h2>
          <p className="mt-2 text-sm text-muted-foreground">{t('gettingStarted.doneDescription')}</p>
        </section>}
        <div className="grid items-start gap-5 lg:grid-cols-[280px_minmax(0,1fr)]">
          <nav className="grid gap-2 sm:grid-cols-2 lg:grid-cols-1" aria-label={t('gettingStarted.title')}>
            {status.steps.map((item, index) => (
              <button key={item.key} type="button" aria-label={t(`gettingStarted.steps.${item.key}.title`)} aria-current={step === item.key ? 'step' : undefined}
                onClick={() => { setActive(item.key); setError('') }}
                className={`flex items-start gap-3 rounded-xl border p-4 text-left ${step === item.key ? 'border-primary/40 bg-primary/5' : 'border-border/60 bg-card hover:bg-accent/30'}`}>
                <span className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs ${item.status === 'complete' ? 'bg-primary text-primary-foreground' : 'bg-accent text-muted-foreground'}`}>
                  {item.status === 'complete' ? <Check className="h-3.5 w-3.5" /> : index + 1}
                </span>
                <div className="min-w-0"><p className="text-sm font-semibold">{t(`gettingStarted.steps.${item.key}.title`)}</p>
                  <p className="mt-1 text-xs text-muted-foreground">{t(`gettingStarted.steps.${item.key}.description`)}</p>
                  <p className="mt-2 text-xs text-muted-foreground">{t(`gettingStarted.${item.status === 'complete' ? 'completed' : item.status === 'skipped' ? 'skipped' : 'pending'}`)} · {t(item.required ? 'gettingStarted.required' : 'gettingStarted.optional')}</p>
                </div>
              </button>
            ))}
          </nav>
          <section className="card min-w-0 space-y-5 p-5 md:p-6" aria-labelledby="setup-step-title">
            <header><h2 id="setup-step-title" className="text-lg font-semibold">{t(`gettingStarted.steps.${step}.title`)}</h2>
              {selected && <p className="mt-1 text-xs text-muted-foreground">{t('gettingStarted.currentStock', { name: selected.name, symbol: selected.symbol })}</p>}
            </header>

            {step === 'stock' && <>
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="setup-stock">{t('gettingStarted.existingStocks')}</label>
                <Select value={selected ? String(selected.id) : ''} disabled={!!busy || running} onValueChange={value => void action('stock', async () => {
                  const valueStatus = await onboardingApi.update({ stock_id: Number(value), deferred: false }); setActive('quote'); return valueStatus
                })}><SelectTrigger id="setup-stock"><SelectValue placeholder={t('gettingStarted.stockPlaceholder')} /></SelectTrigger>
                  <SelectContent>{stocks.map(stock => <SelectItem key={stock.id} value={String(stock.id)}>{stock.name} ({stock.symbol}) · {stock.market}</SelectItem>)}</SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">{t('gettingStarted.sampleHint')}</p>
              </div>
              <div className="space-y-3 border-t border-border/60 pt-4">
                <h3 className="flex items-center gap-2 text-sm font-medium"><Search className="h-4 w-4" />{t('gettingStarted.search')}</h3>
                <div className="flex flex-col gap-2 sm:flex-row">
                  <Select value={market} onValueChange={setMarket}><SelectTrigger className="sm:w-36" aria-label={t('gettingStarted.market')}><SelectValue /></SelectTrigger>
                    <SelectContent>{(['CN', 'HK', 'US'] as const).map(value => <SelectItem key={value} value={value}>{t(`gettingStarted.markets.${value}`)}</SelectItem>)}</SelectContent></Select>
                  <Input value={query} onChange={event => setQuery(event.target.value)} placeholder={t('gettingStarted.searchPlaceholder')} aria-label={t('gettingStarted.search')} />
                </div>
                {searching && <p role="status" className="text-sm text-muted-foreground">{t('gettingStarted.searching')}</p>}
                {searchError && <p role="alert" className="text-sm text-destructive">{searchError}</p>}
                {!searching && query.trim() && !results.length && !searchError && <p className="text-sm text-muted-foreground">{t('gettingStarted.noResults')}</p>}
                <div className="max-h-64 space-y-2 overflow-y-auto scrollbar">
                  {results.map(result => <div key={`${result.market}:${result.symbol}`} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-accent/25 p-3">
                    <p className="min-w-0 text-sm">{result.name} <span className="text-muted-foreground">({result.symbol})</span></p>
                    <Button size="sm" disabled={!!busy || running} onClick={() => void action('add', async () => {
                      const existing = stocks.find(stock => stock.symbol === result.symbol && stock.market === result.market)
                      const added = existing || await stocksApi.create(result)
                      setStocks(items => existing ? items : [...items, added])
                      const value = await onboardingApi.update({ stock_id: added.id, deferred: false }); setQuery(''); setActive('quote'); return value
                    })}>{t('gettingStarted.addStock')}</Button>
                  </div>)}
                </div>
                <Button variant="secondary" asChild><Link to="/portfolio?setup=1">{t('gettingStarted.manageStocks')}</Link></Button>
              </div>
            </>}

            {step !== 'stock' && !selected && <Button onClick={() => setActive('stock')}>{t('gettingStarted.selectFirst')}</Button>}
            {step === 'quote' && selected && <>
              <p className="text-sm text-muted-foreground">{t('gettingStarted.quoteHint')}</p>
              {status.quote && <div className="rounded-xl bg-accent/30 p-5">
                <p className="text-3xl font-semibold tabular-nums">{status.quote.current_price.toLocaleString(i18n.language, { maximumFractionDigits: 4 })} <span className="text-sm text-muted-foreground">{selected.market === 'US' ? 'USD' : selected.market === 'HK' ? 'HKD' : 'CNY'}</span></p>
                {status.quote.change_pct != null && <p className={`mt-1 font-medium ${marketSignTextClass(status.quote.change_pct)}`}>{status.quote.change_pct > 0 ? '+' : ''}{status.quote.change_pct.toFixed(2)}%</p>}
                <div className="mt-4 space-y-1 text-xs text-muted-foreground"><p>{t('gettingStarted.source', { value: status.quote.source || t('gettingStarted.unknown') })}</p>
                  <p>{t('gettingStarted.sourceTime', { value: time(status.quote.source_time) })}</p><p>{t('gettingStarted.observedAt', { value: time(status.quote.observed_at) })}</p></div>
              </div>}
              <div className="flex flex-wrap gap-2"><Button disabled={!!busy} onClick={() => void action('quote', onboardingApi.quote)}><RefreshCw className={`h-4 w-4 ${busy === 'quote' ? 'animate-spin' : ''}`} />{t(status.quote ? 'gettingStarted.refreshQuote' : 'gettingStarted.fetchQuote')}</Button>
                <Button variant="secondary" asChild><Link to="/datasources">{t('gettingStarted.dataSources')}</Link></Button></div>
            </>}

            {step === 'ai' && selected && <>
              <p className="text-sm text-muted-foreground">{t('gettingStarted.aiHint')}</p>
              <Button asChild><Link to="/settings?setup=1#sec-ai"><Bot className="h-4 w-4" />{t('gettingStarted.configureAi')}</Link></Button>
              {status.models.length > 0 && <div className="space-y-3">
                <label htmlFor="setup-model" className="text-sm font-medium">{t('gettingStarted.model')}</label>
                <Select value={defaultModel ? String(defaultModel.id) : ''} disabled={!!busy || running} onValueChange={value => void action('model', async () => {
                  await fetchAPI(`/providers/models/${value}`, { method: 'PUT', body: JSON.stringify({ is_default: true }) })
                })}><SelectTrigger id="setup-model"><SelectValue placeholder={t('gettingStarted.modelPlaceholder')} /></SelectTrigger>
                  <SelectContent>{status.models.map(model => <SelectItem key={model.id} value={String(model.id)}>{model.name} ({model.model})</SelectItem>)}</SelectContent></Select>
                <Button variant="secondary" disabled={!!busy || !defaultModel || running} onClick={() => void action('test-model', async () => {
                  await fetchAPI(`/providers/models/${defaultModel!.id}/test`, { method: 'POST', timeoutMs: 60000 })
                })}>{busy === 'test-model' && <Loader2 className="h-4 w-4 animate-spin" />}{t('gettingStarted.testModel')}</Button>
                {defaultModel?.verified && <p className="flex items-center gap-2 text-sm text-primary"><Check className="h-4 w-4" />{t('gettingStarted.modelVerified')}</p>}
              </div>}
              <p className="text-xs text-muted-foreground">{t('gettingStarted.configureChanged')}</p>
            </>}

            {step === 'analysis' && selected && <>
              <p className="text-sm text-muted-foreground">{t('gettingStarted.analysisHint')}</p>
              <p className="text-xs text-muted-foreground">{t('gettingStarted.analysisScope')}</p>
              {!status.quote ? <Button onClick={() => setActive('quote')}>{t('gettingStarted.fetchQuote')}</Button> : !defaultModel?.verified ? <Button onClick={() => setActive('ai')}>{t('gettingStarted.configureAi')}</Button> :
                <Button disabled={!!busy || running} onClick={() => void action('analysis', onboardingApi.analyze)}>{(running || busy === 'analysis') && <Loader2 className="h-4 w-4 animate-spin" />}{t(status.analysis?.status === 'success' ? 'gettingStarted.rerunAnalysis' : 'gettingStarted.runAnalysis')}</Button>}
              {running && <p role="status" className="text-sm text-muted-foreground">{t('gettingStarted.analysisRunning')}</p>}
              {status.analysis?.status === 'failed' && <div role="alert" className="rounded-xl bg-destructive/5 p-4 text-sm text-destructive">
                <p>{t('gettingStarted.analysisFailed')}</p>
                <p className="mt-1">{localizedApiError({ code: 500, data: null, message: status.analysis.error_message, error_code: status.analysis.error_code }, 500)}</p>
              </div>}
              {status.analysis?.status === 'success' && <>
                <div className="space-y-1 text-xs text-muted-foreground"><p>{t('gettingStarted.analysisModel', { value: status.analysis.model_label })}</p><p>{t('gettingStarted.analysisTime', { value: time(status.analysis.created_at) })}</p></div>
                <article className="prose prose-sm max-w-none break-words dark:prose-invert [&_pre]:overflow-x-auto [&_pre]:scrollbar"><ReactMarkdown remarkPlugins={[remarkGfm]}>{status.analysis.content}</ReactMarkdown></article>
                <div className="rounded-xl bg-primary/5 p-4"><h3 className="text-sm font-semibold">{t('gettingStarted.readAnalysis')}</h3><p className="mt-1 text-sm text-muted-foreground">{t('gettingStarted.readAnalysisHint')}</p></div>
              </>}
            </>}

            {step === 'alert' && selected && <>
              <p className="text-sm text-muted-foreground">{t('gettingStarted.alertHint')}</p>
              {currentStep?.status === 'complete' && <p className="text-sm text-primary">{t('gettingStarted.alertSaved')}</p>}
              <div className="flex flex-wrap gap-2"><Button disabled={!!busy} onClick={() => setAlertOpen(true)}><Bell className="h-4 w-4" />{t('gettingStarted.createAlert')}</Button><Button variant="secondary" asChild><Link to="/alerts">{t('gettingStarted.manageAlerts')}</Link></Button></div>
            </>}

            {step === 'notify' && selected && <>
              <p className="text-sm text-muted-foreground">{t('gettingStarted.notifyHint')}</p>
              <Button asChild><Link to="/settings?setup=1#sec-notify">{t('gettingStarted.configureNotify')}</Link></Button>
              {!!enabledChannels.length && <div className="space-y-3">
                <label htmlFor="setup-channel" className="text-sm font-medium">{t('gettingStarted.channel')}</label>
                <Select value={effectiveChannelId} onValueChange={setChannelId} disabled={!!busy}><SelectTrigger id="setup-channel"><SelectValue placeholder={t('gettingStarted.channelPlaceholder')} /></SelectTrigger>
                  <SelectContent>{enabledChannels.map(channel => <SelectItem key={channel.id} value={String(channel.id)}>{channel.name}</SelectItem>)}</SelectContent></Select>
                <Button disabled={!!busy || !effectiveChannelId} variant="secondary" onClick={() => void action('test-channel', async () => {
                  await fetchAPI(`/channels/${effectiveChannelId}/test`, { method: 'POST', timeoutMs: 60000 })
                })}>{busy === 'test-channel' && <Loader2 className="h-4 w-4 animate-spin" />}{t('gettingStarted.testChannel')}</Button>
              </div>}
              {currentStep?.status === 'complete' && <p className="text-sm text-primary">{t('gettingStarted.notifyVerified')}</p>}
              <p className="text-xs text-muted-foreground">{t('gettingStarted.deliveryHint')}</p>
            </>}

            <footer className="flex flex-wrap justify-between gap-2 border-t border-border/60 pt-4">
              {step === 'alert' || step === 'notify' ? <Button variant="ghost" disabled={!!busy} onClick={() => void action('skip', async () => {
                const value = await onboardingApi.update(currentStep?.status === 'skipped' ? { unskip: step } : { skip: step }); return value
              })}>{t(currentStep?.status === 'skipped' ? 'gettingStarted.doNow' : 'gettingStarted.later')}</Button> : <span />}
              {step !== 'notify' && <Button variant="secondary" disabled={!!busy || currentStep?.status === 'pending'} onClick={advance}>{t('gettingStarted.next')}<ArrowRight className="h-4 w-4" /></Button>}
            </footer>
          </section>
        </div>
        <section className="card p-5"><h2 className="text-sm font-semibold">{t('gettingStarted.advanced')}</h2><p className="mt-2 text-sm text-muted-foreground">{t('gettingStarted.advancedHint')}</p><Button className="mt-3" variant="secondary" asChild><Link to="/agents">{t('gettingStarted.agents')}</Link></Button></section>
      </>}
      {selected && <PriceAlertFormDialog open={alertOpen} onOpenChange={setAlertOpen} stocks={[selected]} channels={status?.channels}
        title={t('gettingStarted.alertTitle')} description={t('gettingStarted.alertHint')} initial={{ stock_id: selected.id }} submitting={busy === 'alert'}
        onSubmit={async payload => { await action('alert', async () => { await fetchAPI('/price-alerts', { method: 'POST', body: JSON.stringify(payload) }); setAlertOpen(false) }) }} />}
    </div>
  )
}
