import { vi } from 'vitest'

/** jsdom 未实现 matchMedia;给需要渲染 AccountMenu/use-theme 等组件的测试安装最小桩。 */
export function installMatchMediaMock() {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia
}
