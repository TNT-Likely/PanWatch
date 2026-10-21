import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { fetchAPI } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { Switch } from '@panwatch/base-ui/components/ui/switch'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

export interface ResearchAgentConfig { name: string; config: Record<string, unknown> }
type TemplateId = 'intraday_fast' | 'premarket_balanced' | 'postmarket_deep' | 'custom_research'
type Specialist = 'technical' | 'flow' | 'events'
interface Template { id: TemplateId; graph: string; defaults: Record<string, unknown>; allowed_specialists: Specialist[] }
interface Templates { default_template: TemplateId; templates: Template[] }
type ResearchLabel = 'title' | 'description' | 'attempts' | 'deadline' | 'cancel' | 'save' | 'saved' | 'failed' | 'template' | 'templateHint' | 'specialists' | 'debate' | 'never' | 'material' | 'always' | 'preview' | 'fastHint' | 'rulesRequired' | 'advanced' | 'tools' | 'feedback' | 'concurrency' | 'tokens' | 'output' | 'loading' | 'invalid' | 'loadFailed' | 'triggerMode' | 'changes' | 'continuous'

export default function ResearchConfigDialog({ agent, onClose, onSaved }: {
  agent: ResearchAgentConfig | null; onClose: () => void; onSaved: () => void
}) {
  const { t } = useTranslation('configuration')
  const { toast } = useToast()
  const [value, setValue] = useState<Record<string, unknown>>({})
  const [metadata, setMetadata] = useState<Templates | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const key = (name: ResearchLabel): string => t(`agentsPage.research.${name}`)
  const templateLabel = (id: TemplateId): string => t(`agentsPage.research.templates.${id}`)
  const roleLabel = (role: Specialist): string => t(`p4.history.graphRoles.${role}`)
  useEffect(() => {
    if (!agent) return
    let active = true
    setMetadata(null); setError('')
    void fetchAPI<Templates>(`/agents/research-templates?agent_name=${encodeURIComponent(agent.name)}`).then(data => {
      if (!active) return
      const current = (agent.config.research_graph || {}) as Record<string, unknown>
      const compatible = agent.name === 'intraday_monitor' && !current.template &&
        ((current.specialists as string[] || []).includes('flow') || current.debate || Number(current.max_feedback || 0) > 0)
      const selected = data.templates.find(item => item.id === current.template) || data.templates.find(item => item.id === (compatible ? 'custom_research' : data.default_template))!
      setValue({ ...selected.defaults, ...current, template: selected.id, engine: 'langgraph' })
      setMetadata(data)
    }).catch(e => { if (active) setError(e instanceof Error ? e.message : 'research_config_invalid') })
    return () => { active = false }
  }, [agent])
  const selected = metadata?.templates.find(item => item.id === value.template)
  const fast = selected?.graph === 'intraday_fast'
  const specialists = (value.specialists || []) as Specialist[]
  const debate = !value.debate ? 'never' : value.debate_on === 'material' ? 'material' : 'always'
  const valid = metadata && specialists.length > 0 && Number.isInteger(value.max_attempts) && Number(value.max_attempts) >= 1 && Number(value.max_attempts) <= 100 && Number(value.deadline_seconds) > 0 && Number(value.deadline_seconds) <= 600
  const numberInput = (name: ResearchLabel, field: string, min: number, max: number) => <div className="space-y-2"><Label htmlFor={`research-${field}`}>{key(name)}</Label>
    <Input id={`research-${field}`} type="number" min={min} max={max} value={Number(value[field] ?? min)} onChange={e => setValue({ ...value, [field]: Number(e.target.value) })} /></div>
  async function save() {
    if (!agent || !valid) return
    setSaving(true)
    try {
      await fetchAPI(`/agents/${agent.name}`, { method: 'PUT', body: JSON.stringify({ config: { ...agent.config, research_graph: value } }) })
      toast(key('saved'), 'success'); onSaved(); onClose()
    } catch (e) { toast(e instanceof Error ? e.message : key('failed'), 'error') }
    finally { setSaving(false) }
  }
  return <Dialog open={!!agent} onOpenChange={open => { if (!open && !saving) onClose() }}>
    <DialogContent className="max-h-[85dvh] overflow-y-auto scrollbar">
      <DialogHeader><DialogTitle>{key('title')}</DialogTitle><DialogDescription>{key('description')}</DialogDescription></DialogHeader>
      {error && <p role="alert" className="text-sm text-destructive">{key('loadFailed')}：{error}</p>}
      {!metadata && !error && <p role="status">{key('loading')}</p>}
      {metadata && <div className="space-y-4">
        <div className="space-y-2"><Label htmlFor="research-template">{key('template')}</Label>
          <Select value={String(value.template)} onValueChange={template => { const next = metadata.templates.find(item => item.id === template)!; setValue({ ...next.defaults, engine: value.engine, template }) }}>
            <SelectTrigger id="research-template" aria-label={key('template')}><SelectValue /></SelectTrigger>
            <SelectContent>{metadata.templates.map(item => <SelectItem key={item.id} value={item.id}>{templateLabel(item.id)}</SelectItem>)}</SelectContent>
          </Select><p className="text-xs text-muted-foreground">{key('templateHint')}</p>
        </div>
        <fieldset className="space-y-2"><legend className="text-sm font-medium">{key('specialists')}</legend>
          {selected?.allowed_specialists.map(role => <div key={role} className="flex items-center justify-between gap-3"><Label htmlFor={`research-role-${role}`}>{roleLabel(role)}</Label><Switch id={`research-role-${role}`} checked={specialists.includes(role)} onCheckedChange={checked => setValue({ ...value, specialists: checked ? [...specialists, role] : specialists.filter(item => item !== role) })} /></div>)}
        </fieldset>
        {fast && <div className="space-y-2"><Label htmlFor="research-trigger">{key('triggerMode')}</Label><Select value={String(value.trigger_mode || 'changes')} onValueChange={trigger_mode => setValue({ ...value, trigger_mode })}>
          <SelectTrigger id="research-trigger" aria-label={key('triggerMode')}><SelectValue /></SelectTrigger><SelectContent><SelectItem value="changes">{key('changes')}</SelectItem><SelectItem value="always">{key('continuous')}</SelectItem></SelectContent></Select></div>}
        {!fast && <div className="space-y-2"><Label htmlFor="research-debate">{key('debate')}</Label><Select value={debate} onValueChange={mode => setValue({ ...value, debate: mode !== 'never', debate_on: mode === 'material' ? 'material' : 'always' })}>
          <SelectTrigger id="research-debate" aria-label={key('debate')}><SelectValue /></SelectTrigger><SelectContent>{(['never', 'material', 'always'] as const).map(mode => <SelectItem key={mode} value={mode}>{key(mode)}</SelectItem>)}</SelectContent></Select></div>}
        <div className="rounded-xl border border-border bg-muted/30 p-3 text-xs leading-6" role="img" aria-label={key('preview')}>
          {fast && <p>{t('p4.history.graphRoles.gate')} ↓</p>}<p>{specialists.map(roleLabel).join(' / ')} ↓</p>
          {!fast && <><p>{debate !== 'never' ? `${t('p4.history.graphRoles.bull')} / ${t('p4.history.graphRoles.bear')} (${key(debate === 'material' ? 'material' : 'always')}) ↓` : ''}</p><p>{t('p4.history.graphRoles.manager')} ↓</p></>}
          <p>{t('p4.history.graphRoles.strategy')} ↓</p><p>{fast ? t('p4.history.graphRoles.execution') : t('p4.history.graphRoles.risk')}</p>{!fast && <><p>↓ {t('p4.history.graphRoles.decision')}</p><p>↓ {t('p4.history.graphRoles.execution')}</p></>}
        </div>
        <p className="text-xs text-muted-foreground">{key(fast ? 'fastHint' : 'rulesRequired')}</p>
        <div className="grid grid-cols-2 gap-3">{numberInput('attempts', 'max_attempts', 1, 100)}{numberInput('deadline', 'deadline_seconds', 1, 600)}</div>
        <details className="rounded-lg border border-border p-3"><summary className="cursor-pointer text-sm">{key('advanced')}</summary><div className="mt-3 grid grid-cols-2 gap-3">
          {numberInput('tools', 'max_tools', 0, 30)}{numberInput('concurrency', 'concurrency', 1, 6)}{!fast && numberInput('feedback', 'max_feedback', 0, 2)}{numberInput('tokens', 'max_total_tokens', 1000, 1000000)}{numberInput('output', 'max_output_tokens', 128, 8000)}
        </div></details>
        {!valid && <p role="alert" className="text-xs text-destructive">{key('invalid')}</p>}
      </div>}
      <div className="flex justify-end gap-2"><Button variant="ghost" disabled={saving} onClick={onClose}>{key('cancel')}</Button><Button disabled={saving || !valid} onClick={() => void save()}>{key('save')}</Button></div>
    </DialogContent>
  </Dialog>
}
