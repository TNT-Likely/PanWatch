import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '@/i18n'
import { ResearchReport, type ResearchReportData } from '@/components/ResearchReport'

afterEach(cleanup)
const report: ResearchReportData = {
  version: 1, content: '## 大盘概览\n\n市场分化\n\n## 个股分析\n\n### 茅台（CN:600519）\n\n看多 · 条件性建议：买入 · 执行受限\n\n## 风险提示\n\n短线超买\n\n## 明日关注\n\n等待回踩确认',
  process: [{ title: '技术分析 · 研究组 1', content: '完整内部分析' }],
  evidence: [{ title: '茅台 · 腾讯行情', content: '来源说明\n\n`CN:600519/technical/hash/summary`' }],
  models: ['fixture'], usage: { attempts: 9, tools: 2, input_tokens: 500, output_tokens: 200 },
}

describe('reader-facing research report', () => {
  it('leads with report sections and keeps audit material collapsed by default', async () => {
    await i18n.changeLanguage('zh-CN')
    const { container } = render(<ResearchReport content="old content" report={report} />)
    expect(screen.getByRole('heading', { name: '大盘概览' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '明日关注' })).toBeTruthy()
    expect(screen.queryByText('old content')).toBeNull()
    const details = container.querySelectorAll('details')
    expect([...details].every(d => !d.open)).toBe(true)
    fireEvent.click(screen.getByText('研究过程（1 个节点）'))
    expect(details[0].open).toBe(true)
    fireEvent.click(screen.getByText('技术分析 · 研究组 1'))
    expect(details[1].open).toBe(true)
    fireEvent.click(screen.getByText('研究过程（1 个节点）'))
    expect(details[0].open).toBe(false)
  })

  it('retains legacy Markdown and localizes the supporting details', async () => {
    await i18n.changeLanguage('en-US')
    const { rerender } = render(<ResearchReport content="## Legacy report" />)
    expect(screen.getByRole('heading', { name: 'Legacy report' })).toBeTruthy()
    expect(screen.queryByText('Run information')).toBeNull()
    rerender(<ResearchReport content="old" report={report} />)
    expect(screen.getByText('Evidence sources (1 items)')).toBeTruthy()
    expect(screen.getByText('Run information')).toBeTruthy()
    expect(screen.getByText(/看多 · 条件性建议：买入 · 执行受限/)).toBeTruthy()
  })
})
