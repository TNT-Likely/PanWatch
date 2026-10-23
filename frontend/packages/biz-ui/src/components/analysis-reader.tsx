import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { FileText, GitBranch, Maximize2, Minus, Plus } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useTranslation } from 'react-i18next'
import type { DeepAnalysisResult } from '@panwatch/api'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@panwatch/base-ui/components/ui/tabs'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import { buildAnalysisSections } from '../analysis-sections'

/** Preserve complete Markdown (including tables/fences) when identifying sections. */
export function analysisChapters(markdown: string): { title: string; markdown: string }[] {
  const chapters: { title: string; markdown: string }[] = []
  let current = { title: '', markdown: '' }
  let fence = ''
  for (const line of markdown.split('\n')) {
    const marker = /^\s*(`{3,}|~{3,})/.exec(line)?.[1]
    if (marker) fence = fence ? (marker[0] === fence ? '' : fence) : marker[0]
    const heading = !fence && !marker && (/^#{1,4}\s+(.+?)\s*#*$/.exec(line)
      || /^\*\*([^*\n]{1,80})\*\*\s*[:：]\s*(.*)$/.exec(line)
      || /^(Bull Analyst|Bear Analyst|Aggressive Analyst|Conservative Analyst|Neutral Analyst)\s*[:：]\s*(.*)$/.exec(line))
    if (heading) {
      if (current.markdown.trim()) chapters.push(current)
      current = { title: heading[1].replace(/[*`]/g, ''), markdown: heading[2] || '' }
    } else current.markdown += `${current.markdown ? '\n' : ''}${line}`
  }
  if (current.markdown.trim()) chapters.push(current)
  return chapters
}

/** Use saved conclusions when present; otherwise explicitly label a verbatim excerpt. */
export function analysisConclusion(markdown: string): { text: string; explicit: boolean } {
  const chapters = analysisChapters(markdown)
  const conclusion = chapters.find(chapter => /Executive Summary|摘要|核心结论|综合判断|综合评估|交易洞察|交易建议|交易行动|结论|结辩/i.test(chapter.title))
    || chapters.find(chapter => /^(?:Recommendation|推荐建议|Action|建议|评级|Rating|Overall Sentiment)$/i.test(chapter.title))
  const candidate = conclusion?.markdown || markdown
  const paragraph = candidate.split(/\n\s*\n/).find(block => block.trim()
    && !/^(?:#{1,6}\s|[-*_]{3,}\s*$|\||```|~~~)/.test(block.trim())
    && !/^\*?\*?(?:分析日期|报告日期|数据源)/i.test(block.trim())) || ''
  return { text: paragraph.replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/[*_`#]/g, '').replace(/\s+/g, ' ').trim(), explicit: !!conclusion }
}

/** Preserve every labelled turn for a role, never attribute an unlabelled transcript. */
export function analysisTurns(history: string, role: string): string {
  const turns: string[] = []
  let speaker = '', content = '', fence = ''
  for (const line of history.split('\n')) {
    const marker = /^\s*(`{3,}|~{3,})/.exec(line)?.[1]
    if (marker) fence = fence ? (marker[0] === fence ? '' : fence) : marker[0]
    const heading = !fence && !marker && /^(?:#{1,4}\s+)?(?:\*\*)?(Bull Analyst|Bear Analyst|Aggressive Analyst|Conservative Analyst|Neutral Analyst)(?:\*\*)?\s*[:：]\s*(.*)$/.exec(line)
    if (heading) {
      if (speaker === role && content.trim()) turns.push(content.trim())
      speaker = heading[1]; content = heading[2]
    } else content += `\n${line}`
  }
  if (speaker === role && content.trim()) turns.push(content.trim())
  return turns.join('\n\n---\n\n')
}

export function AnalysisReader({ result, documentView, onModeChange }: {
  result: DeepAnalysisResult
  documentView: (sectionId: string) => ReactNode
  onModeChange?: (mode: 'map' | 'document') => void
}) {
  const { t, i18n } = useTranslation('bizUi')
  const tr = (key: string) => (t as unknown as (key: string) => string)(`analysisReader.${key}`)
  const english = (i18n.resolvedLanguage || i18n.language).toLowerCase().startsWith('en')
  const raw: Partial<DeepAnalysisResult['raw_data']> = result.raw_data || {}
  const sections = buildAnalysisSections(raw, { english })
  const node = (id: string, sectionId: string, markdown = '') => ({ id, sectionId, title: tr(id), markdown })
  const debate = raw.debate_history?.history || ''
  const risk = raw.risk_debate?.history || ''
  const researchRoles = ['bull', 'bear'].map((id, i) => node(id, 'debate', analysisTurns(debate, ['Bull Analyst', 'Bear Analyst'][i])))
  const riskRoles = ['aggressive', 'conservative', 'neutral'].map((id, i) => node(id, 'risk', analysisTurns(risk, ['Aggressive Analyst', 'Conservative Analyst', 'Neutral Analyst'][i])))
  const layers = [
    ['market', 'social', 'news', 'fundamentals'].map(id => node(id, id, sections.find(section => section.id === id)?.markdown)),
    researchRoles.some(item => item.markdown) || !debate ? researchRoles : [node('debate', 'debate', debate)],
    [node('research', 'debate', raw.debate_history?.judge_decision)],
    [node('trader', 'decision', raw.trader_plan)],
    riskRoles.some(item => item.markdown) || !risk ? riskRoles : [node('riskDebate', 'risk', risk)],
    [node('risk', 'risk', raw.risk_debate?.judge_decision || raw.risk_judgment)],
    [node('decision', 'decision', raw.final_decision || result.content)],
  ]
  const [mode, setMode] = useState<'map' | 'document'>('document')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [originalSection, setOriginalSection] = useState('decision')
  const [zoom, setZoom] = useState(0.9)
  const selected = layers.flat().find(item => item.id === selectedId)
  const readerId = useId().replace(/:/g, '')
  const viewportRef = useRef<HTMLDivElement>(null)
  const documentRef = useRef<HTMLDivElement>(null)
  const documentTabRef = useRef<HTMLButtonElement>(null)
  const navigateOriginal = useRef(false)
  const focusOriginal = useRef(false)
  const changeMode = (value: string) => {
    const next = value === 'map' ? 'map' : 'document'
    setMode(next)
    onModeChange?.(next)
  }
  useEffect(() => {
    if (mode === 'document' && navigateOriginal.current) {
      documentRef.current?.querySelector(`#sec-${originalSection}`)?.scrollIntoView({ block: 'start' })
      navigateOriginal.current = false
    }
    if (mode === 'map' && viewportRef.current) viewportRef.current.scrollLeft = (960 * zoom - viewportRef.current.clientWidth) / 2
  }, [mode, originalSection, zoom])

  const width = 960, nodeWidth = 216, nodeHeight = 136, rowGap = 58, top = 82
  const positioned = layers.map((layer, row) => layer.map((item, column) => ({ ...item, x: width / 2 + (column - (layer.length - 1) / 2) * (nodeWidth + 20) - nodeWidth / 2, y: top + row * (nodeHeight + rowGap) })))
  const endY = top + layers.length * (nodeHeight + rowGap) - rowGap + 44
  const edges = positioned.flatMap((layer, row) => {
    const sources = row ? positioned[row - 1].map(item => ({ x: item.x + nodeWidth / 2, y: item.y + nodeHeight })) : [{ x: width / 2, y: 40 }]
    return sources.flatMap(source => layer.map(target => {
      const x = target.x + nodeWidth / 2, mid = (source.y + target.y) / 2
      return `M ${source.x} ${source.y} C ${source.x} ${mid}, ${x} ${mid}, ${x} ${target.y - 4}`
    }))
  })
  edges.push(`M ${width / 2} ${endY - 44} L ${width / 2} ${endY - 4}`)

  return (
    <Tabs value={mode} onValueChange={changeMode} className="min-w-0">
      <TabsList aria-label={tr('view')} className="gap-1 rounded-xl bg-muted/60 p-1 w-fit">
        <TabsTrigger ref={documentTabRef} value="document" className="gap-1.5 inline-flex items-center px-3 py-2 text-[12px]"><FileText className="h-3.5 w-3.5" aria-hidden="true" />{tr('document')}</TabsTrigger>
        <TabsTrigger value="map" className="gap-1.5 inline-flex items-center px-3 py-2 text-[12px]"><GitBranch className="h-3.5 w-3.5" aria-hidden="true" />{tr('map')}</TabsTrigger>
      </TabsList>
      <TabsContent value="document"><div ref={documentRef}>{documentView(originalSection)}</div></TabsContent>
      <TabsContent value="map">
        <p className="mb-3 text-[12px] text-muted-foreground">{tr('hint')}</p>
        <div className="overflow-hidden rounded-xl border border-border/60 bg-muted/20">
          <div className="flex items-center justify-end gap-1 border-b border-border/50 bg-card p-2">
            <Button size="sm" variant="ghost" aria-label={tr('zoomOut')} disabled={zoom <= 0.3} onClick={() => setZoom(value => Math.max(0.3, value - 0.1))}><Minus className="h-3.5 w-3.5" aria-hidden="true" /></Button>
            <output className="min-w-12 text-center text-[12px] tabular-nums" aria-label={tr('zoom')}>{Math.round(zoom * 100)}%</output>
            <Button size="sm" variant="ghost" aria-label={tr('zoomIn')} disabled={zoom >= 1.3} onClick={() => setZoom(value => Math.min(1.3, value + 0.1))}><Plus className="h-3.5 w-3.5" aria-hidden="true" /></Button>
            <Button size="sm" variant="ghost" className="gap-1.5 text-[12px]" onClick={() => setZoom(Math.max(0.3, Math.min(1.3, ((viewportRef.current?.clientWidth || 960) - 16) / width)))}><Maximize2 className="h-3.5 w-3.5" aria-hidden="true" />{tr('fit')}</Button>
          </div>
          <div ref={viewportRef} role="region" aria-label={tr('map')} tabIndex={0} className="max-h-[70vh] overflow-auto scrollbar overscroll-contain focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" style={{ backgroundImage: 'radial-gradient(hsl(var(--border)) 1px, transparent 1px)', backgroundSize: '18px 18px' }}>
            <div className="relative mx-auto" style={{ width: width * zoom, height: (endY + 50) * zoom }}>
              <div className="absolute left-0 top-0 origin-top-left" style={{ width, height: endY + 50, transform: `scale(${zoom})` }}>
                <svg width={width} height={endY + 50} className="pointer-events-none absolute inset-0 text-muted-foreground/50" aria-hidden="true">
                  <defs><marker id={`${readerId}-arrow`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" /></marker></defs>
                  {edges.map((path, index) => <path key={index} d={path} fill="none" stroke="currentColor" strokeWidth="1.5" markerEnd={`url(#${readerId}-arrow)`} />)}
                </svg>
                {[{ title: tr('start'), y: 8 }, { title: tr('end'), y: endY }].map(item => <div key={item.title} className="absolute rounded-full border border-border bg-card px-4 py-1.5 text-[12px] font-mono" style={{ left: width / 2, top: item.y, transform: 'translateX(-50%)' }}>{item.title}</div>)}
                {positioned.flat().map(item => {
                  const summary = analysisConclusion(item.markdown)
                  return <button key={item.id} type="button" aria-label={item.title} aria-haspopup="dialog" disabled={!item.markdown.trim()} onClick={() => setSelectedId(item.id)} className="absolute flex flex-col rounded-xl border border-border bg-card p-3 text-left shadow-sm transition-colors hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default disabled:opacity-60" style={{ left: item.x, top: item.y, width: nodeWidth, height: nodeHeight }}>
                    <span aria-hidden="true" className="absolute -top-1 left-1/2 h-2 w-2 -translate-x-1/2 rounded-full border border-border bg-card" />
                    <span className="text-[13px] font-semibold">{item.title}</span>
                    <span className="mt-1 text-[10px] text-muted-foreground">{tr(summary.explicit ? 'conclusion' : 'excerpt')}</span>
                    <span className="mt-1 line-clamp-3 text-[12px] leading-[18px] text-foreground/80 [overflow-wrap:anywhere]">{item.markdown.trim() ? summary.text || tr('read') : tr('missing')}</span>
                    <span aria-hidden="true" className="absolute -bottom-1 left-1/2 h-2 w-2 -translate-x-1/2 rounded-full border border-border bg-card" />
                  </button>
                })}
              </div>
            </div>
          </div>
        </div>
        <Dialog open={!!selected} onOpenChange={open => { if (!open) setSelectedId(null) }}>
          <DialogContent className="w-[94vw] max-w-4xl flex max-h-[90vh] flex-col overflow-hidden p-0" onCloseAutoFocus={event => { if (focusOriginal.current) { event.preventDefault(); documentTabRef.current?.focus(); focusOriginal.current = false } }}>
            <DialogHeader className="mb-0 shrink-0 border-b border-border/50 p-4 pr-14">
              <DialogTitle>{selected?.title}</DialogTitle>
              <DialogDescription>{tr('detailHint')}</DialogDescription>
            </DialogHeader>
            <div className="min-h-0 overflow-y-auto scrollbar overscroll-contain p-4 prose prose-sm dark:prose-invert max-w-none [overflow-wrap:anywhere] prose-table:block prose-table:overflow-x-auto prose-table:scrollbar prose-pre:whitespace-pre-wrap prose-strong:text-foreground">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{selected?.markdown || ''}</ReactMarkdown>
            </div>
            <div className="shrink-0 border-t border-border/50 p-3"><Button variant="ghost" size="sm" onClick={() => { if (!selected) return; setOriginalSection(selected.sectionId); navigateOriginal.current = true; focusOriginal.current = true; setSelectedId(null); changeMode('document') }} className="text-[12px]"><FileText className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />{tr('readOriginal')}</Button></div>
          </DialogContent>
        </Dialog>
      </TabsContent>
    </Tabs>
  )
}
