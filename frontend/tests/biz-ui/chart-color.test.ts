import { describe, expect, it } from 'vitest'
import { toLwColor } from '@aiwatch/biz-ui/lib/chart-color'

describe('toLwColor', () => {
  it('现代空格语法 hsl + alpha（暖纸主题崩溃格式的回归用例）', () => {
    // hsl(28 18% 12% / 0.85) 是压垮 lightweight-charts 的原始输入
    expect(toLwColor('hsl(28 18% 12% / 0.85)')).toBe('rgba(36, 30, 25, 0.85)')
  })

  it('现代空格语法 hsl 无 alpha 默认为 1', () => {
    expect(toLwColor('hsl(28 18% 12%)')).toBe('rgba(36, 30, 25, 1)')
  })

  it('legacy 逗号语法 hsl', () => {
    expect(toLwColor('hsl(28, 18%, 12%, 0.85)')).toBe('rgba(36, 30, 25, 0.85)')
    expect(toLwColor('hsl(120, 50%, 50%)')).toBe('rgba(64, 191, 64, 1)')
  })

  it('alpha 支持百分比', () => {
    expect(toLwColor('hsl(28 18% 12% / 85%)')).toBe('rgba(36, 30, 25, 0.85)')
  })

  it('s/l 越界值收敛到 0-100', () => {
    // s=150% l=-20% → 收敛后 s=100% l=0 → 黑色
    expect(toLwColor('hsl(0 150% -20%)')).toBe('rgba(0, 0, 0, 1)')
  })

  it('非 hsl 颜色原样返回（rgba/十六进制）', () => {
    expect(toLwColor('rgba(148, 163, 184, 0.12)')).toBe('rgba(148, 163, 184, 0.12)')
    expect(toLwColor('#f5f0e8')).toBe('#f5f0e8')
    expect(toLwColor('')).toBe('')
  })

  it('负色相与负 s/l 正确换算', () => {
    // hsl(-28 …) 等价 hsl(332 …)：g/b 相对 h=28 交换
    expect(toLwColor('hsl(-28 18% 12%)')).toBe('rgba(36, 25, 30, 1)')
    // s=150% l=-20% → 收敛后 s=100% l=0 → 黑色
    expect(toLwColor('hsl(0 150% -20%)')).toBe('rgba(0, 0, 0, 1)')
  })

  it('纯色基准：hsl(0 100% 50%) → 红色', () => {
    expect(toLwColor('hsl(0 100% 50%)')).toBe('rgba(255, 0, 0, 1)')
    expect(toLwColor('hsl(240 100% 50%)')).toBe('rgba(0, 0, 255, 1)')
  })
})
