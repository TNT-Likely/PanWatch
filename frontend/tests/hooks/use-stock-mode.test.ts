import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'

const fetchAPIMock = vi.hoisted(() => vi.fn())
const isAuthenticatedMock = vi.hoisted(() => vi.fn(() => true))
vi.mock('@panwatch/api', () => ({
  fetchAPI: fetchAPIMock,
  isAuthenticated: isAuthenticatedMock,
}))

const { useStockColorMode, setStockColorMode, initStockModeFromBackend } = await import('@panwatch/base-ui/hooks/use-stock-mode')

describe('use-stock-mode', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('data-stock-mode')
    fetchAPIMock.mockReset()
    fetchAPIMock.mockResolvedValue(undefined)
    isAuthenticatedMock.mockClear()
    isAuthenticatedMock.mockReturnValue(true)
    // 重置模块级状态：切回默认（须在 mock 配置之后——setStockColorMode 会触发 PUT）
    setStockColorMode('up-red')
    fetchAPIMock.mockClear()
  })

  it('默认口径为 up-red 且不设置 dataset（hook 经 renderHook 调用）', () => {
    const { result } = renderHook(() => useStockColorMode())
    expect(result.current).toBe('up-red')
    expect(document.documentElement.dataset.stockMode).toBeUndefined()
  })

  it('setStockColorMode 写 localStorage、应用 dataset、通知订阅者', () => {
    const { result } = renderHook(() => useStockColorMode())
    act(() => setStockColorMode('up-green'))
    expect(result.current).toBe('up-green')
    expect(localStorage.getItem('panwatch-stock-mode')).toBe('up-green')
    expect(document.documentElement.dataset.stockMode).toBe('up-green')
    act(() => setStockColorMode('up-red'))
    expect(result.current).toBe('up-red')
    expect(localStorage.getItem('panwatch-stock-mode')).toBe('up-red')
    expect(document.documentElement.dataset.stockMode).toBeUndefined()
  })

  it('后端同步调用 PUT /settings/price_color_mode', () => {
    fetchAPIMock.mockClear()
    setStockColorMode('up-green')
    expect(fetchAPIMock).toHaveBeenCalledWith(
      '/settings/price_color_mode',
      expect.objectContaining({ method: 'PUT' }),
    )
  })

  it('initStockModeFromBackend：本地有显式值时不回落', async () => {
    localStorage.setItem('panwatch-stock-mode', 'up-red')
    await initStockModeFromBackend()
    expect(fetchAPIMock).not.toHaveBeenCalled()
  })

  it('initStockModeFromBackend：未登录不发起请求（防 401 误登出）', async () => {
    localStorage.removeItem('panwatch-stock-mode')
    isAuthenticatedMock.mockReturnValue(false)
    await initStockModeFromBackend()
    expect(fetchAPIMock).not.toHaveBeenCalled()
  })

  it('initStockModeFromBackend：后端 up-green 时应用口径', async () => {
    localStorage.removeItem('panwatch-stock-mode') // 模拟本地无显式设置（beforeEach 重置已写入 up-red）
    fetchAPIMock.mockResolvedValue([{ key: 'price_color_mode', value: 'up-green' }])
    const { result } = renderHook(() => useStockColorMode())
    await act(async () => initStockModeFromBackend())
    expect(result.current).toBe('up-green')
    expect(document.documentElement.dataset.stockMode).toBe('up-green')
  })

  it('initStockModeFromBackend：后端非法值忽略', async () => {
    localStorage.removeItem('panwatch-stock-mode')
    fetchAPIMock.mockResolvedValue([{ key: 'price_color_mode', value: 'bogus' }])
    await initStockModeFromBackend()
    expect(document.documentElement.dataset.stockMode).toBeUndefined()
  })
})

describe('use-stock-mode · 模块初始化', () => {
  it('模块初始化时同步 localStorage 已有口径到 React 状态（Select 联动）', async () => {
    vi.resetModules()
    localStorage.setItem('panwatch-stock-mode', 'up-green')
    document.documentElement.removeAttribute('data-stock-mode')
    const mod = await import('@panwatch/base-ui/hooks/use-stock-mode')
    const { result } = renderHook(() => mod.useStockColorMode())
    expect(result.current).toBe('up-green')
  })
})
