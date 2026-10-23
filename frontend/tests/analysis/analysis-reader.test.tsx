import i18n from '@/i18n'
import { render, screen, within, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { DeepAnalysisResult } from '@panwatch/api'
import { AnalysisReader, analysisChapters, analysisExcerpt } from '@panwatch/biz-ui/components/analysis-reader'
import { DeepAnalysisModal, ToolkitDiagnostics } from '@panwatch/biz-ui/components/deep-analysis-modal'
import { buildAnalysisSections } from '@panwatch/biz-ui/analysis-sections'

const report = {
  content: 'Legacy decision',
  raw_data: {
    final_decision: '**Rating**: Hold\n\n**Executive Summary**: Wait for verified data.\n\n**Investment Thesis**: Unchanged original reasoning.',
    analyst_reports: { market: 'No verified OHLCV. Do not infer precise prices.', news: 'Saved company announcement evidence.' },
    debate_history: { judge_decision: 'Research verdict remains available without a debate transcript.' },
    risk_judgment: 'Risk verdict remains available without a debate transcript.',
  },
} as DeepAnalysisResult

describe('analysis reading map', () => {
  it('selects saved evidence and opens the corresponding original section', async () => {
    const user = userEvent.setup()
    render(<AnalysisReader result={report} documentView={id => <div data-testid="original">{id}</div>} />)
    expect(screen.getByRole('button', { name: '情绪分析师' }).hasAttribute('disabled')).toBe(true)
    await user.click(screen.getByRole('button', { name: '新闻分析师' }))
    const reading = screen.getByRole('region', { name: '新闻分析师' })
    expect(within(reading).getByText('Saved company announcement evidence.')).toBeTruthy()
    expect(within(reading).queryByText(/No verified OHLCV/)).toBeNull()
    await user.click(within(reading).getByRole('button', { name: '查看对应原文' }))
    expect(screen.getByTestId('original').textContent).toBe('news')
  })

  it('retains isolated research/risk verdicts in both reading views', async () => {
    const user = userEvent.setup()
    const sections = buildAnalysisSections(report.raw_data)
    expect(sections.find(section => section.id === 'debate')?.markdown).toContain(report.raw_data.debate_history.judge_decision)
    expect(sections.find(section => section.id === 'risk')?.markdown).toContain(report.raw_data.risk_judgment)
    render(<AnalysisReader result={report} documentView={() => null} />)
    await user.click(screen.getByRole('button', { name: '研究主管裁决' }))
    expect(within(screen.getByRole('region', { name: '研究主管裁决' })).getByText(report.raw_data.debate_history.judge_decision)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '风控辩论与裁决' }))
    expect(within(screen.getByRole('region', { name: '风控辩论与裁决' })).getByText(report.raw_data.risk_judgment)).toBeTruthy()
  })

  it('preserves legacy content and translates controls without inventing analyst reports', async () => {
    await i18n.changeLanguage('en-US')
    render(<AnalysisReader result={{ content: 'Saved legacy text.' } as DeepAnalysisResult} documentView={() => null} />)
    expect(screen.getByRole('tab', { name: 'Decision map' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Technical analyst' }).hasAttribute('disabled')).toBe(true)
    expect(within(screen.getByRole('region', { name: 'PM final decision' })).getByText('Saved legacy text.')).toBeTruthy()
  })
})

describe('faithful report formatting', () => {
  it('uses the saved executive summary as an excerpt instead of the rating or a generated stance', () => {
    expect(analysisExcerpt(report.raw_data.final_decision)).toBe('Wait for verified data.')
    expect(analysisExcerpt('# Report\n\n**分析日期**: 2026-10-23\n\nNo verified data.')).toBe('No verified data.')
  })
  it('keeps Markdown tables and fenced headings intact while splitting report chapters', () => {
    const chapters = analysisChapters('## Evidence\n\n| Metric | Value |\n| --- | --- |\n| Price | N/A |\n\n```md\n## Literal heading\n```\n\n## Limits\n\nOriginal caveat.')
    expect(chapters.map(chapter => chapter.title)).toEqual(['Evidence', 'Limits'])
    expect(chapters[0].markdown).toContain('| Price | N/A |')
    expect(chapters[0].markdown).toContain('```md\n## Literal heading\n```')
    expect(chapters[1].markdown.trim()).toBe('Original caveat.')
  })
})

it('opens only clicked diagnostic details, supports text focus/Escape and restores the row focus', async () => {
  const user = userEvent.setup()
  render(<ToolkitDiagnostics defaultOpen summary={{ hit: 1, miss: 1, passthrough: 0, error: 0 }} recent={[
    { action: 'HIT', method: 'get_stock_data', symbol: '002594', snippet: 'No kline data available.' },
    { action: 'MISS', method: 'get_indicators', symbol: '002594', snippet: 'Indicator unavailable.' },
  ]} />)
  const first = screen.getByRole('button', { name: /get_stock_data/ })
  await user.hover(first)
  expect(screen.queryByText('No kline data available.')).toBeNull()
  await user.click(first)
  expect(screen.getByText('No kline data available.')).toBeTruthy()
  await user.click(screen.getByRole('button', { name: /get_indicators/ }))
  expect(screen.queryByText('No kline data available.')).toBeNull()
  const snippet = screen.getByText('Indicator unavailable.')
  snippet.focus()
  fireEvent.keyDown(snippet, { key: 'Escape' })
  expect(screen.queryByText('Indicator unavailable.')).toBeNull()
  expect(document.activeElement).toBe(screen.getByRole('button', { name: /get_indicators/ }))
})

it('consumes the first Escape inside a real parent Dialog before allowing the second to close it', async () => {
  const user = userEvent.setup()
  const onOpenChange = vi.fn()
  render(<DeepAnalysisModal open onOpenChange={onOpenChange} stockId={2} stockName="比亚迪" stockSymbol="002594" initialResult={{ ...report, raw_data: { ...report.raw_data, toolkit_diagnostic: {
    summary: { hit: 1, miss: 0, passthrough: 0, error: 0 }, recent: [{ action: 'HIT', method: 'get_stock_data', snippet: 'Original diagnostic detail.' }],
  } } }} />)
  await user.click(screen.getByText('数据注入诊断', { exact: true }))
  const trigger = screen.getByRole('button', { name: /get_stock_data/ })
  await user.click(trigger)
  screen.getByText('Original diagnostic detail.').focus()
  await user.keyboard('{Escape}')
  expect(screen.queryByText('Original diagnostic detail.')).toBeNull()
  expect(onOpenChange).not.toHaveBeenCalled()
  expect(document.activeElement).toBe(trigger)
  await user.keyboard('{Escape}')
  expect(onOpenChange).toHaveBeenCalledWith(false)
})
