import ReactMarkdown from 'react-markdown'
import { useTranslation } from 'react-i18next'
import { Clock, Cpu, Wrench, ArrowDown, ArrowUp, RotateCcw } from 'lucide-react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@panwatch/base-ui/components/ui/tabs'
import { ResearchProcessGraph } from './ResearchProcessGraph'
import { ResearchEvidenceBoard } from './ResearchEvidenceBoard'
import { AiSuggestionBadge } from '@panwatch/biz-ui/components/ai-suggestion-badge'
import type { SuggestionStateInput } from '@panwatch/biz-ui/components/suggestion-action'

export interface ProcessItem {
  id?: string; role?: string; group?: string; epoch?: number; verdict?: string; active?: boolean
  kind?: 'model' | 'rule'; feedback_roles?: string[]; title: string; content: string
}
export interface EvidenceItem {
  id?: string; stock_key?: string; stock_name?: string; domain?: string; domain_label?: string
  providers?: string[]; captured_at?: string; summary?: string; title: string; content: string
  items?: { title: string; source: string; time: string; url: string }[]
}

export interface ResearchReportData {
  version: number
  content: string
  body?: string
  actions?: (SuggestionStateInput & { stock_key: string; stock_name: string; triggers: string[]; invalidations: string[] })[]
  process: ProcessItem[]
  evidence: EvidenceItem[]
  models: string[]
  usage: { attempts?: number; tools?: number; input_tokens?: number; output_tokens?: number; feedback?: number; unknown_usage?: number }
  runtime?: { graph?: string; completion: string; as_of: string; stock_count: number; elapsed_seconds: number | null }
}

export const markdownClass = 'prose prose-sm dark:prose-invert max-w-none break-words text-[13px] md:text-sm [&_h2]:border-b [&_h2]:border-border/50 [&_h2]:pb-2 [&_h2]:mt-5 [&_h2]:mb-3 [&_h3]:text-base [&_h3]:mt-4 [&_h3]:mb-2 [&_p]:my-2 [&_p]:leading-6 [&_li]:my-1 [&_li]:leading-6 [&_ul]:my-2 [&_code]:break-all'

function ActionSummary({ report }: { report: ResearchReportData }) {
  const { t } = useTranslation('configuration')
  return <section aria-label={t('p4.history.actionSummary')} className="space-y-3 rounded-xl border-2 border-primary/20 bg-primary/5 p-3 md:p-4">
    <h2 className="text-base font-semibold">{t('p4.history.actionSummary')}</h2>
    <p className="text-xs text-muted-foreground">{report.runtime?.as_of} · {t('p4.history.conditionalNotice')}</p>
    {report.actions?.map(item => <article key={item.stock_key} className="min-w-0 space-y-2 rounded-lg border border-border/60 bg-card p-3">
      <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold">{item.stock_name}<span className="ml-2 text-xs font-normal text-muted-foreground">{item.stock_key}</span></h3><AiSuggestionBadge {...item} size="lg" /></div>
      <p className="text-xs text-muted-foreground">{t(`p4.history.outlooks.${item.outlook}`, { defaultValue: item.outlook || '' })}{item.confidence != null && ` · ${Math.round(item.confidence * 100)}%`}</p>
      <div className="text-sm leading-6"><span className="font-medium">{t('p4.history.actionTriggers')}：</span>{item.triggers.length ? item.triggers.join('；') : t('p4.history.noTriggers')}</div>
      {item.invalidations.length > 0 && <p className="text-xs leading-5 text-muted-foreground">{t('p4.history.actionInvalidations')}：{item.invalidations.join('；')}</p>}
      {item.execution_status === 'blocked' ? <p className="break-words text-xs leading-5 text-muted-foreground">{t('p4.history.orderRestrictions')}：{item.execution_constraints?.map(value => t(`p4.history.constraints.${value}`, { defaultValue: value })).join('；')}</p> : <p className="text-xs text-muted-foreground">{t(item.execution_status === 'not_applicable' ? 'p4.history.noOrder' : 'p4.history.confirmBeforeOrder')}</p>}
    </article>)}
  </section>
}

function RunOverview({ report }: { report: ResearchReportData }) {
  const { t, i18n } = useTranslation('configuration')
  const format = (value?: number | null) => value == null ? '—' : new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 1 }).format(value)
  const unknown = (report.usage.unknown_usage ?? 0) > 0
  const metrics = [
    [Cpu, t('p4.history.modelCalls'), format(report.usage.attempts)],
    [Wrench, t('p4.history.toolReads'), format(report.usage.tools)],
    [ArrowDown, t('p4.history.inputTokens'), unknown && !report.usage.input_tokens ? '—' : format(report.usage.input_tokens)],
    [ArrowUp, t('p4.history.outputTokens'), unknown && !report.usage.output_tokens ? '—' : format(report.usage.output_tokens)],
    [Clock, t('p4.history.elapsed'), report.runtime?.elapsed_seconds == null ? '—' : t('p4.history.seconds', { value: format(report.runtime.elapsed_seconds) })],
    [RotateCcw, t('p4.history.feedback'), format(report.usage.feedback)],
  ] as const
  return <section className="space-y-4" aria-label={t('p4.history.runInformation')}>
    <div className="rounded-xl border border-border/60 bg-background/60 p-4">
      <p className="font-medium">{report.runtime ? t(`p4.history.completion.${report.runtime.completion}`, { defaultValue: report.runtime.completion }) : t('p4.history.recordedRun')}</p>
      {report.runtime && <p className="mt-1 text-xs text-muted-foreground">{report.runtime.as_of} · {t('p4.history.coveredStocks', { count: report.runtime.stock_count })}</p>}
      <div className="mt-3 flex flex-wrap gap-2">{report.models.map(model => <span key={model} className="rounded-md bg-accent px-2 py-1 text-xs break-all">{model}</span>)}</div>
    </div>
    <dl className="grid grid-cols-2 gap-3 lg:grid-cols-3">{metrics.map(([Icon, label, value]) => <div key={label} className="min-w-0 rounded-xl border border-border/60 bg-background/60 p-3">
      <dt className="flex items-center gap-1.5 text-xs text-muted-foreground"><Icon aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />{label}</dt>
      <dd className="mt-2 break-words text-xl font-semibold tabular-nums">{value}</dd>
    </div>)}</dl>
    <p className="text-xs text-muted-foreground">{t(unknown ? 'p4.history.partialUsage' : 'p4.history.usageNote', { count: report.usage.unknown_usage ?? 0 })}</p>
  </section>
}

export function ResearchReport({ content, report }: { content: string; report?: ResearchReportData | null }) {
  const { t } = useTranslation('configuration')
  return (
    <div className="min-w-0 space-y-4" data-testid="research-report">
      {!!report?.actions?.length && <ActionSummary report={report} />}
      <div className={`${markdownClass} [&>p:first-child]:text-xs [&>p:first-child]:text-muted-foreground`}><ReactMarkdown>{report?.actions?.length ? report.body || report.content : report?.content || content}</ReactMarkdown></div>
      {report && (
        <Tabs defaultValue="process" className="not-prose min-w-0 border-t border-border/50 pt-4 text-sm">
          <TabsList className="grid h-auto w-full grid-cols-3">
            <TabsTrigger value="process" className="px-1 text-xs">{t('p4.history.processTab')}</TabsTrigger>
            <TabsTrigger value="evidence" className="px-1 text-xs">{t('p4.history.evidenceTab')}</TabsTrigger>
            <TabsTrigger value="runtime" className="px-1 text-xs">{t('p4.history.runInformation')}</TabsTrigger>
          </TabsList>
          <TabsContent value="process"><ResearchProcessGraph items={report.process} /></TabsContent>
          <TabsContent value="evidence"><ResearchEvidenceBoard items={report.evidence} /></TabsContent>
          <TabsContent value="runtime"><RunOverview report={report} /></TabsContent>
        </Tabs>
      )}
    </div>
  )
}
