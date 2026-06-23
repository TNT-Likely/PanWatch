import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const REPORT_PROSE =
  'prose prose-sm dark:prose-invert max-w-none break-words leading-relaxed ' +
  'prose-headings:scroll-mt-4 prose-headings:text-foreground prose-headings:font-semibold ' +
  'prose-h1:text-[18px] prose-h1:mt-0 prose-h1:mb-3 ' +
  'prose-h2:text-[16px] prose-h2:mt-5 prose-h2:mb-2 ' +
  'prose-h3:text-[14px] prose-h3:mt-4 prose-h3:mb-1.5 ' +
  'prose-p:my-2 prose-p:text-foreground/90 ' +
  'prose-li:my-0.5 prose-ul:my-2 prose-ol:my-2 ' +
  'prose-blockquote:border-l-primary/40 prose-blockquote:bg-accent/10 prose-blockquote:py-1 prose-blockquote:px-3 prose-blockquote:not-italic ' +
  'prose-hr:my-4 prose-hr:border-border/50 ' +
  'prose-table:my-3 prose-table:text-[12px] prose-th:px-2 prose-th:py-1.5 prose-td:px-2 prose-td:py-1.5 ' +
  'prose-strong:text-foreground prose-a:text-primary'

export function ReportMarkdown({
  content,
  className,
  emptyText = '暂无报告内容',
}: {
  content?: string | null
  className?: string
  emptyText?: string
}) {
  const text = String(content || '').trim()
  if (!text) {
    return <div className="text-[12px] text-muted-foreground">{emptyText}</div>
  }
  return (
    <div className={`${REPORT_PROSE} ${className || ''}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  )
}
