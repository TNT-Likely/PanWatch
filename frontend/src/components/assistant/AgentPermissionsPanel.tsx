import type { AgentPermissions } from '@panwatch/api'
import { useTranslation } from 'react-i18next'

type ToolRisk = 'read' | 'write' | 'external' | 'destructive'
type PermissionMode = 'allow' | 'ask' | 'deny'
type AgentToolPermission = AgentPermissions['tools'][number]

interface AgentPermissionsPanelProps {
  permissions: AgentPermissions
  variant?: 'card' | 'drawer'
  onChange: (change: {
    selector_kind: 'tool' | 'risk'
    selector_value: string
    mode: PermissionMode
    risk: ToolRisk
  }) => void
}

function availableModes(tool: AgentToolPermission): PermissionMode[] {
  if (tool.risk === 'destructive') return ['deny']
  if (tool.confirmation_required) return ['ask', 'deny']
  return ['allow', 'ask', 'deny']
}

function availableModesForRisk(risk: ToolRisk): PermissionMode[] {
  return risk === 'destructive' ? ['deny'] : ['allow', 'ask', 'deny']
}

export function AgentPermissionsPanel({ permissions, onChange, variant = 'card' }: AgentPermissionsPanelProps) {
  const { t } = useTranslation('configuration')
  const riskLabels: Record<ToolRisk, string> = {
    read: t('permissions.risks.read'),
    write: t('permissions.risks.write'),
    external: t('permissions.risks.external'),
    destructive: t('permissions.risks.destructive'),
  }
  const modeLabels: Record<PermissionMode, string> = {
    allow: t('permissions.modes.allow'),
    ask: t('permissions.modes.ask'),
    deny: t('permissions.modes.deny'),
  }
  const content = (
    <>
      <div className="mb-4">
        <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">{t('permissions.title')}</h3>
        <p className="mt-1 text-[11px] text-muted-foreground">
          {t('permissions.description')}
        </p>
      </div>
      <div className={`grid gap-2 ${variant === 'drawer' ? 'grid-cols-2' : 'sm:grid-cols-2 xl:grid-cols-4'}`}>
        {permissions.defaults.map((item) => (
          <label
            key={item.risk}
            className="flex min-w-0 items-center justify-between gap-1 rounded-xl border border-border/50 bg-accent/20 px-2.5 py-2 text-[11px] text-muted-foreground"
          >
            <span className="min-w-0 shrink truncate whitespace-nowrap">{riskLabels[item.risk]}</span>
            <select
              aria-label={t('permissions.defaultAria', { risk: riskLabels[item.risk] })}
              value={item.mode}
              onChange={(event) => onChange({
                selector_kind: 'risk',
                selector_value: item.risk,
                mode: event.target.value as PermissionMode,
                risk: item.risk,
              })}
              className="h-7 min-w-0 shrink-0 rounded-md border border-border/60 bg-background px-1.5 text-[11px] text-foreground outline-none focus:ring-1 focus:ring-primary/30"
            >
              {availableModesForRisk(item.risk).map((mode) => (
                <option key={mode} value={mode}>{modeLabels[mode]}</option>
              ))}
            </select>
          </label>
        ))}
      </div>
      <div className="mt-4 space-y-2">
        {permissions.tools.length === 0 ? (
          <p className="py-2 text-[12px] text-muted-foreground">{t('permissions.empty')}</p>
        ) : permissions.tools.map((tool) => (
          <div key={tool.name} className="flex items-center justify-between gap-3 rounded-xl bg-accent/30 px-3 py-2.5">
            <div className="min-w-0">
              <p className="text-[12px] font-medium text-foreground">{tool.title}</p>
              <p className="mt-0.5 text-[11px] text-muted-foreground">
                {tool.name} · {riskLabels[tool.risk]}{tool.confirmation_required ? t('permissions.confirmationRequired') : ''}
              </p>
            </div>
            <select
              aria-label={tool.title}
              value={tool.mode}
              onChange={(event) => onChange({
                selector_kind: 'tool',
                selector_value: tool.name,
                mode: event.target.value as PermissionMode,
                risk: tool.risk,
              })}
              className="h-8 rounded-lg border border-border/60 bg-background px-2 text-[12px] text-foreground outline-none focus:ring-1 focus:ring-primary/30"
            >
              {availableModes(tool).map((mode) => (
                <option key={mode} value={mode}>{modeLabels[mode]}</option>
              ))}
            </select>
          </div>
        ))}
      </div>
    </>
  )

  if (variant === 'drawer') {
    return <div>{content}</div>
  }

  return (
    <section id="sec-agent-permissions" className="card p-4 md:p-6 lg:col-span-12">
      {content}
    </section>
  )
}
