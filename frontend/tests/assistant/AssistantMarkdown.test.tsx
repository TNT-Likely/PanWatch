import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ReactMarkdown from 'react-markdown'
import { AssistantMarkdown, safeStreamMarkdown } from '@/components/assistant/AssistantMarkdown'

vi.mock('react-markdown', () => ({ default: vi.fn(({ children }: { children: string }) => <div data-testid="parsed-markdown">{children}</div>) }))
beforeEach(() => vi.clearAllMocks())

describe('assistant Markdown render isolation', () => {
  it('does not parse unchanged history again during parent updates', () => {
    const { rerender } = render(<AssistantMarkdown content="已有研究回答" />)
    const calls = vi.mocked(ReactMarkdown).mock.calls.length
    rerender(<AssistantMarkdown content="已有研究回答" />)
    rerender(<AssistantMarkdown content="已有研究回答" />)
    expect(ReactMarkdown).toHaveBeenCalledTimes(calls)
    rerender(<AssistantMarkdown content="更新后的研究回答" />)
    expect(ReactMarkdown).toHaveBeenCalledTimes(calls + 1)
    expect(screen.getByTestId('parsed-markdown').textContent).toBe('更新后的研究回答')
  })

  it('repairs incomplete fences only while streaming and keeps the plugin list stable', () => {
    const content = '```python\nprint(1)'
    const { rerender } = render(<AssistantMarkdown content={content} streaming />)
    expect(screen.getByTestId('parsed-markdown').textContent).toBe(content + '\n```')
    const plugins = vi.mocked(ReactMarkdown).mock.calls.at(-1)![0].remarkPlugins
    rerender(<AssistantMarkdown content={content} />)
    expect(screen.getByTestId('parsed-markdown').textContent).toBe(content)
    expect(vi.mocked(ReactMarkdown).mock.calls.at(-1)![0].remarkPlugins).toBe(plugins)
  })

  it('preserves closed fences and ordinary Markdown', () => {
    expect(safeStreamMarkdown('```js\nconst value = 1\n```')).toBe('```js\nconst value = 1\n```')
    expect(safeStreamMarkdown('**研究结论**')).toBe('**研究结论**')
  })

  it('holds trailing blank tokens out of the streaming preview and preserves completed content', () => {
    const text = '```text\n已有内容' + '\n'.repeat(50)
    expect(safeStreamMarkdown(text)).toBe('```text\n已有内容\n```')
    const { rerender } = render(<AssistantMarkdown content={text} streaming />)
    expect(screen.getByTestId('parsed-markdown').textContent).toBe('```text\n已有内容\n```')
    rerender(<AssistantMarkdown content={text} />)
    expect(screen.getByTestId('parsed-markdown').textContent).toBe(text)
  })
})
