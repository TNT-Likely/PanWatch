import { localSkillAgentName, type LocalSkillItem } from '@panwatch/api'
import { LMD_AGENT_NAME, LMD_DISPLAY_NAME, LMD_SKILL_SLUG } from './lmd-report'

export interface ReportAgentOption {
  name: string
  display_name: string
  enabled: boolean
  execution_mode?: string
  skillSlug?: string
}

/** 合并工作流 Agent 与已启用本地 Skill，供报告生成下拉使用。 */
export function buildReportAgentOptions(
  workflowAgents: Array<{
    name: string
    display_name: string
    enabled: boolean
    execution_mode?: string
  }>,
  localSkills: LocalSkillItem[],
): ReportAgentOption[] {
  const enabledSkills = (localSkills || []).filter(s => s.enabled)
  const lmdSkillEnabled = enabledSkills.some(s => s.slug === LMD_SKILL_SLUG)

  const workflow = (workflowAgents || [])
    .filter(a => {
      // 已启用对应 Hermes skill 时，下拉只保留 skill 项「老马视角」，避免重复
      if (a.name === LMD_AGENT_NAME && lmdSkillEnabled) return false
      return true
    })
    .map(a => ({
      name: a.name,
      display_name: a.display_name,
      enabled: a.enabled,
      execution_mode: a.execution_mode,
      skillSlug: undefined as string | undefined,
    }))

  const fromSkills: ReportAgentOption[] = enabledSkills.map(s => ({
    name: localSkillAgentName(s.slug),
    display_name: s.slug === LMD_SKILL_SLUG ? LMD_DISPLAY_NAME : (s.display_name || s.slug),
    enabled: true,
    execution_mode: 'hermes',
    skillSlug: s.slug,
  }))

  return [...workflow, ...fromSkills]
}
