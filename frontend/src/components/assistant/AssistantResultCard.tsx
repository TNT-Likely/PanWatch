import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { useMemo, useState } from 'react'
import { Bell, ChevronDown, ExternalLink, FileSearch, ShieldAlert, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { AssistantNextAction, AssistantResult } from '@panwatch/api'

interface AssistantResultCardProps {
  result: AssistantResult
  disabled?: boolean
  onPrefill: (prompt: string) => void
  onNavigate: (path: string) => void
  onSubmitPrompt: (prompt: string) => void
}

const ALLOWED_PATHS = new Set(['/portfolio', '/opportunities', '/alerts'])

function safeInternalPath(value: string): string | null {
  try {
    const url = new URL(value, window.location.origin)
    if (url.origin !== window.location.origin || !ALLOWED_PATHS.has(url.pathname)) return null
    return `${url.pathname}${url.search}${url.hash}`
  } catch {
    return null
  }
}

function formatObservedAt(value: string | null | undefined, locale: string): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat(locale, {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date)
}

function safeExternalUrl(value: string | null | undefined): string | null {
  if (!value) return null
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : null
  } catch {
    return null
  }
}

export function AssistantResultCard({
  result,
  disabled = false,
  onPrefill,
  onNavigate,
  onSubmitPrompt,
}: AssistantResultCardProps) {
  const { t, i18n } = useTranslation('configuration')
  const resultT = t as unknown as (key: string, options?: Record<string, unknown>) => string
  const tr = (key: string, options?: Record<string, unknown>) => resultT(`assistantPage.result.${key}`, options)
  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const [alertAction, setAlertAction] = useState<AssistantNextAction | null>(null)
  const [direction, setDirection] = useState<'above' | 'below'>('below')
  const [targetPrice, setTargetPrice] = useState('')
  const evidenceById = useMemo(
    () => new Map(result.evidence.map((item) => [item.id, item])),
    [result.evidence],
  )
  const hasDetails = result.facts.length > 0
    || result.inferences.length > 0
    || result.risks.length > 0
    || result.missing_data.length > 0

  const handleAction = (action: AssistantNextAction) => {
    if (disabled) return
    if (action.kind === 'follow_up' && typeof action.payload.prompt === 'string') {
      onPrefill(action.payload.prompt)
      return
    }
    if (action.kind === 'navigate' && typeof action.payload.path === 'string') {
      const path = safeInternalPath(action.payload.path)
      if (path) {
        onNavigate(path)
        return
      }
    }
    if (action.kind === 'tool_proposal' && action.payload.tool_name === 'create_price_alert') {
      setAlertAction(action)
    }
  }

  const submitAlert = () => {
    if (!alertAction || disabled) return
    const price = Number(targetPrice)
    if (!Number.isFinite(price) || price <= 0) return
    const args = alertAction.payload.arguments || {}
    const market = String(args.market || 'CN').toUpperCase()
    const symbol = String(args.symbol || '').toUpperCase()
    if (!symbol) return
    const prompt = tr('alertPrompt', {
      target: `${market}:${symbol}`,
      direction: direction === 'above' ? tr('above') : tr('below'),
      price: String(price),
    })
    onSubmitPrompt(prompt)
    setAlertAction(null)
    setTargetPrice('')
  }

  if (!hasDetails && result.next_actions.length === 0) return null

  return (
    <section className="mt-2 rounded-xl border border-border/60 bg-background/70 px-3 py-2.5 text-[12px]">
      {result.facts.length > 0 && (
        <div>
          <div className="mb-1.5 flex items-center gap-1.5 font-medium text-foreground">
            <FileSearch className="h-3.5 w-3.5 text-primary" />
            {tr('facts')}
          </div>
          <ul className="space-y-1.5">
            {result.facts.slice(0, 4).map((fact, index) => {
              const sources = fact.evidence_ids
                .map((id) => evidenceById.get(id))
                .filter(Boolean)
              return (
                <li key={`${fact.text}-${index}`} className="leading-relaxed text-foreground/90">
                  <span className="mr-1 text-muted-foreground">•</span>{fact.text}
                  {sources.length > 0 && (
                    <span className="ml-1 text-[10px] text-muted-foreground">
                      [{sources.map((item) => item?.source_name).filter(Boolean).join(' · ')}]
                    </span>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {(result.inferences.length > 0 || result.risks.length > 0 || result.missing_data.length > 0) && (
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          {result.inferences.length > 0 && (
            <div className="rounded-lg bg-primary/5 p-2">
              <div className="mb-1 flex items-center gap-1 font-medium"><Sparkles className="h-3 w-3" />{tr('inferences')}</div>
              {result.inferences.slice(0, 3).map((item, index) => <div key={index} className="text-muted-foreground">• {item}</div>)}
            </div>
          )}
          {(result.risks.length > 0 || result.missing_data.length > 0) && (
            <div className="rounded-lg bg-amber-500/5 p-2">
              <div className="mb-1 flex items-center gap-1 font-medium"><ShieldAlert className="h-3 w-3" />{tr('risks')}</div>
              {[...result.risks, ...result.missing_data].slice(0, 3).map((item, index) => <div key={index} className="text-muted-foreground">• {item}</div>)}
            </div>
          )}
        </div>
      )}

      {result.evidence.length > 0 && (
        <div className="mt-2 border-t border-border/40 pt-2">
          <button
            type="button"
            className="flex w-full items-center gap-1.5 text-left font-medium text-muted-foreground hover:text-foreground"
            aria-expanded={evidenceOpen}
            onClick={() => setEvidenceOpen((value) => !value)}
          >
            <span>{tr('evidence', { count: result.evidence.length })}</span>
            <ChevronDown className={`h-3.5 w-3.5 transition-transform ${evidenceOpen ? 'rotate-180' : ''}`} />
          </button>
          {evidenceOpen && (
            <ul className="mt-2 space-y-1.5">
              {result.evidence.map((item) => {
                const sourceUrl = safeExternalUrl(item.source_url)
                return (
                  <li key={item.id} className="rounded-lg border border-border/40 px-2 py-1.5">
                    <div className="flex flex-wrap items-center gap-1.5">
                      {sourceUrl ? (
                        <a className="inline-flex items-center gap-1 font-medium text-primary hover:underline" href={sourceUrl} target="_blank" rel="noreferrer">
                          {item.source_name}<ExternalLink className="h-3 w-3" />
                        </a>
                      ) : <span className="font-medium">{item.source_name}</span>}
                      <span className={`rounded px-1.5 py-0.5 text-[9px] ${item.freshness === 'fresh' ? 'bg-emerald-500/10 text-emerald-600' : item.freshness === 'stale' ? 'bg-rose-500/10 text-rose-600' : 'bg-amber-500/10 text-amber-600'}`}>
                        {tr(`freshness.${item.freshness}`)}
                      </span>
                      {item.observed_at && <span className="text-[10px] text-muted-foreground">{tr('observedAt', { time: formatObservedAt(item.observed_at, i18n.language) })}</span>}
                      {item.data_at && <span className="text-[10px] text-muted-foreground">{tr('dataAt', { time: formatObservedAt(item.data_at, i18n.language) || item.data_at })}</span>}
                      {(item.period_start || item.period_end) && (
                        <span className="text-[10px] text-muted-foreground">
                          {tr('coverage', { start: item.period_start || '—', end: item.period_end || '—' })}
                        </span>
                      )}
                    </div>
                    <div className="mt-1 text-muted-foreground">{item.summary}</div>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      )}

      {result.next_actions.length > 0 && (
        <div className="mt-2 border-t border-border/40 pt-2">
          <div className="mb-1.5 font-medium text-muted-foreground">{tr('nextActions')}</div>
          <div className="flex flex-wrap gap-1.5">
            {result.next_actions.map((action) => (
              <button
                key={action.id}
                type="button"
                disabled={disabled}
                onClick={() => handleAction(action)}
                className="rounded-full border border-border/70 bg-background px-2.5 py-1 text-[11px] text-foreground hover:border-primary/50 hover:text-primary disabled:opacity-50"
              >
                {action.kind === 'tool_proposal' && <Bell className="mr-1 inline h-3 w-3" />}
                {action.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {alertAction && (
        <div className="mt-2 rounded-lg border border-primary/20 bg-primary/5 p-2">
          <div className="mb-2 font-medium">{tr('configureAlert')}</div>
          <div className="flex flex-wrap items-center gap-2">
            <Select value={direction} onValueChange={value => setDirection(value as 'above' | 'below')}>
              <SelectTrigger aria-label={tr('configureAlert')} className="h-8 w-auto min-w-[100px] text-[11px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="below">{tr('below')}</SelectItem>
                <SelectItem value="above">{tr('above')}</SelectItem>
              </SelectContent>
            </Select>
            <input
              type="number"
              min="0"
              step="any"
              value={targetPrice}
              onChange={(event) => setTargetPrice(event.target.value)}
              placeholder={tr('targetPrice')}
              className="h-8 min-w-0 flex-1 rounded-md border border-border bg-background px-2 text-[11px]"
            />
            <button type="button" disabled={disabled || !(Number(targetPrice) > 0)} onClick={submitAlert} className="h-8 rounded-md bg-primary px-3 text-[11px] text-primary-foreground disabled:opacity-50">
              {tr('requestApproval')}
            </button>
          </div>
        </div>
      )}
    </section>
  )
}
