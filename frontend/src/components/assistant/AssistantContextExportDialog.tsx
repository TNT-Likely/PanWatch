import { useCallback, useEffect, useRef, useState } from 'react'
import { Copy, Download, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { chatApi, type AssistantContextExportJob } from '@panwatch/api'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { normalizeLocale } from '@/i18n'
import { NOTIFICATIONS_CHANGED } from '@/lib/notifications'

interface Props {
  conversationId: number
  exportId?: number
  onClose: () => void
}

function pause(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')) }
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve() }, 2000)
    signal.addEventListener('abort', abort, { once: true })
    if (signal.aborted) abort()
  })
}

export function AssistantContextExportContent({ conversationId, exportId }: Omit<Props, 'onClose'>) {
  const { t, i18n } = useTranslation('configuration')
  const tr = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const language = normalizeLocale(i18n.resolvedLanguage || i18n.language)
  const [job, setJob] = useState<AssistantContextExportJob | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const request = useRef<AbortController>()
  const jobId = useRef<number | undefined>(exportId)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const mounted = useRef(false)

  const monitor = useCallback(async (retry = false) => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setLoading(true); setError(''); setCopyState('idle')
    const fetchJob = async (initial: boolean) => {
      // Only submission and status reads are bounded by an HTTP timeout.
      const deadline = setTimeout(() => controller.abort(), 20000)
      try {
        if (initial && retry && jobId.current) return await chatApi.retryContextExport(jobId.current, controller.signal)
        if (jobId.current) return await chatApi.getContextExport(jobId.current, controller.signal)
        return await chatApi.exportConversationContext(conversationId, language, controller.signal)
      } finally { clearTimeout(deadline) }
    }
    try {
      let next = await fetchJob(true)
      while (request.current === controller && !controller.signal.aborted) {
        jobId.current = next.id
        setJob(next); setLoading(false)
        if (next.status === 'completed' || next.status === 'failed') {
          window.dispatchEvent(new Event(NOTIFICATIONS_CHANGED))
          break
        }
        await pause(controller.signal)
        next = await fetchJob(false)
      }
    } catch (cause) {
      if (request.current === controller) {
        setError(controller.signal.aborted ? tr('assistantPage.exportContext.connectionHint') :
          cause instanceof Error ? cause.message : tr('assistantPage.exportContext.connectionHint'))
      }
    } finally {
      if (request.current === controller) setLoading(false)
    }
  }, [conversationId, language, tr])

  useEffect(() => {
    mounted.current = true
    jobId.current = exportId
    setJob(null)
    // Deferral avoids duplicate submissions during StrictMode setup/cleanup.
    const start = setTimeout(() => { void monitor() }, 0)
    return () => {
      mounted.current = false
      clearTimeout(start)
      const current = request.current
      request.current = undefined
      // Leaving stops polling; the durable server job continues.
      current?.abort()
    }
  }, [monitor, exportId])

  const result = job?.result
  const pending = job?.status === 'queued' || job?.status === 'running'
  const jobError = job?.status === 'failed' ? tr(`assistantPage.exportContext.errors.${job.error_code}`, {
    defaultValue: tr(`assistantPage.errors.${job.error_code}`, { defaultValue: tr('assistantPage.exportContext.failed') }),
  }) : ''
  const copy = async () => {
    if (!result) return
    try {
      await navigator.clipboard.writeText(result.content)
      if (mounted.current) setCopyState('copied')
    } catch {
      textarea.current?.focus(); textarea.current?.select()
      if (mounted.current) setCopyState('failed')
    }
  }
  const download = () => {
    if (!result) return
    const url = URL.createObjectURL(new Blob([result.content], { type: 'text/markdown;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url; link.download = result.filename
    document.body.appendChild(link); link.click(); link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return <>
      <DialogHeader>
        <DialogTitle>{tr('assistantPage.exportContext.title')}</DialogTitle>
        <DialogDescription>{tr('assistantPage.exportContext.description')}</DialogDescription>
      </DialogHeader>
      {(loading || (pending && !error)) && <div role="status" className="flex items-center gap-2 py-4 text-[13px] text-muted-foreground"><Loader2 aria-hidden className="h-4 w-4 animate-spin motion-reduce:animate-none" />{tr(job?.status === 'queued' ? 'assistantPage.exportContext.queued' : 'assistantPage.exportContext.generating')}</div>}
      {pending && <>
        <p className="text-[12px] text-muted-foreground">{tr('assistantPage.exportContext.backgroundHint')}</p>
        {job && job.completed_parts > 0 && <p className="mt-2 text-[12px] text-muted-foreground">{tr('assistantPage.exportContext.progress', { count: job.completed_parts, percent: Math.min(99, Math.floor(job.processed_chars / Math.max(1, job.total_chars) * 100)) })}</p>}
      </>}
      {(error || jobError) && <div><p role="alert" className="mt-3 text-[13px] text-destructive">{error || jobError}</p><button type="button" disabled={loading} onClick={() => { void monitor(job?.status === 'failed') }} className="mt-4 rounded-lg border border-border px-3 py-2 text-[12px] disabled:opacity-50">{tr(job?.status === 'failed' ? 'assistantPage.exportContext.retry' : 'assistantPage.exportContext.refresh')}</button></div>}
      {result && <>
        <p className="mb-3 text-[12px] text-muted-foreground">{tr('assistantPage.exportContext.snapshot', { date: new Date(result.exported_at).toLocaleString(language), count: result.message_count })}</p>
        {result.incomplete && <p className="mb-3 text-[12px] text-muted-foreground">{tr('assistantPage.exportContext.incomplete')}</p>}
        <textarea ref={textarea} readOnly value={result.content} aria-label={tr('assistantPage.exportContext.preview')} className="h-[min(45vh,24rem)] w-full resize-none rounded-xl border border-border bg-background p-3 font-mono text-[12px] leading-6 outline-none focus:border-primary" />
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <button type="button" onClick={() => { void copy() }} className="flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-[12px]"><Copy aria-hidden className="h-3.5 w-3.5" />{tr(copyState === 'copied' ? 'assistantPage.exportContext.copied' : 'assistantPage.exportContext.copy')}</button>
          <button type="button" onClick={download} className="flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-[12px] text-primary-foreground"><Download aria-hidden className="h-3.5 w-3.5" />{tr('assistantPage.exportContext.download')}</button>
        </div>
        {copyState !== 'idle' && <p role="status" className="mt-2 text-[12px] text-muted-foreground">{tr(copyState === 'copied' ? 'assistantPage.exportContext.copied' : 'assistantPage.exportContext.copyFailed')}</p>}
      </>}
  </>
}

export function AssistantContextExportDialog({ onClose, ...props }: Props) {
  return <Dialog open onOpenChange={next => { if (!next) onClose() }}>
    <DialogContent className="max-w-2xl"><AssistantContextExportContent {...props} /></DialogContent>
  </Dialog>
}
