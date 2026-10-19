import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { fetchAPI } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

export interface ResearchAgentConfig { name: string; config: Record<string, unknown> }
type ResearchLabel = 'title' | 'description' | 'engine' | 'legacy' | 'shadow' | 'langgraph' | 'attempts' | 'deadline' | 'cancel' | 'save' | 'saved' | 'failed'

export default function ResearchConfigDialog({ agent, onClose, onSaved }: {
  agent: ResearchAgentConfig | null; onClose: () => void; onSaved: () => void
}) {
  const { t } = useTranslation('configuration')
  const { toast } = useToast()
  const [value, setValue] = useState<Record<string, unknown>>({})
  const [saving, setSaving] = useState(false)
  const key = (name: ResearchLabel): string => t(`agentsPage.research.${name}`)
  useEffect(() => {
    if (!agent) return
    const intraday = agent.name === 'intraday_monitor'
    setValue({ engine: 'legacy', max_attempts: intraday ? 10 : agent.name === 'daily_report' ? 18 : 14,
      deadline_seconds: intraday ? 45 : agent.name === 'daily_report' ? 180 : 90,
      ...(agent.config.research_graph as Record<string, unknown> || {}) })
  }, [agent])
  async function save() {
    if (!agent) return
    setSaving(true)
    try {
      await fetchAPI(`/agents/${agent.name}`, { method: 'PUT', body: JSON.stringify({
        config: { ...agent.config, research_graph: value },
      }) })
      toast(key('saved'), 'success')
      onSaved(); onClose()
    } catch (error) { toast(error instanceof Error ? error.message : key('failed'), 'error') }
    finally { setSaving(false) }
  }
  return <Dialog open={!!agent} onOpenChange={open => { if (!open && !saving) onClose() }}>
    <DialogContent className="max-h-[85dvh] overflow-y-auto scrollbar">
      <DialogHeader><DialogTitle>{key('title')}</DialogTitle><DialogDescription>{key('description')}</DialogDescription></DialogHeader>
      <div className="space-y-4">
        <div className="space-y-2"><Label htmlFor="research-engine">{key('engine')}</Label>
          <Select value={String(value.engine || 'legacy')} onValueChange={engine => setValue({ ...value, engine })}>
            <SelectTrigger id="research-engine" aria-label={key('engine')}><SelectValue /></SelectTrigger>
            <SelectContent>{(['legacy', 'shadow', 'langgraph'] as const).map(engine => <SelectItem key={engine} value={engine}>{key(engine)}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <div className="space-y-2"><Label htmlFor="research-attempts">{key('attempts')}</Label>
          <Input id="research-attempts" type="number" min={1} max={100} value={Number(value.max_attempts)} onChange={e => setValue({ ...value, max_attempts: Number(e.target.value) })} /></div>
        <div className="space-y-2"><Label htmlFor="research-deadline">{key('deadline')}</Label>
          <Input id="research-deadline" type="number" min={1} max={600} value={Number(value.deadline_seconds)} onChange={e => setValue({ ...value, deadline_seconds: Number(e.target.value) })} /></div>
        <div className="flex justify-end gap-2"><Button variant="ghost" disabled={saving} onClick={onClose}>{key('cancel')}</Button><Button disabled={saving} onClick={() => void save()}>{key('save')}</Button></div>
      </div>
    </DialogContent>
  </Dialog>
}
