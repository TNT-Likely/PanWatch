import { useEffect, useState } from 'react'
import { Check, Cpu } from 'lucide-react'
import { chatApi, type AssistantConfig, type AssistantConfigUpdate } from '@panwatch/api'
import { useTranslation } from 'react-i18next'

interface AssistantConfigForm {
  compression_model_id: string
  compression_temperature: string
  summary_max_tokens: string
  max_tokens: string
  soft_limit_tokens: string
  hard_limit_tokens: string
  keep_recent_messages: string
}

const EMPTY_FORM: AssistantConfigForm = {
  compression_model_id: '',
  compression_temperature: '0.1',
  summary_max_tokens: '800',
  max_tokens: '12000',
  soft_limit_tokens: '8400',
  hard_limit_tokens: '10200',
  keep_recent_messages: '8',
}

function toForm(config: AssistantConfig): AssistantConfigForm {
  return {
    compression_model_id: config.compression_model_id?.toString() || '',
    compression_temperature: config.compression_temperature.toString(),
    summary_max_tokens: config.summary_max_tokens.toString(),
    max_tokens: config.max_tokens.toString(),
    soft_limit_tokens: config.soft_limit_tokens.toString(),
    hard_limit_tokens: config.hard_limit_tokens.toString(),
    keep_recent_messages: config.keep_recent_messages.toString(),
  }
}

function toPayload(form: AssistantConfigForm): AssistantConfigUpdate {
  return {
    compression_model_id: form.compression_model_id ? Number(form.compression_model_id) : null,
    compression_temperature: Number(form.compression_temperature),
    summary_max_tokens: Number(form.summary_max_tokens),
    max_tokens: Number(form.max_tokens),
    soft_limit_tokens: Number(form.soft_limit_tokens),
    hard_limit_tokens: Number(form.hard_limit_tokens),
    keep_recent_messages: Number(form.keep_recent_messages),
  }
}

export function AssistantConfigPanel() {
  const { t } = useTranslation('configuration')
  const assistantT = t as unknown as (key: string) => string
  const tr = (key: string) => assistantT(`p4.components.assistantConfig.${key}`)
  const [config, setConfig] = useState<AssistantConfig | null>(null)
  const [form, setForm] = useState<AssistantConfigForm>(EMPTY_FORM)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    let active = true
    chatApi.getAssistantConfig()
      .then((next) => {
        if (!active) return
        setConfig(next)
        setForm(toForm(next))
      })
      .catch(() => {
        if (active) setError(tr('loadFailed'))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => { active = false }
  }, [])

  const update = (key: keyof AssistantConfigForm, value: string) => {
    setSaved(false)
    setForm((previous) => ({ ...previous, [key]: value }))
  }

  const save = async () => {
    setSaving(true)
    setError('')
    setSaved(false)
    try {
      const next = await chatApi.updateAssistantConfig(toPayload(form))
      setConfig(next)
      setForm(toForm(next))
      setSaved(true)
    } catch {
      setError(tr('saveFailed'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section aria-label={tr('aria')} className="border-t border-border/50 pt-5">
      <div className="mb-3 flex items-start gap-2">
        <Cpu className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div>
          <h3 className="text-[13px] font-semibold text-foreground">{tr('title')}</h3>
          <p className="mt-1 text-[11px] text-muted-foreground">{tr('description')}</p>
        </div>
      </div>

      {error && <p className="mb-3 rounded-lg bg-destructive/10 px-3 py-2 text-[11px] text-destructive">{error}</p>}
      {loading ? (
        <div className="flex items-center gap-2 py-6 text-[12px] text-muted-foreground">
          <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current/30 border-t-current" />
          {tr('loading')}
        </div>
      ) : config ? (
        <div className="space-y-3">
          <label className="block text-[11px] text-muted-foreground">
            <span className="mb-1 block">{tr('model')}</span>
            <select
              aria-label={tr('model')}
              value={form.compression_model_id}
              onChange={(event) => update('compression_model_id', event.target.value)}
              className="h-9 w-full rounded-lg border border-border/60 bg-background px-2 text-[12px] text-foreground outline-none focus:ring-1 focus:ring-primary/30"
            >
              <option value="">{tr('defaultModel')}</option>
              {config.models.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.service_name} / {model.name} ({model.model})
                </option>
              ))}
            </select>
          </label>

          <label className="block text-[11px] text-muted-foreground">
            <span className="mb-1 block">{tr('temperature')}</span>
            <input
              aria-label={tr('temperature')}
              type="number"
              min="0"
              max="2"
              step="0.1"
              value={form.compression_temperature}
              onChange={(event) => update('compression_temperature', event.target.value)}
              className="h-9 w-full rounded-lg border border-border/60 bg-background px-2 font-mono text-[12px] text-foreground outline-none focus:ring-1 focus:ring-primary/30"
            />
          </label>

          <div className="grid grid-cols-2 gap-2">
            {([
              ['summary_max_tokens', 'summaryTokens'],
              ['max_tokens', 'maxTokens'],
              ['soft_limit_tokens', 'softLimit'],
              ['hard_limit_tokens', 'hardLimit'],
              ['keep_recent_messages', 'recentMessages'],
            ] as const).map(([key, label]) => (
              <label key={key} className="block text-[11px] text-muted-foreground">
                <span className="mb-1 block">{tr(label)}</span>
                <input
                  aria-label={tr(label)}
                  type="number"
                  min="1"
                  value={form[key]}
                  onChange={(event) => update(key, event.target.value)}
                  className="h-9 w-full rounded-lg border border-border/60 bg-background px-2 font-mono text-[12px] text-foreground outline-none focus:ring-1 focus:ring-primary/30"
                />
              </label>
            ))}
          </div>

          <button
            type="button"
            onClick={() => { void save() }}
            disabled={saving}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-[12px] font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Check className="h-3.5 w-3.5" />
            {saving ? tr('saving') : saved ? tr('saved') : tr('save')}
          </button>
        </div>
      ) : null}
    </section>
  )
}
