import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import MarkdownView from '@aiwatch/biz-ui/components/markdown-view'

/** 与生产同构的 GFM 管道表样例:表头 + 对齐分隔行 + 数据行。 */
const GFM_TABLE = ['| 指标 | 数值 |', '| --- | ---: |', '| 涨幅 | 3.2% |', '| 回撤 | -1.8% |'].join('\n')

/**
 * 回归守护:此前全站 5 处 ReactMarkdown 缺 remark-gfm,GFM 表格退化为竖排纯文本。
 * 这里测的是本仓渲染契约(MarkdownView 必须开启 GFM 并带表格样式/横向滚动),
 * 而非三方库本身——若有人移除 remarkGfm 或容器样式类,本测试即失败。
 */
describe('MarkdownView(全站 Markdown 渲染入口)', () => {
  it('GFM 表格语法渲染为 <table>,而非退化为竖排纯文本', () => {
    const { container } = render(<MarkdownView content={GFM_TABLE} />)
    const table = container.querySelector('table')
    expect(table).not.toBeNull()
    expect(table!.querySelectorAll('thead th')).toHaveLength(2)
    expect(table!.querySelectorAll('tbody td')).toHaveLength(4)
    expect(table!.textContent).toContain('3.2%')
  })

  it('容器携带表格样式契约([&_th]/[&_td] 边框)与 overflow-x-auto(宽表可横向滚动)', () => {
    const { container } = render(<MarkdownView content={GFM_TABLE} />)
    const wrapper = container.firstElementChild as HTMLElement
    expect(wrapper.className).toContain('overflow-x-auto')
    expect(wrapper.className).toContain('[&_table]:w-full')
    expect(wrapper.className).toContain('[&_th]:border')
    expect(wrapper.className).toContain('[&_td]:border')
  })

  it('普通段落照常渲染,空内容不崩溃', () => {
    const r1 = render(<MarkdownView content="普通 **加粗** 段落" />)
    expect(r1.container.querySelector('p')!.innerHTML).toContain('<strong>加粗</strong>')
    const r2 = render(<MarkdownView content="" />)
    expect(r2.container.firstElementChild).not.toBeNull()
  })
})
