import { describe, expect, it } from 'vitest'
import { buildStockUrl } from './stock-link'

describe('buildStockUrl', () => {
  it('CN 深圳股票生成雪球链接', () => {
    expect(buildStockUrl('002837', 'CN')).toBe('https://xueqiu.com/S/SZ002837')
  })

  it('CN 上海股票生成雪球链接', () => {
    expect(buildStockUrl('600519', 'CN')).toBe('https://xueqiu.com/S/SH600519')
  })

  it('CN 北交所股票生成雪球链接', () => {
    expect(buildStockUrl('830799', 'CN')).toBe('https://xueqiu.com/S/BJ830799')
  })

  it('US 美股生成雪球链接', () => {
    expect(buildStockUrl('AAPL', 'US')).toBe('https://xueqiu.com/S/AAPL')
  })

  it('HK 港股生成雪球链接', () => {
    expect(buildStockUrl('00883', 'HK')).toBe('https://xueqiu.com/S/00883')
  })

  it('市场代码不区分大小写', () => {
    expect(buildStockUrl('002837', 'cn')).toBe('https://xueqiu.com/S/SZ002837')
  })
})
