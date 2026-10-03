type Translate = (key: string, options?: Record<string, unknown>) => string

const KNOWN_AGENTS = new Set([
  'premarket_outlook',
  'intraday_monitor',
  'daily_report',
  'tradingagents',
])

export function localizeAgentName(name: string, fallback: string | undefined, t: Translate): string {
  return KNOWN_AGENTS.has(name) ? t(`agentsPage.catalog.${name}.name`) : fallback || name
}

export function localizeAgentDescription(name: string, fallback: string | undefined, t: Translate): string {
  return KNOWN_AGENTS.has(name) ? t(`agentsPage.catalog.${name}.description`) : fallback || ''
}
