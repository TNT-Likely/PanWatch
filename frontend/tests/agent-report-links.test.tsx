import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AgentReportLinks } from '../src/components/AgentReportLinks'

afterEach(cleanup)
describe('Agent report entrypoints', () => {
  it.each([
    ['daily_report', 'reports', 'daily_report'],
    ['premarket_outlook', 'reports', 'premarket_outlook'],
    ['news_digest', 'reports', 'news_digest'],
    ['tradingagents', 'deep', undefined],
    ['intraday_monitor', 'suggestions', undefined],
  ])('opens the destination for %s without activating the surrounding card', (name, tab, reportTab) => {
    const open = vi.fn()
    const configure = vi.fn()
    render(<div onClick={configure}><AgentReportLinks agents={[{ agent_name: name }]} labels={[]}
      onOpen={open} /></div>)
    fireEvent.click(screen.getByRole('button', { name }))
    expect(open).toHaveBeenCalledWith(reportTab ? { tab, reportTab } : { tab })
    expect(configure).not.toHaveBeenCalled()
  })

  it('offers a report and analysis entrypoint when no agents are configured', () => {
    const open = vi.fn()
    render(<AgentReportLinks agents={[]} labels={[]} onOpen={open} />)
    fireEvent.click(screen.getByRole('button', { name: '查看报告 / 立即分析' }))
    expect(open).toHaveBeenCalledWith({ tab: 'reports' })
  })
})
