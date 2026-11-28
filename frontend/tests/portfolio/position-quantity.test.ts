import { describe, expect, it } from 'vitest'
import {
  formatQuantity,
  parseQuantity,
  quantityDecimals,
} from '../../src/lib/position-quantity'

describe('position quantity input rules', () => {
  it('accepts quantities below one', () => {
    expect(parseQuantity('0.5')).toBe(0.5)
    expect(parseQuantity(' 0.25 ')).toBe(0.25)
    expect(parseQuantity('0.00000001')).toBe(0.00000001)
  })

  it('rejects empty, non-positive and over-precise input', () => {
    expect(parseQuantity('')).toBeNull()
    expect(parseQuantity('   ')).toBeNull()
    expect(parseQuantity('0')).toBeNull()
    expect(parseQuantity('-1')).toBeNull()
    expect(parseQuantity('abc')).toBeNull()
    expect(parseQuantity('0.000000001')).toBeNull()
    expect(parseQuantity('1.234567891')).toBeNull()
  })

  it('counts decimals of exponential notation', () => {
    expect(quantityDecimals(0.5)).toBe(1)
    expect(quantityDecimals(100)).toBe(0)
    expect(quantityDecimals(0.00000001)).toBe(8)
    expect(quantityDecimals(0.000000001)).toBe(9)
  })

  it('formats quantities without exponent notation', () => {
    expect(formatQuantity(100)).toBe('100')
    expect(formatQuantity(0.5)).toBe('0.5')
    expect(formatQuantity(0.00000001)).toBe('0.00000001')
    expect(formatQuantity(1.5)).toBe('1.5')
    expect(formatQuantity(null)).toBe('—')
  })
})
