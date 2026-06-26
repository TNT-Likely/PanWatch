import type { LmdReportSnapshot } from '@panwatch/api'

/** 从卡片已加载的老马视角快照拼一句结论（无报告则返回 null）。 */
export function formatLmdBriefFromSnapshot(snapshot: LmdReportSnapshot | null | undefined): string | null {
  if (!snapshot?.has_report) return null
  const parts: string[] = []
  if (snapshot.valuation_score != null) parts.push(`估值${snapshot.valuation_score}分`)
  if (snapshot.valuation_verdict) parts.push(snapshot.valuation_verdict)
  if (snapshot.expectation_hint) parts.push(`预期差${snapshot.expectation_hint}`)
  if (snapshot.profit_yoy_pct != null) {
    const sign = snapshot.profit_yoy_pct > 0 ? '+' : ''
    parts.push(`净利同比${sign}${snapshot.profit_yoy_pct.toFixed(1)}%`)
  }
  if (snapshot.revenue_yoy_pct != null) {
    const sign = snapshot.revenue_yoy_pct > 0 ? '+' : ''
    parts.push(`营收同比${sign}${snapshot.revenue_yoy_pct.toFixed(1)}%`)
  }
  if (parts.length === 0) return null
  const date = snapshot.report_date ? `(${snapshot.report_date})` : ''
  return `老马视角${date}：${parts.join('，')}`
}
