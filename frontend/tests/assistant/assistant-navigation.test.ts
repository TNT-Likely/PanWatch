import { describe, expect, it } from 'vitest'

import { parseAssistantPortfolioTarget } from '@/lib/assistant-navigation'

describe('parseAssistantPortfolioTarget', () => {
  it('parses a validated chart target', () => {
    expect(parseAssistantPortfolioTarget(new URLSearchParams('view=kline&symbol=brk.b&market=us'))).toEqual({
      view: 'kline',
      symbol: 'BRK.B',
      market: 'US',
    })
  })

  it('rejects unsupported views, markets, and symbols', () => {
    expect(parseAssistantPortfolioTarget(new URLSearchParams('view=analysis&symbol=600519&market=CN'))).toBeNull()
    expect(parseAssistantPortfolioTarget(new URLSearchParams('view=kline&symbol=600519&market=XX'))).toBeNull()
    expect(parseAssistantPortfolioTarget(new URLSearchParams('view=kline&symbol=../bad&market=CN'))).toBeNull()
  })
})
