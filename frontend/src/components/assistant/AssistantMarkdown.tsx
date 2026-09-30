import { memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const REMARK_PLUGINS = [remarkGfm]
const MARKDOWN_CLASS = 'prose prose-sm dark:prose-invert max-w-none overflow-x-auto [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0.5 [&_h1]:text-[15px] [&_h2]:text-[14px] [&_h3]:text-[13px] [&_table]:my-2 [&_table]:w-full [&_table]:border-collapse [&_table]:text-[12px] [&_th]:border [&_th]:border-border/60 [&_th]:bg-background/30 [&_th]:px-2 [&_th]:py-1.5 [&_th]:font-semibold [&_td]:border [&_td]:border-border/60 [&_td]:px-2 [&_td]:py-1.5 [&_td]:align-top'

export function safeStreamMarkdown(text: string): string {
  text = text.trimEnd()
  const fences = (text.match(/```/g) || []).length
  return fences % 2 === 1 ? `${text}\n\`\`\`` : text
}

/** Keep immutable history out of the live token and task-status render path. */
export const AssistantMarkdown = memo(function AssistantMarkdown({ content, streaming = false }: { content: string; streaming?: boolean }) {
  return (
    <div className={MARKDOWN_CLASS}>
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS}>{streaming ? safeStreamMarkdown(content) : content}</ReactMarkdown>
    </div>
  )
})
