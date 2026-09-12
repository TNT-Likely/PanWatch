import type { AgentPermissions } from '@panwatch/api'

type ToolRisk = 'read' | 'write' | 'external' | 'destructive'
type PermissionMode = 'allow' | 'ask' | 'deny'
type AgentToolPermission = AgentPermissions['tools'][number]

interface AgentPermissionsPanelProps {
  permissions: AgentPermissions
  onChange: (change: {
    selector_kind: 'tool' | 'risk'
    selector_value: string
    mode: PermissionMode
    risk: ToolRisk
  }) => void
}

const RISK_LABELS: Record<ToolRisk, string> = {
  read: '读取',
  write: '修改',
  external: '外部操作',
  destructive: '破坏性操作',
}

const MODE_LABELS: Record<PermissionMode, string> = {
  allow: '直接允许',
  ask: '每次询问',
  deny: '禁止',
}

function availableModes(tool: AgentToolPermission): PermissionMode[] {
  if (tool.risk === 'destructive') return ['deny']
  if (tool.confirmation_required) return ['ask', 'deny']
  return ['allow', 'ask', 'deny']
}

function availableModesForRisk(risk: ToolRisk): PermissionMode[] {
  return risk === 'destructive' ? ['deny'] : ['allow', 'ask', 'deny']
}

export function AgentPermissionsPanel({ permissions, onChange }: AgentPermissionsPanelProps) {
  return (
    <section id="sec-agent-permissions" className="card p-4 md:p-6 lg:col-span-12">
      <div className="mb-4">
        <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">智能体工具权限</h3>
        <p className="mt-1 text-[11px] text-muted-foreground">
          仅影响助手可见和可执行的工具；破坏性操作始终禁止，需确认的工具不能设为直接允许。
        </p>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {permissions.defaults.map((item) => (
          <label
            key={item.risk}
            className="flex items-center justify-between gap-2 rounded-xl border border-border/50 bg-accent/20 px-2.5 py-2 text-[11px] text-muted-foreground"
          >
            <span>{RISK_LABELS[item.risk]}</span>
            <select
              aria-label={`${RISK_LABELS[item.risk]}默认权限`}
              value={item.mode}
              onChange={(event) => onChange({
                selector_kind: 'risk',
                selector_value: item.risk,
                mode: event.target.value as PermissionMode,
                risk: item.risk,
              })}
              className="h-7 rounded-md border border-border/60 bg-background px-1.5 text-[11px] text-foreground outline-none focus:ring-1 focus:ring-primary/30"
            >
              {availableModesForRisk(item.risk).map((mode) => (
                <option key={mode} value={mode}>{MODE_LABELS[mode]}</option>
              ))}
            </select>
          </label>
        ))}
      </div>
      <div className="mt-4 space-y-2">
        {permissions.tools.length === 0 ? (
          <p className="py-2 text-[12px] text-muted-foreground">当前没有已注册的可配置工具。</p>
        ) : permissions.tools.map((tool) => (
          <div key={tool.name} className="flex items-center justify-between gap-3 rounded-xl bg-accent/30 px-3 py-2.5">
            <div className="min-w-0">
              <p className="text-[12px] font-medium text-foreground">{tool.title}</p>
              <p className="mt-0.5 text-[11px] text-muted-foreground">
                {tool.name} · {RISK_LABELS[tool.risk]}{tool.confirmation_required ? ' · 需确认' : ''}
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
                <option key={mode} value={mode}>{MODE_LABELS[mode]}</option>
              ))}
            </select>
          </div>
        ))}
      </div>
    </section>
  )
}
