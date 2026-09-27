import { memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/** GFM 表格样式(与 ChatWidget 消息体一致),收敛于此供全站 Markdown 容器复用。 */
const markdownTableStyles =
  '[&_table]:my-2 [&_table]:w-full [&_table]:border-collapse [&_table]:text-body-sm ' +
  '[&_th]:border [&_th]:border-border/60 [&_th]:bg-background/30 [&_th]:px-2 [&_th]:py-1.5 [&_th]:font-semibold ' +
  '[&_td]:border [&_td]:border-border/60 [&_td]:px-2 [&_td]:py-1.5 [&_td]:align-top'

function MarkdownViewImpl({ content, className = '' }: { content: string; className?: string }) {
  return (
    <div
      className={`prose prose-sm dark:prose-invert max-w-none break-words overflow-x-auto ${markdownTableStyles}${
        className ? ` ${className}` : ''
      }`}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  )
}

/** 全站 Markdown 渲染统一入口:GFM(表格/删除线/任务列表)+ 表格样式 + 宽表横向滚动。
 *  memo:弹窗/页签父组件状态流转时 content 不变则跳过 markdown 重解析。 */
const MarkdownView = memo(MarkdownViewImpl)
export default MarkdownView
