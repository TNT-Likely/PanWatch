import { describe, expect, it } from 'vitest'
import { buildKlineSuggestion } from '@/lib/kline-scorer'
import { localizeAgentDescription, localizeAgentName } from '@/i18n/agent-labels'
import { mapLoggerName } from '@/lib/logger-map'
import { bizUi as enBizUi } from '@/i18n/locales/en-US/biz-ui'
import { configuration as enConfiguration } from '@/i18n/locales/en-US/configuration'

function translator(resource: unknown) {
  return (key: string) => {
    let value: unknown = resource
    for (const part of key.split('.')) value = (value as Record<string, unknown>)[part]
    return String(value)
  }
}

describe('dynamic interface labels', () => {
  it('builds local technical signals in the active language', () => {
    const tr = translator(enBizUi.kline)
    const result = buildKlineSuggestion({
      trend: '多头排列',
      macd_status: '金叉',
      macd_hist: 0.2,
      rsi_status: '偏强',
    }, false, tr)

    expect(result.action).toBe('buy')
    expect(result.action_label).toBe('Buy')
    expect(result.signal).toContain('Bullish MAs')
    expect(result.evidence.every(item => !/[\u4e00-\u9fff]/.test(item.text))).toBe(true)
  })

  it('localizes known Agent catalog fields while preserving custom Agents', () => {
    const tr = translator(enConfiguration)
    expect(localizeAgentName('daily_report', '收盘复盘', tr)).toBe('Closing review')
    expect(localizeAgentDescription('daily_report', '旧说明', tr)).toContain('daily closing report')
    expect(localizeAgentName('custom_agent', 'My Agent', tr)).toBe('My Agent')
  })

  it('localizes logger names from stable module identifiers', () => {
    expect(mapLoggerName('src.agents.daily_report.worker', 'en-US')).toBe('Closing review')
    expect(mapLoggerName('src.core.scheduler', 'zh-CN')).toBe('调度器')
  })
})
