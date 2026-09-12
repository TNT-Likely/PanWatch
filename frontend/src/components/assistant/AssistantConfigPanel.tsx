import { useEffect, useState } from 'react'
import { Check, Cpu } from 'lucide-react'
import { chatApi, type AssistantConfig, type AssistantConfigUpdate } from '@panwatch/api'

interface AssistantConfigForm {
  compression_model_id: string
  compression_temperature: string
  max_tokens: string
  soft_limit_tokens: string
  hard_limit_tokens: string
  keep_recent_messages: string
}

const EMPTY_FORM: AssistantConfigForm = {
  compression_model_id: '',
  compression_temperature: '0.1',
  max_tokens: '12000',
  soft_limit_tokens: '8400',
  hard_limit_tokens: '10200',
  keep_recent_messages: '8',
}

function toForm(config: AssistantConfig): AssistantConfigForm {
  return {
    compression_model_id: config.compression_model_id?.toString() || '',
    compression_temperature: config.compression_temperature.toString(),
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
    max_tokens: Number(form.max_tokens),
    soft_limit_tokens: Number(form.soft_limit_tokens),
    hard_limit_tokens: Number(form.hard_limit_tokens),
    keep_recent_messages: Number(form.keep_recent_messages),
  }
}

export function AssistantConfigPanel() {
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
        if (active) setError('无法加载上下文配置，请稍后重试。')
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
      setError('保存上下文配置失败，请检查阈值后重试。')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section aria-label="上下文配置" className="border-t border-border/50 pt-5">
      <div className="mb-3 flex items-start gap-2">
        <Cpu className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div>
          <h3 className="text-[13px] font-semibold text-foreground">上下文工程</h3>
          <p className="mt-1 text-[11px] text-muted-foreground">配置自动压缩所使用的模型和上下文预算。</p>
        </div>
      </div>

      {error && <p className="mb-3 rounded-lg bg-destructive/10 px-3 py-2 text-[11px] text-destructive">{error}</p>}
      {loading ? (
        <div className="flex items-center gap-2 py-6 text-[12px] text-muted-foreground">
          <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current/30 border-t-current" />
          正在加载上下文配置…
        </div>
      ) : config ? (
        <div className="space-y-3">
          <label className="block text-[11px] text-muted-foreground">
            <span className="mb-1 block">上下文压缩模型</span>
            <select
              aria-label="上下文压缩模型"
              value={form.compression_model_id}
              onChange={(event) => update('compression_model_id', event.target.value)}
              className="h-9 w-full rounded-lg border border-border/60 bg-background px-2 text-[12px] text-foreground outline-none focus:ring-1 focus:ring-primary/30"
            >
              <option value="">跟随系统默认模型</option>
              {config.models.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.service_name} / {model.name} ({model.model})
                </option>
              ))}
            </select>
          </label>

          <label className="block text-[11px] text-muted-foreground">
            <span className="mb-1 block">压缩温度</span>
            <input
              aria-label="压缩温度"
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
              ['max_tokens', '最大上下文 Token'],
              ['soft_limit_tokens', '自动压缩阈值'],
              ['hard_limit_tokens', '硬上限 Token'],
              ['keep_recent_messages', '保留最近消息数'],
            ] as const).map(([key, label]) => (
              <label key={key} className="block text-[11px] text-muted-foreground">
                <span className="mb-1 block">{label}</span>
                <input
                  aria-label={label}
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
            {saving ? '保存中…' : saved ? '已保存' : '保存上下文配置'}
          </button>
        </div>
      ) : null}
    </section>
  )
}
