import { localSkillAgentName, parseLocalSkillSlug } from '@panwatch/api'

/** Hermes skill 名；与 lmd_outlook Agent 共用同一套老马视角报告。 */
export const LMD_SKILL_SLUG = 'lmd-finance-perspective'

/** 工作流 Agent 名（报告列表、触发生成统一用这个）。 */
export const LMD_AGENT_NAME = 'lmd_outlook'

export const LMD_DISPLAY_NAME = '老马视角'

export const LMD_LOCAL_SKILL_AGENT_NAME = localSkillAgentName(LMD_SKILL_SLUG)

export function isLmdReportAgent(agentName: string): boolean {
  if (agentName === LMD_AGENT_NAME) return true
  return parseLocalSkillSlug(agentName) === LMD_SKILL_SLUG
}

/** 报告分组 / 展示时把 skill 记录归到 lmd_outlook。 */
export function normalizeLmdReportAgentName(agentName: string): string {
  return isLmdReportAgent(agentName) ? LMD_AGENT_NAME : agentName
}

export function resolveLmdReportAgentLabel(agentName: string, fallback?: string): string {
  if (isLmdReportAgent(agentName)) return LMD_DISPLAY_NAME
  return fallback || agentName
}

function reportTimeMs(record: {
  updated_at?: string
  created_at?: string
  analysis_date?: string
}): number {
  for (const v of [record.updated_at, record.created_at, record.analysis_date]) {
    if (!v) continue
    const t = Date.parse(String(v).replace(' ', 'T'))
    if (!Number.isNaN(t)) return t
  }
  return 0
}

/** 从报告列表中取最新一条老马视角（含 lmd_outlook 与 local_skill:lmd-finance-perspective）。 */
export function pickLatestLmdReport<T extends {
  agent_name: string
  updated_at?: string
  created_at?: string
  analysis_date?: string
}>(items: T[]): T | null {
  const lmd = items.filter(r => isLmdReportAgent(r.agent_name))
  if (lmd.length === 0) return null
  return [...lmd].sort((a, b) => reportTimeMs(b) - reportTimeMs(a))[0]
}
