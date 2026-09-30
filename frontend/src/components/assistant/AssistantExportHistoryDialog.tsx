import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, ArrowLeft, CheckCircle2, FileText, Loader2, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { chatApi, type AssistantContextExportJobInfo } from '@panwatch/api'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { normalizeLocale } from '@/i18n'
import { AssistantContextExportContent } from './AssistantContextExportDialog'

interface Props {
  conversationId?: number
  onClose: () => void
}

export function AssistantExportHistoryDialog({ conversationId, onClose }: Props) {
  const { t, i18n } = useTranslation('configuration')
  const tr = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const locale = normalizeLocale(i18n.resolvedLanguage || i18n.language)
  const [items, setItems] = useState<AssistantContextExportJobInfo[]>([])
  const [cursor, setCursor] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<AssistantContextExportJobInfo | null>(null)
  const request = useRef<AbortController>()
  const revision = useRef(0)
  const itemsRef = useRef(items)
  itemsRef.current = items

  const load = useCallback(async (beforeId?: number) => {
    revision.current += 1
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    const deadline = setTimeout(() => controller.abort(), 20000)
    setLoading(true); setError('')
    try {
      const page = await chatApi.listContextExports({ conversationId, beforeId }, controller.signal)
      if (request.current !== controller) return
      setItems(previous => beforeId == null ? page.items : [...previous, ...page.items.filter(item => !previous.some(old => old.id === item.id))])
      setCursor(page.next_cursor)
    } catch (cause) {
      if (request.current === controller) setError(cause instanceof Error ? cause.message : tr('assistantPage.exportHistory.loadFailed'))
    } finally {
      clearTimeout(deadline)
      if (request.current === controller) { request.current = undefined; setLoading(false) }
    }
  }, [conversationId, tr])

  useEffect(() => {
    setItems([]); setCursor(null); setSelected(null)
    const timer = setTimeout(() => { void load() }, 0)
    return () => {
      clearTimeout(timer)
      const current = request.current
      request.current = undefined
      current?.abort()
    }
  }, [load])

  useEffect(() => {
    if (selected) return
    let controller: AbortController | undefined
    let stopped = false
    let busy = false
    // Poll only active records already visible in history. Opening history
    // reads existing jobs and never submits another export.
    const timer = setInterval(async () => {
      if (busy || document.hidden) return
      const active = itemsRef.current.filter(item => item.status === 'queued' || item.status === 'running')
      if (!active.length) return
      busy = true
      controller = new AbortController()
      const current = controller
      const currentRevision = revision.current
      const deadline = setTimeout(() => current.abort(), 20000)
      try {
        const batches: number[][] = []
        for (let index = 0; index < active.length; index += 50) batches.push(active.slice(index, index + 50).map(item => item.id))
        const responses = await Promise.allSettled(batches.map(ids => chatApi.listContextExports({ ids, limit: 50 }, current.signal)))
        if (stopped || currentRevision !== revision.current) return
        const updates = new Map(responses.flatMap(response => response.status === 'fulfilled' ? response.value.items.map(item => [item.id, item] as const) : []))
        const checked = new Set(responses.flatMap((response, index) => response.status === 'fulfilled' ? batches[index] : []))
        setItems(previous => previous.filter(item => !checked.has(item.id) || updates.has(item.id)).map(item => updates.get(item.id) || item))
        setError(responses.some(response => response.status === 'rejected') ? tr('assistantPage.exportHistory.connectionHint') : '')
      } catch {
        if (!stopped) setError(tr('assistantPage.exportHistory.connectionHint'))
      } finally { clearTimeout(deadline); busy = false }
    }, 3000)
    return () => { stopped = true; clearInterval(timer); controller?.abort() }
  }, [selected, tr])

  return <Dialog open onOpenChange={next => { if (!next) onClose() }}>
    <DialogContent className="max-w-2xl">
      {selected ? <>
        <button type="button" onClick={() => { setSelected(null); void load() }} className="mb-4 flex items-center gap-1.5 rounded-md text-[12px] text-muted-foreground hover:text-foreground"><ArrowLeft aria-hidden className="h-3.5 w-3.5" />{tr('assistantPage.exportHistory.back')}</button>
        <AssistantContextExportContent key={selected.id} conversationId={selected.conversation_id} exportId={selected.id} />
      </> : <>
        <DialogHeader>
          <DialogTitle>{tr('assistantPage.exportHistory.title')}</DialogTitle>
          <DialogDescription>{tr(conversationId == null ? 'assistantPage.exportHistory.description' : 'assistantPage.exportHistory.conversationDescription')}</DialogDescription>
        </DialogHeader>
        <div className="mb-3 flex justify-end"><button type="button" disabled={loading} onClick={() => { void load() }} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-[12px] disabled:opacity-50"><RefreshCw aria-hidden className="h-3.5 w-3.5" />{tr('assistantPage.exportHistory.refresh')}</button></div>
        {error && <p role="alert" className="mb-3 text-[12px] text-destructive">{error}</p>}
        <div className="max-h-[55vh] space-y-2 overflow-y-auto pr-1 scrollbar">
          {items.map(item => {
            const active = item.status === 'queued' || item.status === 'running'
            const Icon = active ? Loader2 : item.status === 'completed' ? CheckCircle2 : AlertTriangle
            return <button key={item.id} type="button" onClick={() => setSelected(item)} className="flex w-full items-start gap-3 rounded-xl border border-border bg-background p-3 text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
              <Icon aria-hidden className={`mt-0.5 h-4 w-4 shrink-0 ${active ? 'animate-spin text-primary motion-reduce:animate-none' : item.status === 'failed' ? 'text-destructive' : 'text-muted-foreground'}`} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium">{item.title || tr('assistantPage.newResearch')}</span>
                <span className="mt-1 block text-[11px] text-muted-foreground">{tr('assistantPage.exportContext.snapshot', { date: new Date(item.created_at).toLocaleString(locale), count: item.message_count })} · {tr(`assistantPage.exportHistory.languages.${item.language}`)}</span>
                <span className="mt-1 block text-[12px] text-muted-foreground">{tr(`assistantPage.exportHistory.statuses.${item.status}`)}{active && item.completed_parts > 0 ? ` · ${Math.min(99, Math.floor(item.processed_chars / Math.max(1, item.total_chars) * 100))}%` : ''}</span>
              </span>
              <FileText aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
            </button>
          })}
          {!loading && !error && items.length === 0 && <p className="py-8 text-center text-[13px] text-muted-foreground">{tr('assistantPage.exportHistory.empty')}</p>}
        </div>
        {loading && <p role="status" className="flex items-center justify-center gap-2 py-4 text-[12px] text-muted-foreground"><Loader2 aria-hidden className="h-4 w-4 animate-spin motion-reduce:animate-none" />{tr('assistantPage.exportHistory.loading')}</p>}
        {cursor != null && <button type="button" disabled={loading} onClick={() => { void load(cursor) }} className="mt-3 w-full rounded-lg border border-border py-2 text-[12px] disabled:opacity-50">{tr('assistantPage.exportHistory.more')}</button>}
      </>}
    </DialogContent>
  </Dialog>
}
