import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { DeepAnalysisResult } from '@panwatch/api'
import { AnalysisMetadata, AnalysisUsage, analysisDateForResult } from '@panwatch/biz-ui/components/analysis-metadata'

const report = { agent_name: 'tradingagents', title: 'Report', content: 'Report', analysis_date: '2026-10-01', generated_at: '2026-10-01T15:30:00+08:00', raw_data: { cost_usd: 0.0378 } } as DeepAnalysisResult

describe('analysis date and usage', () => {
  it('displays the historical report date and generated timestamp without exposing heuristic cost', () => {
    render(<AnalysisMetadata result={report} />)
    expect(screen.getByText('报告日期：2026-10-01')).toBeTruthy()
    expect(screen.getByText(/^生成时间：/)).toBeTruthy()
    expect(screen.getByText('Token 用量未记录')).toBeTruthy()
    expect(screen.queryByText(/0.0378/)).toBeNull()
  })
  it('does not invent today as the date of an undated report', () => {
    render(<AnalysisMetadata result={{ ...report, analysis_date: undefined, generated_at: undefined }} />)
    expect(screen.getByText('报告日期：未记录')).toBeTruthy()
    expect(analysisDateForResult({ ...report, analysis_date: undefined })).toBeUndefined()
  })
  it('shows actual usage and explicitly labels partial statistics', () => {
    render(<AnalysisUsage usage={{ input_tokens: 1200, output_tokens: 300, total_tokens: 1500, recorded_calls: 2, completed_calls: 3, complete: false }} />)
    expect(screen.getByText('Token 合计 1,500')).toBeTruthy()
    expect(screen.getByText('输入 1,200')).toBeTruthy()
    expect(screen.getByText('输出 300')).toBeTruthy()
    expect(screen.getByText('部分用量（2 / 3 次调用已记录）')).toBeTruthy()
  })
})
