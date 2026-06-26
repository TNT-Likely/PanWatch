/** 从 Markdown 正文提取 2~4 级标题，用于报告子目录导航。 */

import { normalizeLmdReportAgentName } from './lmd-report'

export function slugifyHeading(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[*_`#~]/g, '')
    .replace(/\s+/g, '-')
    .replace(/[^\w一-龥-]/g, '')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
}

export type ReportHeading = {
  level: 2 | 3 | 4
  text: string
  slug: string
}

export function parseReportHeadings(markdown: string): ReportHeading[] {
  const out: ReportHeading[] = []
  for (const raw of (markdown || '').split('\n')) {
    const m = /^(#{2,4})\s+(.+?)\s*#*$/.exec(raw)
    if (!m) continue
    const level = m[1].length as 2 | 3 | 4
    const text = m[2].replace(/[*_`]/g, '').trim()
    if (!text) continue
    out.push({ level, text, slug: slugifyHeading(m[2]) })
  }
  return out
}

/** 按 agent_name 分组报告列表，便于侧边栏折叠浏览。 */
export function groupReportsByAgent<T extends { agent_name: string }>(
  items: T[],
): Array<{ agentName: string; items: T[] }> {
  const map = new Map<string, T[]>()
  for (const item of items) {
    const key = normalizeLmdReportAgentName(item.agent_name || 'unknown')
    const bucket = map.get(key)
    if (bucket) bucket.push(item)
    else map.set(key, [item])
  }
  return Array.from(map.entries()).map(([agentName, grouped]) => ({
    agentName,
    items: [...grouped].sort((a, b) => {
      const am = reportItemTimeMs(a as { updated_at?: string; created_at?: string; analysis_date?: string })
      const bm = reportItemTimeMs(b as { updated_at?: string; created_at?: string; analysis_date?: string })
      return bm - am
    }),
  }))
}

function reportItemTimeMs(record: {
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

export type LmdReportSection = 'fundamentals' | 'valuation'

function headingMatchesLmdSection(text: string, section: LmdReportSection): boolean {
  const t = text.trim()
  if (/^路径|^情景/.test(t)) return false
  if (section === 'fundamentals') return /基本面/.test(t)
  if (section === 'valuation') return /估值/.test(t) && !/基本面/.test(t)
  return false
}

/** 在老马视角报告 Markdown 中定位「基本面 / 估值」章节锚点。 */
export function findLmdReportSectionSlug(
  markdown: string,
  section: LmdReportSection,
): string | null {
  const headings = parseReportHeadings(markdown)
  const matches = headings.filter(h => headingMatchesLmdSection(h.text, section))
  if (matches.length === 0) return null
  const preferred = matches.find(h => h.level === 3) || matches[0]
  return preferred.slug
}

export function resolveLmdReportSectionSlug(
  markdown: string,
  section: string,
): string | null {
  const key = section.trim().toLowerCase()
  if (key === 'fundamentals' || key === '基本面') {
    return findLmdReportSectionSlug(markdown, 'fundamentals')
  }
  if (key === 'valuation' || key === '估值') {
    return findLmdReportSectionSlug(markdown, 'valuation')
  }
  if (!key) return null
  return key
}
