import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { ArrowDown, ArrowUp, FileText, GitBranch, Target } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useTranslation } from 'react-i18next'
import type { DeepAnalysisResult } from '@panwatch/api'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@panwatch/base-ui/components/ui/tabs'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { buildAnalysisSections } from '../analysis-sections'

/** Keep excerpts verbatim apart from Markdown formatting; never infer a stance or score. */
export function analysisExcerpt(markdown: string): string {
  const executive = /^\*\*(?:Executive Summary|摘要|核心结论)\*\*\s*[:：]\s*(.+)$/im.exec(markdown)?.[1]
  const paragraph = executive || markdown.split(/\n\s*\n/).find(block =>
    block.trim() && !/^(?:#{1,6}\s|[-*_]{3,}\s*$|\||```|~~~)/.test(block.trim())
    && !/^\*?\*?(?:Rating\b|评级|分析日期|报告日期|数据源)/i.test(block.trim()),
  ) || ''
  return paragraph.replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/[*_`#]/g, '').replace(/\s+/g, ' ').trim()
}

/** Headings/structured PM fields become readable chapters without rewriting their content. */
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
  const section = (id: string) => sections.find(item => item.id === id)
  const nodes = [
    ...['market', 'social', 'news', 'fundamentals'].map(id => ({ id, sectionId: id, title: tr(id), markdown: section(id)?.markdown || '' })),
    { id: 'debate', sectionId: 'debate', title: tr('debate'), markdown: raw.debate_history?.history || '' },
    { id: 'research', sectionId: 'debate', title: tr('research'), markdown: raw.debate_history?.judge_decision || '' },
    { id: 'trader', sectionId: 'decision', title: tr('trader'), markdown: raw.trader_plan || '' },
    { id: 'risk', sectionId: 'risk', title: tr('risk'), markdown: section('risk')?.markdown || raw.risk_judgment || '' },
    { id: 'decision', sectionId: 'decision', title: tr('decision'), markdown: raw.final_decision || result.content || '' },
  ]
  const [mode, setMode] = useState<'map' | 'document'>('map')
  const [selectedId, setSelectedId] = useState('decision')
  const selected = nodes.find(node => node.id === selectedId && node.markdown) || nodes.find(node => node.id === 'decision')!
  const readerId = useId()
  const graphRef = useRef<HTMLDivElement>(null)
  const readingRef = useRef<HTMLElement>(null)
  const documentRef = useRef<HTMLDivElement>(null)
  const nodeRefs = useRef<Record<string, HTMLButtonElement | null>>({})
  useEffect(() => {
    if (mode === 'document') documentRef.current?.querySelector(`#sec-${selected.sectionId}`)?.scrollIntoView({ block: 'start' })
  }, [mode, selected.sectionId])
  const changeMode = (value: string) => {
    const next = value === 'document' ? 'document' : 'map'
    setMode(next)
    onModeChange?.(next)
  }
  const chapters = analysisChapters(selected.markdown)
  const leadChapter = Math.max(0, chapters.findIndex(chapter => /Executive Summary|摘要|数据.*声明|可用性/i.test(chapter.title)))
  const groups = [nodes.slice(0, 4), nodes.slice(4, 6), nodes.slice(6, 8), nodes.slice(8)]
  const groupLabels = ['analysts', 'researchStage', 'execution', 'outcome']

  return (
    <Tabs value={mode} onValueChange={changeMode} className="min-w-0">
      <TabsList aria-label={tr('view')} className="gap-1 rounded-xl bg-muted/60 p-1 w-fit">
        <TabsTrigger value="map" className="gap-1.5 inline-flex items-center px-3 py-2 text-[12px]"><GitBranch className="h-3.5 w-3.5" aria-hidden="true" />{tr('map')}</TabsTrigger>
        <TabsTrigger value="document" className="gap-1.5 inline-flex items-center px-3 py-2 text-[12px]"><FileText className="h-3.5 w-3.5" aria-hidden="true" />{tr('document')}</TabsTrigger>
      </TabsList>
      <TabsContent value="map">
        <p className="mb-3 text-[12px] text-muted-foreground">{tr('hint')}</p>
        <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <div ref={graphRef} className="min-w-0 scroll-mt-4 rounded-xl border border-border/60 bg-muted/20 p-3" role="group" aria-label={tr('map')}>
            {groups.map((group, groupIndex) => (
              <div key={groupLabels[groupIndex]}>
                {groupIndex > 0 && <div className="flex h-5 items-center justify-center text-muted-foreground/60"><ArrowDown className="h-4 w-4" aria-hidden="true" /></div>}
                <div className="mb-2 flex items-center gap-2 text-[11px] font-medium text-muted-foreground"><span className="font-mono text-primary">0{groupIndex + 1}</span>{tr(groupLabels[groupIndex])}</div>
                <div className={`grid gap-2 ${group.length > 1 ? 'grid-cols-2' : 'grid-cols-1'}`}>
                  {group.map(node => (
                    <button
                      key={node.id}
                      ref={element => { nodeRefs.current[node.id] = element }}
                      type="button"
                      aria-label={node.title}
                      aria-pressed={selected.id === node.id}
                      aria-controls={`${readerId}-reading`}
                      disabled={!node.markdown.trim()}
                      onClick={() => {
                        setSelectedId(node.id)
                        if (window.innerWidth < 1024) readingRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                      }}
                      className={`min-w-0 rounded-lg border px-2.5 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default ${selected.id === node.id ? 'border-primary bg-primary/5 ring-1 ring-primary/20' : 'border-border/70 bg-card hover:border-primary/50 disabled:hover:border-border/70'}`}
                    >
                      <span className="flex items-center gap-1.5 text-[12px] font-semibold">{node.id === 'decision' && <Target className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />}{node.title}</span>
                      <span className="mt-1 line-clamp-1 text-[11px] leading-4 text-muted-foreground [overflow-wrap:anywhere]">{node.markdown.trim() ? analysisExcerpt(node.markdown).slice(0, 140) || tr('read') : tr('missing')}</span>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <section ref={readingRef} id={`${readerId}-reading`} aria-labelledby={`${readerId}-title`} className="min-w-0 scroll-mt-4 rounded-xl border border-border/60 bg-card">
            <div className="border-b border-border/50 p-4">
              <div className="mb-2 flex items-center justify-between gap-2">
                <span className="text-[11px] text-muted-foreground">{tr('excerpt')}</span>
                <Button size="sm" variant="ghost" className="h-7 text-[11px] lg:hidden" onClick={() => { graphRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }); nodeRefs.current[selected.id]?.focus({ preventScroll: true }) }}><ArrowUp className="mr-1 h-3 w-3" aria-hidden="true" />{tr('back')}</Button>
              </div>
              <h3 id={`${readerId}-title`} className="text-[15px] font-semibold">{selected.title}</h3>
            </div>
            <div className="space-y-2 p-3 lg:max-h-[62vh] lg:overflow-y-auto scrollbar overscroll-contain">
              {chapters.length ? chapters.map((chapter, index) => (
                <details key={`${selected.id}-${index}`} open={index === leadChapter || /数据.*声明|可用性/i.test(chapter.title)} className="group rounded-lg border border-border/50">
                  <summary className="cursor-pointer rounded-lg px-3 py-2.5 text-[12px] font-medium leading-relaxed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{chapter.title || tr('report')}</summary>
                  <div className="min-w-0 border-t border-border/40 px-3 py-2 prose prose-sm dark:prose-invert max-w-none [overflow-wrap:anywhere] prose-headings:text-[14px] prose-p:my-2 prose-table:block prose-table:overflow-x-auto prose-table:scrollbar prose-pre:whitespace-pre-wrap prose-strong:text-foreground">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{chapter.markdown}</ReactMarkdown>
                  </div>
                </details>
              )) : <p className="p-3 text-[12px] text-muted-foreground">{tr('missing')}</p>}
              <Button variant="ghost" size="sm" onClick={() => changeMode('document')} className="text-[12px]"><FileText className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />{tr('readOriginal')}</Button>
            </div>
          </section>
        </div>
      </TabsContent>
      <TabsContent value="document"><div ref={documentRef}>{documentView(selected.sectionId)}</div></TabsContent>
    </Tabs>
  )
}
