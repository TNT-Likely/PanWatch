import ReactMarkdown from 'react-markdown'
import { useTranslation } from 'react-i18next'

export interface ResearchReportData {
  version: number
  content: string
  process: { title: string; content: string }[]
  evidence: { title: string; content: string }[]
  models: string[]
  usage: { attempts?: number; tools?: number; input_tokens?: number; output_tokens?: number }
}

const markdownClass = 'prose prose-sm dark:prose-invert max-w-none break-words [&_h2]:border-b [&_h2]:border-border/50 [&_h2]:pb-2 [&_h3]:text-base [&_p]:leading-7 [&_li]:leading-7 [&_code]:break-all'

export function ResearchReport({ content, report }: { content: string; report?: ResearchReportData | null }) {
  const { t } = useTranslation('configuration')
  return (
    <div className="min-w-0 space-y-4" data-testid="research-report">
      <div className={markdownClass}><ReactMarkdown>{report?.content || content}</ReactMarkdown></div>
      {report && (
        <div className="not-prose space-y-2 border-t border-border/50 pt-4 text-sm">
          {(['process', 'evidence'] as const).map(section => (
            <details key={section} className="rounded-lg border border-border/60 bg-background/60 p-3">
              <summary className="cursor-pointer rounded font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
                {section === 'process' ? t('p4.history.researchProcess', { count: report.process.length }) : t('p4.history.evidenceSources', { count: report.evidence.length })}
              </summary>
              <div className="mt-3 space-y-2">
                {report[section].map((item, index) => (
                  <details key={index} className="rounded-md border border-border/50 p-3">
                    <summary className="cursor-pointer break-words text-[13px] font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">{item.title}</summary>
                    <div className={`mt-2 ${markdownClass}`}><ReactMarkdown>{item.content}</ReactMarkdown></div>
                  </details>
                ))}
              </div>
            </details>
          ))}
          <details className="rounded-lg border border-border/60 bg-background/60 p-3">
            <summary className="cursor-pointer rounded font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">{t('p4.history.runInformation')}</summary>
            <dl className="mt-3 grid grid-cols-1 gap-2 text-xs text-muted-foreground sm:grid-cols-2">
              <div><dt>{t('p4.history.models')}</dt><dd className="break-words">{report.models.join(', ') || '—'}</dd></div>
              <div><dt>{t('p4.history.modelCalls')}</dt><dd>{report.usage.attempts ?? '—'}</dd></div>
              <div><dt>{t('p4.history.toolReads')}</dt><dd>{report.usage.tools ?? '—'}</dd></div>
              <div><dt>{t('p4.history.tokenUsage')}</dt><dd>{report.usage.input_tokens ?? '—'} / {report.usage.output_tokens ?? '—'}</dd></div>
            </dl>
          </details>
        </div>
      )}
    </div>
  )
}
