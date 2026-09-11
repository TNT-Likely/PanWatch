import { describe, expect, it } from 'vitest'

import { isNearBottom } from './useChatAutoScroll'

describe('isNearBottom', () => {
  it('keeps following when the viewport is within the 80px bottom tolerance', () => {
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 420, clientHeight: 500 })).toBe(true)
  })

  it('stops following after the user reads more than the tolerance above the bottom', () => {
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 300, clientHeight: 500 })).toBe(false)
  })
})
