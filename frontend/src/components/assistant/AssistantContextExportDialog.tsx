import { useCallback, useEffect, useRef, useState } from 'react'
import { Copy, Download, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { chatApi, type AssistantContextExport } from '@panwatch/api'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { normalizeLocale } from '@/i18n'

interface Props {
  conversationId: number
  onClose: () => void
}

export function AssistantContextExportDialog({ conversationId, onClose }: Props) {
  const { t, i18n } = useTranslation('configuration')
  const tr = t as unknown as (key: string) => string
  const language = normalizeLocale(i18n.resolvedLanguage || i18n.language)
  const [result, setResult] = useState<AssistantContextExport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const request = useRef<AbortController>()
  const deadline = useRef<ReturnType<typeof setTimeout>>()
  const textarea = useRef<HTMLTextAreaElement>(null)
  const mounted = useRef(false)

  const generate = useCallback(async () => {
    request.current?.abort()
    clearTimeout(deadline.current)
    const controller = new AbortController()
    request.current = controller
    setLoading(true); setError(''); setResult(null); setCopyState('idle')
    deadline.current = setTimeout(() => {
      if (request.current !== controller) return
      setError(tr('assistantPage.exportContext.timeout'))
      setLoading(false)
      controller.abort()
    }, 90000)
    try {
      const exported = await chatApi.exportConversationContext(conversationId, language, controller.signal)
      if (request.current === controller && !controller.signal.aborted) setResult(exported)
    } catch (cause) {
      if (request.current === controller && !controller.signal.aborted) {
        setError(cause instanceof Error ? cause.message : tr('assistantPage.exportContext.failed'))
      }
    } finally {
      if (request.current === controller) { clearTimeout(deadline.current); setLoading(false) }
    }
  }, [conversationId, language, tr])

  useEffect(() => {
    mounted.current = true
    // Deferral avoids duplicate model requests during StrictMode setup/cleanup.
    const start = setTimeout(() => { void generate() }, 0)
    return () => {
      mounted.current = false
      clearTimeout(start); clearTimeout(deadline.current)
      const current = request.current
      request.current = undefined
      current?.abort()
    }
  }, [generate])

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

  return <Dialog open onOpenChange={next => { if (!next) onClose() }}>
    <DialogContent className="max-w-2xl">
      <DialogHeader>
        <DialogTitle>{tr('assistantPage.exportContext.title')}</DialogTitle>
        <DialogDescription>{tr('assistantPage.exportContext.description')}</DialogDescription>
      </DialogHeader>
      {loading && <div role="status" className="flex items-center gap-2 py-8 text-[13px] text-muted-foreground"><Loader2 aria-hidden className="h-4 w-4 animate-spin motion-reduce:animate-none" />{tr('assistantPage.exportContext.generating')}</div>}
      {error && <div><p role="alert" className="text-[13px] text-destructive">{error}</p><button type="button" onClick={() => { void generate() }} className="mt-4 rounded-lg border border-border px-3 py-2 text-[12px]">{tr('assistantPage.exportContext.retry')}</button></div>}
      {result && <>
        {result.incomplete && <p className="mb-3 text-[12px] text-muted-foreground">{tr('assistantPage.exportContext.incomplete')}</p>}
        <textarea ref={textarea} readOnly value={result.content} aria-label={tr('assistantPage.exportContext.preview')} className="h-[min(45vh,24rem)] w-full resize-none rounded-xl border border-border bg-background p-3 font-mono text-[12px] leading-6 outline-none focus:border-primary" />
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <button type="button" onClick={() => { void copy() }} className="flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-[12px]"><Copy aria-hidden className="h-3.5 w-3.5" />{tr(copyState === 'copied' ? 'assistantPage.exportContext.copied' : 'assistantPage.exportContext.copy')}</button>
          <button type="button" onClick={download} className="flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-[12px] text-primary-foreground"><Download aria-hidden className="h-3.5 w-3.5" />{tr('assistantPage.exportContext.download')}</button>
        </div>
        {copyState !== 'idle' && <p role="status" className="mt-2 text-[12px] text-muted-foreground">{tr(copyState === 'copied' ? 'assistantPage.exportContext.copied' : 'assistantPage.exportContext.copyFailed')}</p>}
      </>}
    </DialogContent>
  </Dialog>
}
