import { useCallback, useRef, useState } from 'react'

type ScrollMetrics = Pick<HTMLElement, 'scrollHeight' | 'scrollTop' | 'clientHeight'>

/** Whether a reader is close enough to the latest message to keep following it. */
export function isNearBottom(metrics: ScrollMetrics, threshold = 80): boolean {
  return metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight <= threshold
}

/**
 * Follow incoming chat content until the reader explicitly scrolls away.
 *
 * Streaming uses an immediate scroll rather than repeated smooth animations:
 * smooth-scroll intermediate positions fire the same scroll handler as a user
 * gesture and can incorrectly disable following while content is still growing.
 */
export function useChatAutoScroll() {
  const scrollBoxRef = useRef<HTMLDivElement>(null)
  const followingRef = useRef(true)
  const [showScrollToBottom, setShowScrollToBottom] = useState(false)

  const followNewContent = useCallback(() => {
    const box = scrollBoxRef.current
    if (box && followingRef.current) {
      box.scrollTop = box.scrollHeight
    }
  }, [])

  const handleScroll = useCallback(() => {
    const box = scrollBoxRef.current
    if (!box) return
    const nearBottom = isNearBottom(box)
    followingRef.current = nearBottom
    setShowScrollToBottom(!nearBottom)
  }, [])

  const scrollToBottom = useCallback(() => {
    const box = scrollBoxRef.current
    followingRef.current = true
    setShowScrollToBottom(false)
    if (box) {
      box.scrollTop = box.scrollHeight
    }
  }, [])

  const resetFollowing = useCallback(() => {
    followingRef.current = true
    setShowScrollToBottom(false)
  }, [])

  return {
    scrollBoxRef,
    followNewContent,
    handleScroll,
    scrollToBottom,
    showScrollToBottom,
    resetFollowing,
  }
}
