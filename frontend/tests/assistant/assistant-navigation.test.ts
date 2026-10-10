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

  it('accepts Canadian REITs and separates TSX from TSXV with canonical symbols', () => {
    expect(parseAssistantPortfolioTarget(new URLSearchParams('view=kline&symbol=rei-un.to&market=tsx')))
      .toEqual({ view: 'kline', symbol: 'REI-UN', market: 'TSX' })
    expect(parseAssistantPortfolioTarget(new URLSearchParams('view=kline&symbol=zsp&market=tsx')))
      .toEqual({ view: 'kline', symbol: 'ZSP', market: 'TSX' })
    expect(parseAssistantPortfolioTarget(new URLSearchParams('view=kline&symbol=tbrd.v&market=tsxv')))
      .toEqual({ view: 'kline', symbol: 'TBRD', market: 'TSXV' })
  })

  it('rejects unsupported views, markets, and symbols', () => {
    expect(parseAssistantPortfolioTarget(new URLSearchParams('view=kline&symbol=.TO&market=TSX'))).toBeNull()
    expect(parseAssistantPortfolioTarget(new URLSearchParams('view=kline&symbol=ZSP.TO&market=TSXV'))).toBeNull()
    expect(parseAssistantPortfolioTarget(new URLSearchParams('view=analysis&symbol=600519&market=CN'))).toBeNull()
    expect(parseAssistantPortfolioTarget(new URLSearchParams('view=kline&symbol=600519&market=XX'))).toBeNull()
    expect(parseAssistantPortfolioTarget(new URLSearchParams('view=kline&symbol=../bad&market=CN'))).toBeNull()
  })
})
