import { useTranslation } from 'react-i18next'
import type { AnalysisTokenUsage, DeepAnalysisResult } from '@panwatch/api'

export function analysisDateForResult(result: DeepAnalysisResult): string | undefined {
  const value = result.analysis_date || result.timestamp?.slice(0, 10)
  return value && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : undefined
}

export function AnalysisUsage({ usage }: { usage?: AnalysisTokenUsage | null }) {
  const { t, i18n } = useTranslation('bizUi')
  const tr = (key: string, options?: Record<string, unknown>) =>
    (t as unknown as (key: string, options?: Record<string, unknown>) => string)(`deepAnalysis.usage.${key}`, options)
  const number = new Intl.NumberFormat(i18n.resolvedLanguage || i18n.language)
  if (!usage || usage.recorded_calls <= 0) return <span className="text-muted-foreground">{tr('unavailable')}</span>
  return <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground" data-testid="analysis-token-usage">
    <span>{tr('total', { value: number.format(usage.total_tokens) })}</span>
    <span>{tr('input', { value: number.format(usage.input_tokens) })}</span>
    <span>{tr('output', { value: number.format(usage.output_tokens) })}</span>
    {!usage.complete && <span>{tr('partial', { recorded: usage.recorded_calls, total: usage.completed_calls })}</span>}
  </div>
}

export function AnalysisMetadata({ result }: { result: DeepAnalysisResult }) {
  const { t, i18n } = useTranslation('bizUi')
  const tr = (key: string, options?: Record<string, unknown>) =>
    (t as unknown as (key: string, options?: Record<string, unknown>) => string)(`deepAnalysis.done.${key}`, options)
  const analysisDate = analysisDateForResult(result)
  const generated = result.generated_at ? new Date(result.generated_at) : null
  const generatedAt = generated && !Number.isNaN(generated.getTime())
    ? new Intl.DateTimeFormat(i18n.resolvedLanguage || i18n.language, { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(generated)
    : undefined
  return <div className="space-y-1.5 text-[11px] leading-5 text-muted-foreground" data-testid="analysis-metadata">
    <div className="flex flex-wrap gap-x-4 gap-y-1">
      <span>{tr('analysisDate', { value: analysisDate || tr('dateUnavailable') })}</span>
      {generatedAt && <span>{tr('generatedAt', { value: generatedAt })}</span>}
    </div>
    <AnalysisUsage usage={result.raw_data?.token_usage} />
  </div>
}
