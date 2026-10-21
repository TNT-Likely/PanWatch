import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '@/i18n'
import { ResearchReport, type ResearchReportData } from '@/components/ResearchReport'
import { ResearchProcessGraph } from '@/components/ResearchProcessGraph'

afterEach(cleanup)
const roles = ['technical', 'flow', 'events', 'bull', 'bear', 'manager', 'strategy', 'risk', 'decision']
const report: ResearchReportData = {
  version: 2, content: '## 大盘概览\n\n市场分化\n\n## 个股分析\n\n### 茅台（CN:600519）\n\n看多 · 条件性建议：买入 · 执行受限\n\n## 风险提示\n\n短线超买\n\n## 明日关注\n\n等待回踩确认',
  process: roles.map(role => ({ id: `0/0/${role}`, role, title: `${role} · 研究组 1`, content: `${role} 完整内部分析`, verdict: 'ready', active: true })),
  evidence: [
    { id: 'cn-news', stock_key: 'CN:600519', stock_name: '茅台', domain: 'events', domain_label: '新闻与公告', providers: ['fixture'], captured_at: '2026-10-19 18:00', summary: '已采集 5 条新闻与公告', content: '完整新闻详情\n\n`CN:600519/events/hash`', items: Array.from({ length: 5 }, (_, i) => ({ title: `新闻 ${i + 1}`, source: 'fixture', time: '18:00', url: `https://example.com/${i}` })) },
    { id: 'hk-flow', stock_key: 'HK:600519', stock_name: '港股样本', domain: 'flow', domain_label: '资金与指数', summary: '港股资金快照', content: '完整资金详情' },
  ],
  models: ['fixture'], usage: { attempts: 9, tools: 2, input_tokens: 500, output_tokens: 200, feedback: 0 },
  runtime: { completion: 'complete', as_of: '2026-10-19 18:00', stock_count: 2, elapsed_seconds: 82.4 },
}
const openTab = (name: string) => fireEvent.mouseDown(screen.getByRole('tab', { name, exact: true }), { button: 0, ctrlKey: false })

describe('reader-facing research report', () => {
  it('keeps the main report and lets readers select a role without opening every artifact', () => {
    render(<ResearchReport content="old content" report={report} />)
    expect(screen.getByRole('heading', { name: '大盘概览' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '明日关注' })).toBeTruthy()
    expect(screen.queryByText('old content')).toBeNull()
    const graph = screen.getByTestId('research-graph')
    expect(within(graph).getAllByRole('button')).toHaveLength(9)
    expect(screen.getByText('manager 完整内部分析')).toBeTruthy()
    fireEvent.click(within(graph).getByRole('button', { name: /技术分析/ }))
    expect(screen.getByText('technical 完整内部分析')).toBeTruthy()
    expect(screen.queryByText('manager 完整内部分析')).toBeNull()
  })

  it('omits absent roles and identifies a revision as a request rather than executed feedback', () => {
    const { container, rerender } = render(<ResearchProcessGraph items={report.process.filter(p => ['technical', 'manager'].includes(p.role!))} />)
    expect(within(screen.getByTestId('research-graph')).getAllByRole('button')).toHaveLength(2)
    expect(screen.queryByRole('button', { name: /多头论证/ })).toBeNull()
    const revision = { ...report.process[7], id: 'portfolio/1/risk', epoch: 1, verdict: 'revise', active: false }
    rerender(<ResearchProcessGraph items={[...report.process, revision]} />)
    expect(container.querySelector('path[data-feedback]')).toBeTruthy()
    expect(screen.getByText('虚线表示补证或修订要求，不代表反馈一定已执行。')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /风险审查/ }))
    expect(screen.getAllByText('risk 完整内部分析')).toHaveLength(2)
    expect(screen.getByText('历史轮次')).toBeTruthy()
  })

  it('previews every source, expands all details and filters market-qualified stock keys and domains', () => {
    render(<ResearchReport content="" report={report} />)
    openTab('证据来源')
    expect(screen.getByText('已采集 5 条新闻与公告')).toBeTruthy()
    expect(screen.getByText('港股资金快照')).toBeTruthy()
    expect(screen.getAllByRole('link', { name: /新闻/ })).toHaveLength(3)
    fireEvent.click(screen.getByRole('button', { name: '展开全部详情' }))
    expect(screen.getByText('完整新闻详情')).toBeTruthy()
    expect(screen.getByText('完整资金详情')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '收起全部详情' }))
    fireEvent.click(screen.getByRole('button', { name: '茅台 · CN:600519' }))
    expect(screen.queryByText('港股资金快照')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '全部股票' }))
    fireEvent.keyDown(screen.getByRole('combobox', { name: '证据类型' }), { key: 'Enter' })
    fireEvent.click(screen.getByRole('option', { name: '资金与指数' }))
    expect(screen.getByText('港股资金快照')).toBeTruthy()
    expect(screen.queryByText('已采集 5 条新闻与公告')).toBeNull()
  })

  it('shows actual whole-run duration and discloses unknown token usage without raw JSON', () => {
    const { rerender } = render(<ResearchReport content="" report={report} />)
    openTab('运行概览')
    expect(screen.getByText('82.4 秒')).toBeTruthy()
    expect(screen.getByText('500')).toBeTruthy()
    expect(screen.getByText('fixture')).toBeTruthy()
    expect(screen.queryByText(/reserved_tokens/)).toBeNull()
    rerender(<ResearchReport content="" report={{ ...report, usage: { attempts: 2, unknown_usage: 2, input_tokens: 0, output_tokens: 0 }, runtime: { ...report.runtime!, elapsed_seconds: null } }} />)
    expect(screen.getByText(/2 次调用未返回 Token 用量/)).toBeTruthy()
    expect(screen.queryByText('0 秒')).toBeNull()
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(3)
  })

  it('retains legacy Markdown and unstructured artifacts while localizing controls', async () => {
    await i18n.changeLanguage('en-US')
    const { rerender } = render(<ResearchReport content="## Legacy report" />)
    expect(screen.getByRole('heading', { name: 'Legacy report' })).toBeTruthy()
    expect(screen.queryByRole('tab')).toBeNull()
    rerender(<ResearchReport content="old" report={{ ...report, version: 1, process: [{ title: '旧研究', content: '旧版完整研究过程' }] }} />)
    expect(screen.getByText('旧版完整研究过程')).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'Research graph' })).toBeTruthy()
    expect(screen.getByText(/看多 · 条件性建议：买入 · 执行受限/)).toBeTruthy()
    openTab('Evidence')
    expect(screen.getByRole('combobox', { name: 'Evidence type' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Expand all details' })).toBeTruthy()
  })
})

it('leads with prominent actions and triggers while avoiding duplicate notification summaries', () => {
  const item = { stock_key: 'CN:600519', stock_name: '茅台', action: 'watch', outlook: 'bullish' as const,
    confidence: .6, execution_status: 'not_applicable' as const, triggers: ['回踩确认后重新评估'], invalidations: ['跌破支撑'] }
  render(<ResearchReport content="" report={{ ...report, version: 3, content: 'notification summary duplicate', body: report.content, actions: [item] }} />)
  const summary = screen.getByRole('region', { name: '操作建议' })
  expect(within(summary).getByText('观望')).toBeTruthy()
  expect(within(summary).getByText(/回踩确认后重新评估/)).toBeTruthy()
  expect(within(summary).getByText('无需下单')).toBeTruthy()
  expect(screen.queryByText('notification summary duplicate')).toBeNull()
  expect(screen.getByRole('heading', { name: '明日关注' })).toBeTruthy()
})

it('keeps a blocked buy prominent and translates its machine constraints', () => {
  const item = { stock_key: 'CN:600519', stock_name: '茅台', action: 'buy', outlook: 'bullish' as const,
    execution_status: 'blocked' as const, execution_constraints: ['insufficient_funds'], triggers: ['条件确认'], invalidations: [] }
  render(<ResearchReport content="" report={{ ...report, body: report.content, actions: [item] }} />)
  const summary = screen.getByRole('region', { name: '操作建议' })
  expect(within(summary).getByText('买入 · 执行受限')).toBeTruthy()
  expect(within(summary).getByText('下单限制：账户可用资金不足')).toBeTruthy()
  expect(within(summary).queryByText('insufficient_funds')).toBeNull()
})
