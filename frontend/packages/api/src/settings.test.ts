import { describe, expect, it } from 'vitest'
import { parseBoolSetting } from './settings'

describe('parseBoolSetting', () => {
  it('空值默认 false', () => {
    expect(parseBoolSetting(undefined, false)).toBe(false)
    expect(parseBoolSetting('', false)).toBe(false)
  })

  it('true 字符串解析为 true', () => {
    expect(parseBoolSetting('true')).toBe(true)
    expect(parseBoolSetting('TRUE')).toBe(true)
  })

  it('其他值解析为 false', () => {
    expect(parseBoolSetting('false')).toBe(false)
    expect(parseBoolSetting('1')).toBe(false)
  })
})
