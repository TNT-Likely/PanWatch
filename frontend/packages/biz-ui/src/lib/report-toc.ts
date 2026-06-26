/** 从 Markdown 正文提取 2~4 级标题，用于报告子目录导航。 */

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
    const key = item.agent_name || 'unknown'
    const bucket = map.get(key)
    if (bucket) bucket.push(item)
    else map.set(key, [item])
  }
  return Array.from(map.entries()).map(([agentName, grouped]) => ({
    agentName,
    items: grouped,
  }))
}
