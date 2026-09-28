import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { cn } from '@aiwatch/base-ui/cn'
import { Button } from '@aiwatch/base-ui/components/ui/button'

// 回归：tailwind-merge 默认不认识自定义字阶（text-body-sm 等），会把未知 text-* 误归入
// 文字颜色组，与 text-white 同串时按冲突删除后者 → size=sm 主按钮白字丢失（继承深色前景）。
// cn.ts 已通过 extendTailwindMerge 把字阶键注册进 font-size 组，这里断言两类共存、互不删除。

describe('cn 自定义字阶与文字颜色不冲突', () => {
  it('text-white 在前、text-body-sm 在后：两类都保留', () => {
    const merged = cn('text-white', 'text-body-sm')
    expect(merged).toContain('text-white')
    expect(merged).toContain('text-body-sm')
  })

  it('text-body-sm 在前、text-white 在后：两类都保留（顺序无关）', () => {
    const merged = cn('text-body-sm', 'text-white')
    expect(merged).toContain('text-body-sm')
    expect(merged).toContain('text-white')
  })

  it('字阶与颜色真同组冲突时仍按后者覆盖（保留 twMerge 去重语义）', () => {
    const merged = cn('text-white', 'text-foreground')
    expect(merged).not.toContain('text-white')
    expect(merged).toContain('text-foreground')
  })
})

describe('Button size=sm 主按钮类完整性', () => {
  it('默认变体同时携带 bg-primary / text-white / text-body-sm', () => {
    render(<Button size="sm">保存</Button>)
    const btn = screen.getByRole('button', { name: '保存' })
    const cls = btn.className
    expect(cls).toContain('bg-primary')
    expect(cls).toContain('text-white')
    expect(cls).toContain('text-body-sm')
  })
})
