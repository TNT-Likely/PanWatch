import type { DeepAnalysisResult } from '@aiwatch/api'

export interface AnalysisSection {
  id: string
  title: string
  markdown: string
}

/**
 * 从深度分析 raw_data 组装各部分(决策正文 / 四分析师 / 看多看空辩论 / 风控辩论)。
 * 弹窗的 tab 与详细阅读页的长文共用这一份组装逻辑,避免两处渲染漂移。
 * 顺序即详细页从上到下、弹窗 tab 从左到右的顺序。只返回有内容的部分。
 */
export function buildAnalysisSections(
  rawData: Partial<DeepAnalysisResult['raw_data']>,
): AnalysisSection[] {
  const reports = rawData.analyst_reports || { market: '', social: '', news: '', fundamentals: '' }
  const debate = rawData.debate_history
  const riskDebate = rawData.risk_debate
  const sections: AnalysisSection[] = []

  // 决策书:section 标题直接用「PM 最终决策书」(去掉原先重复的前置「最终决策」标题);
  // 交易员执行计划作为子标题保留(与决策书区分)。
  const decisionBody = [
    rawData.final_decision || '',
    rawData.trader_plan && `### 💼 交易员执行计划\n\n${rawData.trader_plan}`,
  ]
    .filter(Boolean)
    .join('\n\n')
  if (decisionBody) sections.push({ id: 'decision', title: 'PM 最终决策书', markdown: decisionBody })

  // 四位分析师
  const analysts: [string, string][] = [
    ['market', '技术分析师'],
    ['social', '情绪分析师'],
    ['news', '新闻分析师'],
    ['fundamentals', '基本面分析师'],
  ]
  for (const [k, title] of analysts) {
    const text = (reports as unknown as Record<string, string>)[k] || ''
    if (text) sections.push({ id: k, title, markdown: text })
  }

  // 看多看空辩论(研究团队:辩论历史 + 研究主管裁决)
  if (debate?.history) {
    let dc = debate.history
    if (debate.judge_decision) dc += `\n\n### ⚖️ 研究主管裁决\n\n${debate.judge_decision}`
    sections.push({ id: 'debate', title: '看多看空辩论', markdown: dc })
  }

  // 风控辩论(风控团队:激进/中立/保守辩论 + 风控裁决)
  if (riskDebate?.history) {
    let rc = riskDebate.history
    if (riskDebate.judge_decision) rc += `\n\n### 🛡️ 风控裁决\n\n${riskDebate.judge_decision}`
    sections.push({ id: 'risk', title: '风控辩论', markdown: rc })
  }

  return sections
}
