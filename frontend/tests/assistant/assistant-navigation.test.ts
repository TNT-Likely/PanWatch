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

it.each([
  ['ENB.PR.V', 'TSX', 'ENB-PV'],
  ['BCE.PR.A', 'TSX', 'BCE-PA'],
  ['ENB.PR.V.TO', 'TSX', 'ENB-PV'],
  ['ZSP.U.TO', 'TSX', 'ZSP-U'],
  ['REI.UN', 'TSX', 'REI-UN'],
  ['TBRD.A.V', 'TSXV', 'TBRD-A'],
])('opens the assistant chart action for Canadian alias %s', (symbol, market, canonical) => {
  // Result-builder actions preserve provider/tool spelling in their public URL.
  const path = `/portfolio?view=kline&symbol=${symbol}&market=${market}`
  expect(parseAssistantPortfolioTarget(new URL(path, 'http://localhost').searchParams))
    .toEqual({ view: 'kline', symbol: canonical, market })
})

it.each([
  ['ENB.PR.V', 'TSXV'], ['ENB.PR.V', 'US'], ['TBRD.V', 'TSX'],
  ['ZSP.U.TO', 'TSXV'], ['ENB.PF.V', 'TSX'], ['.TO', 'TSX'],
])('rejects contradictory or unsupported Canadian alias %s/%s', (symbol, market) => {
  expect(parseAssistantPortfolioTarget(new URLSearchParams({ view: 'kline', symbol, market }))).toBeNull()
})
