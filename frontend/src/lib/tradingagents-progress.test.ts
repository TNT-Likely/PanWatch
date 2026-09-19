import { describe, expect, it } from 'vitest'
import {
  isTerminalProgressStatus,
  shouldContinueProgressWatch,
} from './tradingagents-progress'

describe('TradingAgents progress lifecycle', () => {
  it('only explicit backend terminal statuses stop a watch', () => {
    expect(isTerminalProgressStatus('success')).toBe(true)
    expect(isTerminalProgressStatus('failed')).toBe(true)
    expect(isTerminalProgressStatus('stale')).toBe(true)
    expect(isTerminalProgressStatus('not_found')).toBe(false)
    expect(isTerminalProgressStatus('running')).toBe(false)
  })

  it('keeps polling after a non-terminal SSE done or not_found', () => {
    expect(shouldContinueProgressWatch('not_found', 'done')).toBe(true)
    expect(shouldContinueProgressWatch('running', 'done')).toBe(true)
    expect(shouldContinueProgressWatch('timeout', 'done')).toBe(true)
    expect(shouldContinueProgressWatch('success', 'done')).toBe(false)
    expect(shouldContinueProgressWatch('failed', 'progress')).toBe(false)
  })
})
