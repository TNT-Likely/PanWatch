import { describe, expect, it } from 'vitest'
import i18n from '@/i18n'
import { notificationTitle } from '@/lib/notifications'

describe('notification titles', () => {
  const translate = (key: string) => String(i18n.t(`configuration:${key}`))
  it('includes the stock name and code for Agent reports in both languages', async () => {
    const item = { source: 'agent' as const, title: 'tradingagents', template_params: { agent_name: 'tradingagents', stock_name: '广汽集团', stock_symbol: '601238' } }
    expect(notificationTitle(item, translate)).toContain('广汽集团 (601238)')
    await i18n.changeLanguage('en-US')
    expect(notificationTitle(item, translate)).toMatch(/TradingAgents.*广汽集团 \(601238\)/)
  })
  it('keeps the symbol if the stock name is unavailable and leaves custom titles intact', () => {
    expect(notificationTitle({ source: 'agent', title: 'tradingagents', template_params: { stock_symbol: 'BRK-B' } }, translate)).toContain(' · BRK-B')
    expect(notificationTitle({ source: 'agent', title: 'Custom report', template_params: {} }, translate)).toBe('Custom report')
    expect(notificationTitle({ source: 'assistant', title: '广汽集团基本面分析', template_params: {} }, translate)).toBe('广汽集团基本面分析')
  })
})
