import { useRef, useState } from 'react'
import { Bot, Loader2 } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useTranslation } from 'react-i18next'
import { onboardingApi } from '@panwatch/api/onboarding'
import { localizedApiError } from '@panwatch/api/client'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { useGuidedTour } from './GuidedTour'

export default function FirstAnalysis() {
  const { t, i18n } = useTranslation('bizUi')
  const tour = useGuidedTour()
  const status = tour?.status
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const lock = useRef(false)
  if (!tour?.active || tour.step !== 'analysis' || !status?.selected_stock) return null
  const run = status.analysis
  const running = busy || run?.status === 'running'
  const ready = !!status.quote && status.models.some(model => model.is_default && model.verified)
  const analyze = async () => {
    if (lock.current || !ready || running) return
    lock.current = true; setBusy(true); setError('')
    try { tour?.accept(await onboardingApi.analyze()) }
    catch (exc) { setError(exc instanceof Error ? exc.message : t('gettingStarted.errors.action')) }
    finally { lock.current = false; setBusy(false) }
  }
  return <section className="card mb-4 flex flex-wrap items-center justify-between gap-3 p-4">
    <div><p className="text-sm font-medium">{t('gettingStarted.steps.analysis.title')} · {status.selected_stock.name}</p><p className="mt-1 text-xs text-muted-foreground">{t('gettingStarted.analysisScope')}</p></div>
    <Button data-tour="first-analysis" size="sm" variant="secondary" onClick={() => setOpen(true)}><Bot className="h-4 w-4" />{t('tour.openAnalysis')}</Button>
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-w-2xl">
        <DialogHeader><DialogTitle>{t('gettingStarted.steps.analysis.title')} · {status.selected_stock.name}</DialogTitle><DialogDescription>{t('gettingStarted.analysisHint')}</DialogDescription></DialogHeader>
        <div data-tour-instructions />
        <div data-tour={run?.status === 'success' ? 'analysis-result' : 'analysis-form'} className="space-y-4">
          <p className="text-xs text-muted-foreground">{t('gettingStarted.analysisScope')}</p>
          {!ready && <p className="text-sm text-muted-foreground">{t('tour.analysisNotReady')}</p>}
          <Button disabled={running || !ready} onClick={() => void analyze()}>{running && <Loader2 className="h-4 w-4 animate-spin" />}{t(run?.status === 'success' ? 'gettingStarted.rerunAnalysis' : 'gettingStarted.runAnalysis')}</Button>
          {running && <p role="status" className="text-sm text-muted-foreground">{t('gettingStarted.analysisRunning')}</p>}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          {run?.status === 'failed' && <p role="alert" className="text-sm text-destructive">{localizedApiError({ code: 500, data: null, message: run.error_message, error_code: run.error_code }, 500)}</p>}
          {run?.status === 'success' && <>
            <p className="text-xs text-muted-foreground">{run.model_label} · {new Date(run.created_at).toLocaleString(i18n.language)}</p>
            <article className="prose prose-sm max-w-none break-words dark:prose-invert [&_pre]:overflow-x-auto [&_pre]:scrollbar"><ReactMarkdown remarkPlugins={[remarkGfm]}>{run.content}</ReactMarkdown></article>
          </>}
        </div>
      </DialogContent>
    </Dialog>
  </section>
}
