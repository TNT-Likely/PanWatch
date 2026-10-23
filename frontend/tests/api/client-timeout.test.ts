import { afterEach, expect, it, vi } from 'vitest'
import { fetchAPI } from '../../packages/api/src/client'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

function untilAborted(signal: AbortSignal) {
  return new Promise<never>((_, reject) => {
    const abort = () => reject(new DOMException('Cancelled', 'AbortError'))
    if (signal.aborted) abort()
    else signal.addEventListener('abort', abort, { once: true })
  })
}

it('enforces the deadline when the caller also supplies a cancellation signal', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn((_url, options) => untilAborted(options.signal)))
  const assertion = expect(fetchAPI('/slow', { signal: new AbortController().signal, timeoutMs: 10 })).rejects.toThrow(/超时|timed out/)
  await vi.advanceTimersByTimeAsync(10)
  await assertion
  expect(vi.getTimerCount()).toBe(0)
})

it('keeps the deadline active while reading the response body', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn(async (_url, options) => ({ status: 200, json: () => untilAborted(options.signal) })))
  const assertion = expect(fetchAPI('/slow-body', { timeoutMs: 10 })).rejects.toThrow(/超时|timed out/)
  await vi.advanceTimersByTimeAsync(10)
  await assertion
  expect(vi.getTimerCount()).toBe(0)
})

it('preserves intentional cancellation and removes its deadline', async () => {
  vi.useFakeTimers()
  const controller = new AbortController()
  vi.stubGlobal('fetch', vi.fn((_url, options) => untilAborted(options.signal)))
  const assertion = expect(fetchAPI('/cancel', { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' })
  controller.abort()
  await assertion
  expect(vi.getTimerCount()).toBe(0)
})
