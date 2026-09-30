import { StrictMode } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { isNearBottom, useChatAutoScroll } from './useChatAutoScroll'

let api: ReturnType<typeof useChatAutoScroll>
let frames: Map<number, FrameRequestCallback>
let resizes: Array<{ callback: ResizeObserverCallback; disconnected: boolean }>
function Harness() {
  api = useChatAutoScroll()
  return <><div data-testid="messages" ref={api.scrollBoxRef} onScroll={api.handleScroll}><div>Message</div></div>{api.showScrollToBottom && <button onClick={api.scrollToBottom}>Latest</button>}</>
}
function metrics() {
  const box = screen.getByTestId('messages')
  const values = { scrollHeight: 1000, clientHeight: 400, scrollTop: 0 }
  Object.defineProperties(box, {
    scrollHeight: { configurable: true, get: () => values.scrollHeight },
    clientHeight: { configurable: true, get: () => values.clientHeight },
    scrollTop: { configurable: true, get: () => values.scrollTop, set: value => { values.scrollTop = Math.max(0, Math.min(value, values.scrollHeight - values.clientHeight)) } },
  })
  act(() => api.followNewContent())
  return { box, values }
}
function resize() {
  act(() => {
    resizes.filter(row => !row.disconnected).forEach(row => row.callback([], {} as ResizeObserver))
    const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(0))
  })
}
beforeEach(() => {
  frames = new Map(); resizes = []
  let index = 0
  vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => { frames.set(++index, callback); return index }))
  vi.stubGlobal('cancelAnimationFrame', vi.fn((id: number) => frames.delete(id)))
  vi.stubGlobal('ResizeObserver', class {
    row: { callback: ResizeObserverCallback; disconnected: boolean }
    constructor(callback: ResizeObserverCallback) { this.row = { callback, disconnected: false }; resizes.push(this.row) }
    observe() {}; unobserve() {}; disconnect() { this.row.disconnected = true }
  })
})
afterEach(() => vi.unstubAllGlobals())

describe('chat scroll following', () => {
  it('recognizes the bottom tolerance', () => {
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 420, clientHeight: 500 })).toBe(true)
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 300, clientHeight: 500 })).toBe(false)
  })

  it('does not treat a large streaming/layout gap as user scrolling', () => {
    render(<Harness />); const { box, values } = metrics()
    expect(values.scrollTop).toBe(600)
    values.scrollHeight = 1500
    fireEvent.scroll(box)
    act(() => api.followNewContent())
    expect(values.scrollTop).toBe(1100)
    expect(screen.queryByText('Latest')).toBeNull()
  })

  it('follows delayed content layout and viewport resizing', () => {
    render(<Harness />); const { values } = metrics()
    values.scrollHeight = 1700; resize()
    expect(values.scrollTop).toBe(1300)
    values.clientHeight = 300; resize()
    expect(values.scrollTop).toBe(1400)
  })

  it('pauses when the reader scrolls up and resumes with the latest button', () => {
    render(<Harness />); const { box, values } = metrics()
    fireEvent.wheel(box, { deltaY: -200 }); box.scrollTop = 250; fireEvent.scroll(box)
    expect(screen.getByText('Latest')).toBeTruthy()
    values.scrollHeight = 1800; resize()
    expect(values.scrollTop).toBe(250)
    fireEvent.click(screen.getByText('Latest'))
    expect(values.scrollTop).toBe(1400)
    values.scrollHeight = 1900; resize()
    expect(values.scrollTop).toBe(1500)
  })

  it('detects scrollbar scrolling without mistaking new content for a gesture', () => {
    render(<Harness />); const { box, values } = metrics()
    box.scrollTop = 200; fireEvent.scroll(box)
    values.scrollHeight = 1600; resize()
    expect(values.scrollTop).toBe(200)
    box.scrollTop = 1200; fireEvent.scroll(box)
    values.scrollHeight = 1800; resize()
    expect(values.scrollTop).toBe(1400)
  })

  it('handles touch and keyboard reading without taking control back', () => {
    render(<Harness />); const { box, values } = metrics()
    fireEvent.touchStart(box, { touches: [{ clientY: 100 }] })
    fireEvent.touchMove(box, { touches: [{ clientY: 200 }] })
    box.scrollTop = 100; fireEvent.scroll(box)
    values.scrollHeight = 1500; resize()
    expect(values.scrollTop).toBe(100)
    act(() => api.scrollToBottom())
    fireEvent.keyDown(box, { key: 'PageUp' }); box.scrollTop = 200; fireEvent.scroll(box)
    values.scrollHeight = 1700; resize()
    expect(values.scrollTop).toBe(200)
    act(() => api.resetFollowing()); resize()
    expect(values.scrollTop).toBe(1300)
  })

  it('reconnects observers during StrictMode replay and cleans up on unmount', () => {
    const view = render(<StrictMode><Harness /></StrictMode>); const { values } = metrics()
    values.scrollHeight = 1800; resize()
    expect(values.scrollTop).toBe(1400)
    expect(resizes.some(row => !row.disconnected)).toBe(true)
    view.unmount()
    expect(resizes.every(row => row.disconnected)).toBe(true)
    expect(frames.size).toBe(0)
  })
})
