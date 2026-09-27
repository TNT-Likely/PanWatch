import { renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SLOW_LANE_FALLBACK_MS, SLOW_LANE_IDLE_TIMEOUT_MS, useSlowLaneScheduler } from './useSlowLaneScheduler'

describe('useSlowLaneScheduler', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  /** stub 一个"主线程立即空闲"的 requestIdleCallback */
  const stubIdleRIC = () =>
    vi.stubGlobal('requestIdleCallback', vi.fn((cb: () => void) => {
      queueMicrotask(cb)
      return 1
    }))
  /** 显式声明环境不支持 requestIdleCallback */
  const stubNoRIC = () => vi.stubGlobal('requestIdleCallback', undefined)

  it('armed 为 false 时不调度', () => {
    const onFire = vi.fn()
    stubIdleRIC()
    renderHook(() => useSlowLaneScheduler(false, onFire))
    vi.advanceTimersByTime(SLOW_LANE_FALLBACK_MS * 5)
    expect(onFire).not.toHaveBeenCalled()
  })

  it('兜底到点后经 requestIdleCallback 在空闲期执行', async () => {
    const onFire = vi.fn()
    const ric = vi.fn((cb: () => void) => {
      queueMicrotask(cb)
      return 1
    })
    vi.stubGlobal('requestIdleCallback', ric)
    const { rerender } = renderHook(({ armed }: { armed: boolean }) => useSlowLaneScheduler(armed, onFire), {
      initialProps: { armed: false },
    })
    rerender({ armed: true })
    vi.advanceTimersByTime(SLOW_LANE_FALLBACK_MS - 1)
    expect(onFire).not.toHaveBeenCalled()
    expect(ric).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    await Promise.resolve()
    expect(ric).toHaveBeenCalledTimes(1)
    expect(onFire).toHaveBeenCalledTimes(1)
  })

  it('requestIdleCallback 不可用时走 setTimeout 兜底', () => {
    const onFire = vi.fn()
    stubNoRIC()
    renderHook(() => useSlowLaneScheduler(true, onFire))
    vi.advanceTimersByTime(SLOW_LANE_FALLBACK_MS)
    expect(onFire).toHaveBeenCalledTimes(1)
  })

  it('兜底到点后主线程繁忙时,由 ric timeout 上限强制执行', () => {
    const onFire = vi.fn()
    // ric 不回调(主线程持续繁忙),仅到期强制执行
    vi.stubGlobal('requestIdleCallback', vi.fn((_cb: () => void, opts?: { timeout?: number }) => {
      setTimeout(() => _cb(), opts?.timeout ?? 0)
      return 1
    }))
    renderHook(() => useSlowLaneScheduler(true, onFire))
    vi.advanceTimersByTime(SLOW_LANE_FALLBACK_MS)
    expect(onFire).not.toHaveBeenCalled()
    vi.advanceTimersByTime(SLOW_LANE_IDLE_TIMEOUT_MS)
    expect(onFire).toHaveBeenCalledTimes(1)
  })

  it('卸载后清理两路定时,不再触发', () => {
    const onFire = vi.fn()
    stubIdleRIC()
    const { unmount } = renderHook(() => useSlowLaneScheduler(true, onFire))
    unmount()
    vi.advanceTimersByTime(SLOW_LANE_FALLBACK_MS * 5)
    expect(onFire).not.toHaveBeenCalled()
  })

  it('每次 armed 会话至多触发一次;armed 翻转后重新调度', async () => {
    const onFire = vi.fn()
    stubIdleRIC()
    const { rerender } = renderHook(({ armed }: { armed: boolean }) => useSlowLaneScheduler(armed, onFire), {
      initialProps: { armed: true },
    })
    vi.advanceTimersByTime(SLOW_LANE_FALLBACK_MS)
    await Promise.resolve()
    expect(onFire).toHaveBeenCalledTimes(1)
    // armed 抖动(false→true)不重置已触发会话
    rerender({ armed: false })
    rerender({ armed: true })
    vi.advanceTimersByTime(SLOW_LANE_FALLBACK_MS * 5)
    expect(onFire).toHaveBeenCalledTimes(1)
  })

  it('兜底计时器到点前卸载,idle 回调同样被清理', () => {
    const onFire = vi.fn()
    let storedCb: () => void = () => {}
    vi.stubGlobal('requestIdleCallback', vi.fn((cb: () => void) => {
      storedCb = cb
      return 7
    }))
    const cancelIdleCallback = vi.fn()
    vi.stubGlobal('cancelIdleCallback', cancelIdleCallback)
    const { unmount } = renderHook(() => useSlowLaneScheduler(true, onFire))
    vi.advanceTimersByTime(SLOW_LANE_FALLBACK_MS)
    unmount()
    storedCb()
    expect(onFire).not.toHaveBeenCalled()
    expect(cancelIdleCallback).toHaveBeenCalledWith(7)
  })
})
