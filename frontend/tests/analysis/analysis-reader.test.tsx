import i18n from '@/i18n'
import { render, screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { DeepAnalysisResult } from '@panwatch/api'
import { AnalysisReader, analysisChapters, analysisConclusion, analysisTurns } from '@panwatch/biz-ui/components/analysis-reader'
import { DeepAnalysisModal, ToolkitDiagnostics } from '@panwatch/biz-ui/components/deep-analysis-modal'
import { buildAnalysisSections } from '@panwatch/biz-ui/analysis-sections'

const report = {
  content: 'Legacy decision',
  raw_data: {
    final_decision: '**Rating**: Hold\n\n**Executive Summary**: Wait for verified data.\n\n**Investment Thesis**: Unchanged original reasoning.',
    analyst_reports: { market: 'Introduction about data.\n\n## 核心结论\n\nNo verified OHLCV. Do not infer precise prices.', news: 'Saved company announcement evidence.' },
    debate_history: { history: 'Bull Analyst: Bull evidence.\n\nBear Analyst: Bear evidence.', judge_decision: '**Recommendation**: Underweight' },
    risk_debate: { history: 'Aggressive Analyst: Aggressive case.\n\nConservative Analyst: Conservative case.\n\nNeutral Analyst: Neutral case.' },
    risk_judgment: 'Risk verdict remains available without a judgment in the transcript.',
  },
} as DeepAnalysisResult

async function openGraph(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('tab', { name: '决策流程图' }))
}

describe('analysis reading graph', () => {
  it('defaults to original reading and opens node reports in a dialog with matching original navigation', async () => {
    const user = userEvent.setup()
    const originalScroll = vi.spyOn(HTMLElement.prototype, 'scrollIntoView')
    render(<AnalysisReader result={report} documentView={id => <div id={`sec-${id}`} data-testid="original">{id}</div>} />)
    expect(screen.getByRole('tab', { name: '原文阅读' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByTestId('original').textContent).toBe('decision')
    expect(screen.queryByRole('region', { name: '决策流程图' })).toBeNull()
    await openGraph(user)
    const originalFocus = vi.spyOn(screen.getByRole('tab', { name: '原文阅读' }), 'focus')
    expect(screen.getByRole('button', { name: '情绪分析师' }).hasAttribute('disabled')).toBe(true)
    await user.click(screen.getByRole('button', { name: '新闻分析师' }))
    const reading = screen.getByRole('dialog', { name: '新闻分析师' })
    expect(within(reading).getByText('Saved company announcement evidence.')).toBeTruthy()
    expect(within(reading).queryByText(/No verified OHLCV/)).toBeNull()
    await user.click(within(reading).getByRole('button', { name: '查看对应原文' }))
    expect(screen.getByTestId('original').textContent).toBe('news')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: '原文阅读' }))
    expect(originalFocus).toHaveBeenCalledWith({ preventScroll: true })
    await waitFor(() => expect(originalScroll.mock.instances).toContain(screen.getByTestId('original')))
    originalScroll.mockRestore()
    originalFocus.mockRestore()
  })

  it('shows conclusion excerpts and keeps all five debate roles distinct', async () => {
    const user = userEvent.setup()
    render(<AnalysisReader result={report} documentView={() => null} />)
    await openGraph(user)
    const graph = screen.getByRole('region', { name: '决策流程图' })
    const market = within(graph).getByRole('button', { name: '技术分析师' })
    expect(market.textContent).toContain('No verified OHLCV.')
    expect(market.textContent).not.toContain('Introduction')
    expect(graph.querySelector('svg path[marker-end]')).toBeTruthy()
    for (const [name, text] of [['多头研究员', 'Bull evidence.'], ['空头研究员', 'Bear evidence.'], ['激进风控', 'Aggressive case.'], ['保守风控', 'Conservative case.'], ['中立风控', 'Neutral case.']]) {
      const opener = screen.getByRole('button', { name })
      await user.click(opener)
      const dialog = screen.getByRole('dialog', { name })
      expect(within(dialog).getByText(text)).toBeTruthy()
      await user.keyboard('{Escape}')
      expect(screen.queryByRole('dialog')).toBeNull()
      expect(document.activeElement).toBe(opener)
    }
    await user.click(screen.getByRole('button', { name: '缩小流程图' }))
    expect(screen.getByLabelText('图缩放比例').textContent).toBe('80%')
    await user.click(screen.getByRole('button', { name: '放大流程图' }))
    expect(screen.getByLabelText('图缩放比例').textContent).toBe('90%')
  })

  it('preserves research/risk verdicts and unlabelled legacy debate without attributing speakers', async () => {
    const user = userEvent.setup()
    const legacy = { ...report, raw_data: { ...report.raw_data, debate_history: { history: 'Unlabelled legacy debate.', judge_decision: 'Saved research verdict.' }, risk_debate: undefined } } as DeepAnalysisResult
    const sections = buildAnalysisSections(legacy.raw_data)
    expect(sections.find(section => section.id === 'debate')?.markdown).toContain('Saved research verdict.')
    expect(sections.find(section => section.id === 'risk')?.markdown).toContain(report.raw_data.risk_judgment)
    render(<AnalysisReader result={legacy} documentView={() => null} />)
    await openGraph(user)
    expect(screen.queryByRole('button', { name: '多头研究员' })).toBeNull()
    await user.click(screen.getByRole('button', { name: '多空辩论' }))
    expect(within(screen.getByRole('dialog')).getByText('Unlabelled legacy debate.')).toBeTruthy()
    await user.keyboard('{Escape}')
    await user.click(screen.getByRole('button', { name: '风控裁决' }))
    expect(within(screen.getByRole('dialog')).getByText(report.raw_data.risk_judgment)).toBeTruthy()
  })

  it('preserves legacy content and translates graph controls without inventing reports', async () => {
    const user = userEvent.setup()
    await i18n.changeLanguage('en-US')
    render(<AnalysisReader result={{ content: 'Saved legacy text.' } as DeepAnalysisResult} documentView={() => <div>Original</div>} />)
    expect(screen.getByRole('tab', { name: 'Original report' }).getAttribute('aria-selected')).toBe('true')
    await user.click(screen.getByRole('tab', { name: 'Decision graph' }))
    expect(screen.getByRole('button', { name: 'Technical analyst' }).hasAttribute('disabled')).toBe(true)
    await user.click(screen.getByRole('button', { name: 'PM final decision' }))
    expect(within(screen.getByRole('dialog')).getByText('Saved legacy text.')).toBeTruthy()
  })
})

describe('faithful report formatting', () => {
  it('prefers saved conclusions over introductions, and explicitly identifies excerpt fallbacks', () => {
    expect(analysisConclusion(report.raw_data.final_decision)).toEqual({ text: 'Wait for verified data.', explicit: true })
    expect(analysisConclusion(report.raw_data.analyst_reports.market)).toEqual({ text: 'No verified OHLCV. Do not infer precise prices.', explicit: true })
    expect(analysisConclusion('# Report\n\n**分析日期**: 2026-10-23\n\nNo verified data.')).toEqual({ text: 'No verified data.', explicit: false })
  })
  it('keeps Markdown tables and fenced headings intact', () => {
    const chapters = analysisChapters('## Evidence\n\n| Metric | Value |\n| --- | --- |\n| Price | N/A |\n\n```md\n## Literal heading\n```\n\n## Limits\n\nOriginal caveat.')
    expect(chapters.map(chapter => chapter.title)).toEqual(['Evidence', 'Limits'])
    expect(chapters[0].markdown).toContain('| Price | N/A |')
    expect(chapters[0].markdown).toContain('```md\n## Literal heading\n```')
    expect(chapters[1].markdown.trim()).toBe('Original caveat.')
  })
  it('retains repeated role turns, ignores fenced speaker labels and never merges opponents', () => {
    const transcript = 'Bull Analyst: First bull.\n\n```text\nBear Analyst: Quoted example.\n```\n\nBear Analyst: Actual bear.\n\n**Bull Analyst**: Second bull.'
    expect(analysisTurns(transcript, 'Bull Analyst')).toBe('First bull.\n\n```text\nBear Analyst: Quoted example.\n```\n\n---\n\nSecond bull.')
    expect(analysisTurns(transcript, 'Bear Analyst')).toBe('Actual bear.')
    expect(analysisTurns('Unknown role: Not attributed.', 'Bull Analyst')).toBe('')
  })
})

const diagnosticSummary = { hit: 1, miss: 1, passthrough: 0, error: 0 }
const diagnosticRecords = [
  { action: 'HIT', method: 'get_stock_data', symbol: '002594', snippet: 'No kline data available.', chars: 100 },
  { action: 'MISS', method: 'get_indicators', symbol: '002594', snippet: 'Indicator unavailable.', reason: 'Not implemented.' },
]

it('keeps one hover preview and opens a focused detail dialog on click', async () => {
  const user = userEvent.setup()
  render(<ToolkitDiagnostics summary={diagnosticSummary} recent={diagnosticRecords} />)
  expect(screen.queryByText('No kline data available.')).toBeNull()
  const first = screen.getByRole('button', { name: /get_stock_data/ })
  const second = screen.getByRole('button', { name: /get_indicators/ })
  await user.hover(first)
  expect(within(screen.getByRole('region', { name: '数据注入预览' })).getByText('No kline data available.')).toBeTruthy()
  await user.hover(second)
  expect(screen.getAllByRole('region', { name: '数据注入预览' })).toHaveLength(1)
  expect(screen.queryByText('No kline data available.')).toBeNull()
  await user.click(second)
  const detail = screen.getByRole('dialog', { name: '数据注入详情 · get_indicators' })
  expect(within(detail).getByText('Indicator unavailable.')).toBeTruthy()
  expect(within(detail).getByText('Not implemented.')).toBeTruthy()
  expect(screen.queryByRole('region', { name: '数据注入预览' })).toBeNull()
  await user.keyboard('{Escape}')
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(document.activeElement).toBe(second)
})

it('opens all saved records together and preserves truncation notices', async () => {
  const user = userEvent.setup()
  render(<ToolkitDiagnostics summary={diagnosticSummary} recent={diagnosticRecords} />)
  await user.click(screen.getByRole('button', { name: '查看全部数据注入' }))
  const all = screen.getByRole('dialog', { name: '全部数据注入（2 条）' })
  expect(within(all).getByText('No kline data available.')).toBeTruthy()
  expect(within(all).getByText('Indicator unavailable.')).toBeTruthy()
  expect(within(all).getByText(/共 100 字符/)).toBeTruthy()
  expect(within(all).getByRole('region', { name: '数据注入内容' }).querySelectorAll('article')).toHaveLength(2)
  await user.keyboard('{Escape}')
  const first = screen.getByRole('button', { name: /get_stock_data/ })
  await user.click(first)
  await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '查看全部数据注入' }))
  expect(screen.getByRole('dialog', { name: '全部数据注入（2 条）' })).toBeTruthy()
  await user.keyboard('{Escape}')
  expect(document.activeElement).toBe(first)
})

it('supports keyboard and touch details without relying on hover; retains a clicked record during live updates', async () => {
  const user = userEvent.setup()
  const { rerender } = render(<ToolkitDiagnostics summary={diagnosticSummary} recent={diagnosticRecords} />)
  const first = screen.getByRole('button', { name: /get_stock_data/ })
  first.focus()
  await user.keyboard('{Enter}')
  rerender(<ToolkitDiagnostics summary={diagnosticSummary} recent={[{ ...diagnosticRecords[0], snippet: 'A later polling result.' }]} />)
  expect(within(screen.getByRole('dialog')).getByText('No kline data available.')).toBeTruthy()
  expect(within(screen.getByRole('dialog')).queryByText('A later polling result.')).toBeNull()
  await user.keyboard('{Escape}')
  await user.pointer([{ keys: '[TouchA>]', target: first }, { keys: '[/TouchA]' }])
  expect(screen.getByRole('dialog')).toBeTruthy()
  expect(screen.queryByRole('region', { name: '数据注入预览' })).toBeNull()
})

it('dismisses the preview and diagnostic dialogs before the actual parent dialog', async () => {
  const user = userEvent.setup()
  const onOpenChange = vi.fn()
  render(<DeepAnalysisModal open onOpenChange={onOpenChange} stockId={2} stockName="比亚迪" stockSymbol="002594" initialResult={{ ...report, raw_data: { ...report.raw_data, toolkit_diagnostic: { summary: diagnosticSummary, recent: diagnosticRecords } } }} />)
  const first = screen.getByRole('button', { name: /get_stock_data/ })
  await user.hover(first)
  await user.keyboard('{Escape}')
  expect(screen.queryByRole('region', { name: '数据注入预览' })).toBeNull()
  expect(onOpenChange).not.toHaveBeenCalled()
  await user.click(first)
  await user.keyboard('{Escape}')
  expect(onOpenChange).not.toHaveBeenCalled()
  expect(document.activeElement).toBe(first)
  const all = screen.getByRole('button', { name: '查看全部数据注入', exact: true })
  await user.click(all)
  await user.keyboard('{Escape}')
  expect(onOpenChange).not.toHaveBeenCalled()
  expect(document.activeElement).toBe(all)
  await user.keyboard('{Escape}')
  expect(onOpenChange).toHaveBeenCalledTimes(1)
  expect(onOpenChange).toHaveBeenCalledWith(false)
})

it('closes the nested node dialog first, restores its node, then closes the real parent dialog', async () => {
  const user = userEvent.setup()
  const onOpenChange = vi.fn()
  render(<DeepAnalysisModal open onOpenChange={onOpenChange} stockId={2} stockName="比亚迪" stockSymbol="002594" initialResult={{ ...report, raw_data: { ...report.raw_data, toolkit_diagnostic: {
    summary: { hit: 1, miss: 0, passthrough: 0, error: 0 }, recent: [{ action: 'HIT', method: 'get_stock_data', snippet: 'Original diagnostic detail.' }],
  } } }} />)
  expect(screen.queryByText('Original diagnostic detail.')).toBeNull()
  await openGraph(user)
  const opener = screen.getByRole('button', { name: 'PM 最终决策', exact: true })
  await user.click(opener)
  expect(screen.getByRole('dialog', { name: 'PM 最终决策' })).toBeTruthy()
  await user.keyboard('{Escape}')
  expect(onOpenChange).not.toHaveBeenCalled()
  expect(document.activeElement).toBe(opener)
  await user.keyboard('{Escape}')
  expect(onOpenChange).toHaveBeenCalledTimes(1)
  expect(onOpenChange).toHaveBeenCalledWith(false)
})
