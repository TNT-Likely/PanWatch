import { useEffect, useRef } from 'react'

/** 首屏快车道完成后的兜底触发延迟(ms):保证慢车道最迟此刻进入调度 */
export const SLOW_LANE_FALLBACK_MS = 800
/** 兜底到点后 requestIdleCallback 的强制执行上限(ms):防主线程长期繁忙饿死慢车道 */
export const SLOW_LANE_IDLE_TIMEOUT_MS = 800

/**
 * 次屏车道调度:armed(首屏快车道完成)后,先挂 SLOW_LANE_FALLBACK_MS 兜底计时器,
 * 到点后用 requestIdleCallback 等主线程空闲再执行 onFire(不支持则直接执行)。
 *
 * 为什么不在 armed 后立即 requestIdleCallback:networkidle 判定需要 500ms 网络静默窗口,
 * 首屏渲染完成后主线程往往已经空闲(ric 会立刻触发),慢请求若在静默窗口形成前发出,
 * 会把 goto 的 networkidle 拖到最慢接口(如 AI 策展 10s+)完成之后——兜底延迟先行,
 * ric 只负责兜底到点后的"空闲执行",两条路径都不早于兜底时刻。
 *
 * 清理:armed 翻转/组件卸载时同时取消计时器与 idle 回调;onFire 幂等(每次 armed 会话至多一次)。
 */
export function useSlowLaneScheduler(armed: boolean, onFire: () => void): void {
  const firedRef = useRef(false)
  const onFireRef = useRef(onFire)
  onFireRef.current = onFire

  useEffect(() => {
    if (!armed || firedRef.current) return
    let cancelled = false
    let idleId = 0
    let fallback: ReturnType<typeof setTimeout> | undefined

    const cleanup = () => {
      if (fallback !== undefined) {
        clearTimeout(fallback)
        fallback = undefined
      }
      // requestIdleCallback 与 cancelIdleCallback 在浏览器成对存在;jsdom 等测试环境可能只 stub 前者
      if (idleId && typeof window.cancelIdleCallback === 'function') {
        window.cancelIdleCallback(idleId)
        idleId = 0
      }
    }

    const kick = () => {
      if (cancelled || firedRef.current) return
      firedRef.current = true
      cleanup()
      onFireRef.current()
    }

    fallback = setTimeout(() => {
      fallback = undefined
      if (typeof window.requestIdleCallback === 'function') {
        idleId = window.requestIdleCallback(kick, { timeout: SLOW_LANE_IDLE_TIMEOUT_MS })
      } else {
        kick()
      }
    }, SLOW_LANE_FALLBACK_MS)

    return () => {
      cancelled = true
      cleanup()
    }
  }, [armed])
}
