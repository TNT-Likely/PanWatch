import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Popover, PopoverAnchor, PopoverContent } from '@panwatch/base-ui/components/ui/popover'

interface DiagnosticItem {
  action?: string
  method?: string
  symbol?: string
  chars?: number
  snippet?: string
  source?: string
  reason?: string
}
interface DiagnosticSummary {
  hit: number
  miss: number
  passthrough: number
  fallthrough?: number
  error: number
}
const ACTION_CLS: Record<string, string> = {
  HIT: 'text-emerald-600 dark:text-emerald-400',
  MISS: 'text-amber-600 dark:text-amber-400',
  PASSTHROUGH: 'text-sky-600 dark:text-sky-400',
  FALLTHROUGH: 'text-orange-600 dark:text-orange-400',
  ERROR: 'text-rose-600 dark:text-rose-400',
}

export function ToolkitDiagnostics({ summary, recent }: { summary: DiagnosticSummary | undefined; recent: DiagnosticItem[] }) {
  const { t } = useTranslation('bizUi')
  const tr = (key: string, options?: Record<string, unknown>) =>
    (t as unknown as (key: string, options?: Record<string, unknown>) => string)(`deepAnalysis.toolkit.${key}`, options)
  const [hovered, setHovered] = useState<number | null>(null)
  const [selected, setSelected] = useState<DiagnosticItem | null>(null)
  const [allOpen, setAllOpen] = useState(false)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const clearTimer = () => { if (closeTimer.current) { clearTimeout(closeTimer.current); closeTimer.current = null } }
  useEffect(() => () => { if (closeTimer.current) clearTimeout(closeTimer.current) }, [])
  if (!summary && recent.length === 0) return null
  const closePreview = () => { clearTimer(); setHovered(null) }
  const leavePreview = () => { clearTimer(); closeTimer.current = setTimeout(() => setHovered(null), 120) }
  const openAll = () => { closePreview(); setSelected(null); setAllOpen(true) }
  const selectedItem = selected
  const statusClass = (item: DiagnosticItem) => ACTION_CLS[(item.action || '').toUpperCase()] || 'text-muted-foreground'
  const metadata = (item: DiagnosticItem) => <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 font-mono text-[11px] [overflow-wrap:anywhere]">
    <span className={`font-semibold ${statusClass(item)}`}>{item.action}</span>
    <span>{item.method} ({item.symbol || '-'})</span>
    <span className="text-muted-foreground">{item.source}{item.chars != null && tr('chars', { count: item.chars })}</span>
  </div>
  const details = (item: DiagnosticItem) => <>
    {metadata(item)}
    {item.reason && <p className="mt-2 text-[11px] text-amber-600 dark:text-amber-400 [overflow-wrap:anywhere]">{item.reason}</p>}
    {item.snippet ? <pre tabIndex={0} className="mt-2 whitespace-pre-wrap break-words rounded bg-muted/50 p-3 font-mono text-[11px] leading-relaxed text-foreground [overflow-wrap:anywhere] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
      {item.snippet}
      {item.chars != null && item.chars > item.snippet.length && <span className="text-muted-foreground">{'\n\n'}{tr('truncated', { total: item.chars, shown: item.snippet.length })}</span>}
    </pre> : <p className="mt-2 text-[12px] text-muted-foreground">{tr('noContent')}</p>}
  </>

  return <section aria-label={tr('title')} className="rounded-xl border border-border/50 bg-muted/20 p-3 text-[12px]">
    <div className="flex flex-wrap items-center gap-2">
      <h4 className="font-medium">{tr('title')}</h4>
      <span className="text-[11px] text-muted-foreground">{tr('subtitle')}</span>
      <Button variant="ghost" size="sm" className="ml-auto h-7 text-[11px]" disabled={!recent.length} onClick={openAll}>{tr('viewAll')}</Button>
    </div>
    <div className="mt-1 text-[11px]">
      <span className={ACTION_CLS.HIT}>HIT {summary?.hit ?? 0}</span>
      <span className="text-muted-foreground"> · MISS {summary?.miss ?? 0}</span>
      <span className={ACTION_CLS.PASSTHROUGH}> · {tr('passthrough')} {summary?.passthrough ?? 0}</span>
      {!!summary?.fallthrough && <span className={ACTION_CLS.FALLTHROUGH}> · {tr('fallback')} {summary.fallthrough}</span>}
      {!!summary?.error && <span className={ACTION_CLS.ERROR}> · {tr('error')} {summary.error}</span>}
    </div>
    <p className="mt-2 text-[10.5px] leading-relaxed text-muted-foreground">{tr('interactionHint')}</p>
    {recent.length === 0 ? <p className="mt-2 text-[11px] text-muted-foreground">{tr('empty')}</p> : <div role="region" aria-label={tr('records')} className="mt-2 max-h-52 space-y-1 overflow-y-auto scrollbar overscroll-contain" onScroll={closePreview}>
      {recent.map((item, index) => <Popover key={index} open={hovered === index && selected === null && !allOpen} onOpenChange={open => { if (!open && hovered === index) closePreview() }}>
        <PopoverAnchor asChild>
          <button type="button" aria-haspopup="dialog" onPointerEnter={event => { if (event.pointerType !== 'touch') { clearTimer(); setHovered(index) } }} onPointerLeave={leavePreview} onClick={() => { closePreview(); setSelected(item) }} className="flex w-full min-w-0 items-center gap-2 rounded-md border border-border/40 bg-card px-2 py-2 text-left font-mono text-[11px] hover:border-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <span className={`w-20 shrink-0 ${statusClass(item)}`}>{item.action}</span>
            <span className="min-w-0 flex-1 truncate">{item.method} ({item.symbol || '-'})</span>
            <span className="max-w-28 truncate text-muted-foreground">{item.source}</span>
          </button>
        </PopoverAnchor>
        {hovered === index && selected === null && !allOpen && <PopoverContent role="region" aria-label={tr('preview')} side="top" align="start" className="z-[60] w-[28rem] max-w-[calc(100vw-2rem)] bg-card p-3" onOpenAutoFocus={event => event.preventDefault()} onCloseAutoFocus={event => event.preventDefault()} onPointerEnter={() => { clearTimer(); setHovered(index) }} onPointerLeave={leavePreview} onEscapeKeyDown={event => { event.preventDefault(); closePreview() }}>
          <div className="max-h-60 overflow-y-auto scrollbar overscroll-contain">{details(item)}</div>
        </PopoverContent>}
      </Popover>)}
    </div>}
    <Dialog open={selectedItem !== null || allOpen} onOpenChange={open => { if (!open) { setSelected(null); setAllOpen(false); closePreview() } }}>
      <DialogContent className={`w-[94vw] flex max-h-[90vh] flex-col overflow-hidden p-0 ${allOpen ? 'max-w-6xl' : 'max-w-3xl'}`}>
        <DialogHeader className="mb-0 shrink-0 border-b border-border/50 p-4 pr-14">
          <DialogTitle>{allOpen ? tr('allTitle', { count: recent.length }) : tr('detailTitle', { method: selectedItem?.method || '-' })}</DialogTitle>
          <DialogDescription>{tr('savedDescription')}</DialogDescription>
        </DialogHeader>
        <div role="region" aria-label={tr('contents')} tabIndex={0} className="min-h-0 space-y-3 overflow-y-auto scrollbar overscroll-contain p-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          {(allOpen ? recent : selectedItem ? [selectedItem] : []).map((item, index) => <article key={index} className="min-w-0 rounded-lg border border-border/50 bg-card p-3">{details(item)}</article>)}
        </div>
        {!allOpen && <div className="shrink-0 border-t border-border/50 p-3"><Button variant="ghost" size="sm" onClick={openAll}>{tr('viewAll')}</Button></div>}
      </DialogContent>
    </Dialog>
  </section>
}
