import { useCallback, useEffect, useRef, useState } from 'react'

type ScrollMetrics = Pick<HTMLElement, 'scrollHeight' | 'scrollTop' | 'clientHeight'>

/** Whether a reader is close enough to the latest message to keep following it. */
export function isNearBottom(metrics: ScrollMetrics, threshold = 80): boolean {
  return metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight <= threshold
}

/** Follow layout changes until the reader deliberately scrolls up. */
export function useChatAutoScroll() {
  const boxRef = useRef<HTMLDivElement | null>(null)
  const followingRef = useRef(true)
  const metricsRef = useRef<ScrollMetrics | null>(null)
  const gestureRef = useRef<'up' | 'down' | null>(null)
  const cleanupRef = useRef<(() => void) | null>(null)
  const frameRef = useRef<number | null>(null)
  const [showScrollToBottom, setShowScrollToBottom] = useState(false)

  const remember = (box: HTMLDivElement) => {
    metricsRef.current = { scrollHeight: box.scrollHeight, scrollTop: box.scrollTop, clientHeight: box.clientHeight }
  }
  const followNewContent = useCallback(() => {
    const box = boxRef.current
    if (!box) return
    if (followingRef.current) box.scrollTop = box.scrollHeight
    remember(box)
    setShowScrollToBottom(!followingRef.current && !isNearBottom(box))
  }, [])
  const schedule = useCallback(() => {
    if (frameRef.current !== null) return
    frameRef.current = requestAnimationFrame(() => { frameRef.current = null; followNewContent() })
  }, [followNewContent])

  const handleScroll = useCallback(() => {
    const box = boxRef.current
    if (!box) return
    const previous = metricsRef.current
    const resized = previous && (previous.scrollHeight !== box.scrollHeight || previous.clientHeight !== box.clientHeight)
    const movedUp = previous && box.scrollTop < previous.scrollTop - 1
    const movedDown = previous && box.scrollTop > previous.scrollTop + 1
    // Content growth and browser scroll adjustments are not user intent.
    if (gestureRef.current === 'up' || (movedUp && !resized)) followingRef.current = false
    else if (isNearBottom(box) && (gestureRef.current === 'down' || movedDown)) followingRef.current = true
    gestureRef.current = null
    remember(box)
    setShowScrollToBottom(!followingRef.current && !isNearBottom(box))
  }, [])

  const scrollBoxRef = useCallback((box: HTMLDivElement | null) => {
    cleanupRef.current?.(); cleanupRef.current = null
    if (frameRef.current !== null) { cancelAnimationFrame(frameRef.current); frameRef.current = null }
    boxRef.current = box; metricsRef.current = null
    if (!box) return
    let touchY = 0
    const intent = (direction: 'up' | 'down') => {
      gestureRef.current = direction
      if (direction === 'up') followingRef.current = false
    }
    const wheel = (event: WheelEvent) => { if (event.deltaY) intent(event.deltaY < 0 ? 'up' : 'down') }
    const touchStart = (event: TouchEvent) => { touchY = event.touches[0]?.clientY ?? 0 }
    const touchMove = (event: TouchEvent) => {
      const next = event.touches[0]?.clientY ?? touchY
      if (next !== touchY) intent(next > touchY ? 'up' : 'down')
      touchY = next
    }
    const keyDown = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement)?.closest('input,textarea,[contenteditable=true]')) return
      if (['ArrowUp', 'PageUp', 'Home'].includes(event.key)) intent('up')
      if (['ArrowDown', 'PageDown', 'End'].includes(event.key)) intent('down')
    }
    box.addEventListener('wheel', wheel, { passive: true })
    box.addEventListener('touchstart', touchStart, { passive: true })
    box.addEventListener('touchmove', touchMove, { passive: true })
    box.addEventListener('keydown', keyDown)
    // Observe the fixed viewport and direct message/card sizes. Resize also
    // catches delayed Markdown/image layout and the task bar changing height.
    const resize = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(schedule)
    resize?.observe(box)
    Array.from(box.children).forEach(child => resize?.observe(child))
    const mutation = new MutationObserver(records => {
      for (const record of records) {
        if (record.target !== box) continue
        record.removedNodes.forEach(node => { if (node instanceof Element) resize?.unobserve(node) })
        record.addedNodes.forEach(node => { if (node instanceof Element) resize?.observe(node) })
      }
      schedule()
    })
    mutation.observe(box, { childList: true, characterData: true, subtree: true })
    schedule()
    cleanupRef.current = () => {
      resize?.disconnect(); mutation.disconnect()
      box.removeEventListener('wheel', wheel); box.removeEventListener('touchstart', touchStart)
      box.removeEventListener('touchmove', touchMove); box.removeEventListener('keydown', keyDown)
    }
  }, [schedule])

  useEffect(() => {
    // React StrictMode replays effects without reattaching the DOM ref.
    if (boxRef.current) scrollBoxRef(boxRef.current)
    return () => {
      cleanupRef.current?.()
      if (frameRef.current !== null) { cancelAnimationFrame(frameRef.current); frameRef.current = null }
    }
  }, [scrollBoxRef])

  const scrollToBottom = useCallback(() => {
    followingRef.current = true; gestureRef.current = null
    followNewContent()
  }, [followNewContent])
  const resetFollowing = useCallback(() => {
    followingRef.current = true; gestureRef.current = null
    setShowScrollToBottom(false)
    schedule()
  }, [schedule])

  return { scrollBoxRef, followNewContent, handleScroll, scrollToBottom, showScrollToBottom, resetFollowing }
}
